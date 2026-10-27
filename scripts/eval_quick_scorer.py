#!/usr/bin/env python3
"""Read-only before/after evaluation of the Pass-1 quick scorer (src/quick_scorer.py).

Scores every row of data/jobs.db IN MEMORY with
  (a) the scorer committed at a git ref (default HEAD) — "old"
  (b) the working-tree src/quick_scorer.py (or --new PATH) — "new"
and prints: distinct-score counts, top-band size, bucket histogram, per-panel counts,
precision of the >=90 band on a hand-labeled sample, recall of relevant roles, the named
false positives, and notable rows that moved.

It never writes anything:
  * the DB is opened with sqlite3 URI mode=ro;
  * src.scraper is replaced by a stub whose get_db / update_job_score raise, so neither
    scorer module can reach the write path (this also means jobspy is not needed — any
    python3 works).

Usage:
  python3 scripts/eval_quick_scorer.py                 # HEAD vs working tree
  python3 scripts/eval_quick_scorer.py --old-ref HEAD~1
  python3 scripts/eval_quick_scorer.py --new /tmp/candidate_quick_scorer.py

Labels (LABELS below) were assigned by reading title + company + description:
  R = relevant for the candidate (target family, plausible level for the profile's years + a master's, no F-1 blocker)
  B = borderline (adjacent role, stretch seniority, sponsorship-unfriendly, unclear from title)
  I = irrelevant (wrong field, far too senior, F-1 ineligible, hourly labor)
"strat" rows are a 400-row sample stratified by OLD score band x source x region x job type
(weight = rows in stratum / rows sampled). "holdout" rows were drawn afterwards from the NEW
>=90 band, to check the new top band on rows the rules were not tuned on.
"""

import argparse
import collections
import inspect
import re
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "jobs.db"


# --------------------------------------------------------------------------------------
# Module loading (read-only by construction)
# --------------------------------------------------------------------------------------

def _install_readonly_stubs():
    sys.path.insert(0, str(ROOT))
    import src  # noqa: F401  (package must exist before we register a submodule stub)

    def _blocked(*_a, **_k):
        raise RuntimeError("eval_quick_scorer is read-only: scraper DB helpers are disabled")

    stub = types.ModuleType("src.scraper")
    stub.get_db = _blocked
    stub.update_job_score = _blocked
    sys.modules["src.scraper"] = stub


def _load_module(name, source):
    mod = types.ModuleType(f"src.{name}")
    mod.__package__ = "src"
    mod.__file__ = f"<{name}>"
    exec(compile(source, f"<{name}>", "exec"), mod.__dict__)
    return mod


def _call(scorer, row):
    """Call score_job_quick with whatever optional kwargs this version supports."""
    fn = scorer.score_job_quick
    params = inspect.signature(fn).parameters
    kwargs = {k: row[k] for k in ("job_type", "date_posted", "scraped_at", "is_remote", "salary_min")
              if k in params}
    score = fn(row["title"] or "", row["company"] or "", row["location"] or "",
               row["description"] or "", **kwargs)[0]
    return float(score)


# --------------------------------------------------------------------------------------
# Reporting helpers
# --------------------------------------------------------------------------------------

def _dist(scores):
    top = [s for s in scores if s >= 70]
    c = collections.Counter(top)
    val, cnt = c.most_common(1)[0] if c else (None, 0)
    return {
        "distinct_all": len(set(scores)),
        "distinct_70_100": len(set(top)),
        "n_ge70": len(top),
        "n_ge90": sum(s >= 90 for s in scores),
        "biggest_bucket": f"{val:g} x{cnt} ({100 * cnt / max(1, len(top)):.1f}% of >=70)" if c else "-",
        "biggest_bucket_ge90": (lambda cc: f"{cc[0][0]:g} x{cc[0][1]}" if cc else "-")(
            collections.Counter(s for s in scores if s >= 90).most_common(1)),
    }


def _hist(scores, lo=0, hi=100, step=10):
    edges = list(range(lo, hi, step))
    out = collections.OrderedDict((f"{e}-{e + step - 1 if e + step < hi else hi}", 0) for e in edges)
    for s in scores:
        e = min(int(s // step) * step, hi - step)
        out[f"{e}-{e + step - 1 if e + step < hi else hi}"] += 1
    return out


def _prec(rows, key, thr, weighted):
    sel = [r for r in rows if r[key] >= thr]
    if not sel:
        return "n=0"
    w = (lambda r: r["weight"]) if weighted else (lambda r: 1.0)
    tot = sum(w(r) for r in sel)
    pr = sum(w(r) for r in sel if r["label"] == "R") / tot
    prb = sum(w(r) for r in sel if r["label"] in "RB") / tot
    pi = sum(w(r) for r in sel if r["label"] == "I") / tot
    return f"n={len(sel):>3}  R={100 * pr:4.0f}%  R+B={100 * prb:4.0f}%  I={100 * pi:4.0f}%"


def _recall(rows, key, thr, weighted):
    rel = [r for r in rows if r["label"] == "R"]
    if not rel:
        return "-"
    w = (lambda r: r["weight"]) if weighted else (lambda r: 1.0)
    return f"{100 * sum(w(r) for r in rel if r[key] >= thr) / sum(w(r) for r in rel):.0f}%"


NAMED = [
    # (label, title regex, company regex or None, job_type or None)
    ("Podiatrist (Program Manager) - VA", r"\bpodiatrist\b", None, None),
    ("Ops Manager, School Food Service", r"operations manager.*food service|food service.*operations manager", None, None),
    ("Amazon Site Leader", r"\bsite leader\b", r"amazon", None),
    ("Defense Program Manager", r"\bdefense program manager\b", None, None),
    ("Operations Specialist, Driving", r"\boperations specialist,? driving\b", None, None),
    ("Senior TPM in internship panel", r"\b(?:senior|sr\.?)\s+(?:technical program manager|tpm)\b", None, "internship"),
    ("Warehouse Associate (was 95)", r"\bwarehouse associate\b", None, None),
    ("Applied Scientist, Supply Chain", r"\bapplied scientist\b.*supply chain", None, None),
    ("VP, Global Supply Chain", r"^vp,? ", None, None),
]

# Synthetic probes: the reported false positives verbatim (some were pruned from the DB by
# the 90-day export prune) plus priority-order checks. (title, company, location, job_type)
PROBES = [
    ("Podiatrist (Program Manager) - Outpatient Mental Health Section", "US Department of Veterans Affairs",
     "Seattle, WA, US", "fulltime"),
    ("Operations Manager Needed for School Food Service", "Chartwells K12", "Seattle, WA", "fulltime"),
    ("Site Leader", "Amazon", "Kent, WA", "fulltime"),
    ("Defense Program Manager", "Actalent", "Seattle, WA", "fulltime"),
    ("Operations Specialist, Driving", "AdaptHealth", "Kent, WA", "fulltime"),
    ("Senior Technical Program Manager", "Amazon", "Seattle, WA", "internship"),
    ("Fraud Analyst", "Amazon", "Seattle, WA", "fulltime"),
    ("Risk and Compliance Analyst", "Uber", "Seattle, WA", "fulltime"),
    ("Supply Chain Analyst", "Amazon", "Seattle, WA", "fulltime"),
    ("Operations Analyst", "Costco Wholesale", "Issaquah, WA", "fulltime"),
    ("Demand Planner", "Starbucks", "Seattle, WA", "fulltime"),
    ("Program Manager", "Microsoft", "Redmond, WA", "fulltime"),
    ("Supply Chain Analyst", "Target", "Minneapolis, MN", "fulltime"),
    ("Supply Chain Intern", "Amazon", "Seattle, WA", "internship"),
    ("Procurement Analyst", "Lockheed Martin", "Fort Worth, TX", "fulltime"),
    ("Director, Supply Chain", "Amazon", "Seattle, WA", "fulltime"),
]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--old-ref", default="HEAD", help="git ref for the old scorer (default HEAD)")
    ap.add_argument("--new", default=None, help="path to the new scorer (default src/quick_scorer.py)")
    ap.add_argument("--movers", type=int, default=25, help="how many moved labeled rows to list")
    args = ap.parse_args()

    _install_readonly_stubs()
    old_src = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{args.old_ref}:src/quick_scorer.py"], text=True)
    new_src = Path(args.new or ROOT / "src" / "quick_scorer.py").read_text()
    old = _load_module("quick_scorer_old", old_src)
    new = _load_module("quick_scorer_new", new_src)

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cols = ("job_id, title, company, location, description, job_type, date_posted, scraped_at, "
            "is_remote, salary_min, source, region, score")
    rows = [dict(r) for r in conn.execute(f"SELECT {cols} FROM jobs")]
    conn.close()

    for r in rows:
        r["old"] = _call(old, r)
        r["new"] = _call(new, r)

    stored_mismatch = sum(1 for r in rows if r["score"] is not None and abs(r["score"] - r["old"]) > 0.01)
    print(f"Rows scored in memory: {len(rows):,}   (stored score != old-rule rescore: {stored_mismatch:,};"
          f" nonzero means stored scores came from other rules or Pass 2)")

    # ---- distribution ---------------------------------------------------------------
    d_old, d_new = _dist([r["old"] for r in rows]), _dist([r["new"] for r in rows])
    print("\n== Score distribution (all rows) ==")
    print(f"{'metric':<22}{'old':>34}{'new':>34}")
    for k in d_old:
        print(f"{k:<22}{str(d_old[k]):>34}{str(d_new[k]):>34}")

    print("\n== Histogram (10-pt buckets) ==")
    h_old, h_new = _hist([r["old"] for r in rows]), _hist([r["new"] for r in rows])
    for b in h_old:
        print(f"  {b:>7}  old {h_old[b]:>7,}   new {h_new[b]:>7,}")
    print("  70-100 in 2-pt buckets (new):")
    h2 = _hist([r["new"] for r in rows if r["new"] >= 70], lo=70, hi=100, step=2)
    print("   " + "  ".join(f"{b}:{n}" for b, n in h2.items()))

    print("\n== Per panel: rows >=70 / >=90 ==")
    panels = collections.defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        p = panels[(r["region"], "internship" if r["job_type"] == "internship" else "fulltime")]
        p[0] += r["old"] >= 70
        p[1] += r["old"] >= 90
        p[2] += r["new"] >= 70
        p[3] += r["new"] >= 90
    print(f"  {'panel':<22}{'old>=70':>9}{'old>=90':>9}{'new>=70':>9}{'new>=90':>9}")
    for k in sorted(panels, key=str):
        v = panels[k]
        print(f"  {str(k[0]) + ' / ' + k[1]:<22}{v[0]:>9,}{v[1]:>9,}{v[2]:>9,}{v[3]:>9,}")

    # ---- labeled sample ---------------------------------------------------------------
    by_id = {r["job_id"]: r for r in rows}
    lab = []
    for jid, (label, weight, part) in LABELS.items():
        r = by_id.get(jid)
        if r:
            lab.append({**r, "label": label, "weight": weight, "part": part})
    strat = [r for r in lab if r["part"] == "strat"]
    hold = [r for r in lab if r["part"] == "holdout"]
    hold80 = [r for r in lab if r["part"] == "holdout80"]
    print(f"\n== Labeled sample: {len(strat)} stratified + {len(hold)} holdout(>=90) + {len(hold80)} "
          f"holdout(80-90) rows (missing from DB: {len(LABELS) - len(lab)}) ==")
    print("  Label mix (stratified):", dict(collections.Counter(r["label"] for r in strat)))
    for thr in (90, 80, 70):
        print(f"  precision @>={thr}  old: {_prec(strat, 'old', thr, False)}")
        print(f"  {'':<16} new: {_prec(strat, 'new', thr, False)}")
        print(f"  {'(weighted est.)':<16} old: {_prec(strat, 'old', thr, True)}")
        print(f"  {'':<16} new: {_prec(strat, 'new', thr, True)}")
    if hold:
        print(f"  HOLDOUT drawn from new >=90 band   new @>=90: {_prec(hold, 'new', 90, False)}")
        print(f"  {'':<34} same rows, old @>=90: {_prec(hold, 'old', 90, False)}")
    if hold80:
        print(f"  HOLDOUT drawn from new 80-90 band  new @>=80: {_prec(hold80, 'new', 80, False)}")
    if hold or hold80:
        old_top = [r for r in strat if r["old"] >= 90]
        print(f"  (compare: old >=90 band on its own stratified rows: {_prec(old_top, 'old', 90, False)})")
    for thr in (90, 70):
        print(f"  recall of R rows @>={thr}:  old {_recall(strat, 'old', thr, False)} "
              f"(weighted {_recall(strat, 'old', thr, True)})   new {_recall(strat, 'new', thr, False)} "
              f"(weighted {_recall(strat, 'new', thr, True)})")
    leak_old = sum(1 for r in strat if r["label"] == "I" and r["old"] >= 70)
    leak_new = sum(1 for r in strat if r["label"] == "I" and r["new"] >= 70)
    print(f"  I-labeled rows scoring >=70: old {leak_old}  new {leak_new}  (of {sum(r['label'] == 'I' for r in strat)})")

    # Rank quality: mean score by label.
    for part_name, part in (("stratified", strat),):
        for key in ("old", "new"):
            means = {L: sum(r[key] for r in part if r["label"] == L) / max(1, sum(r["label"] == L for r in part))
                     for L in "RBI"}
            print(f"  mean {key} score by label ({part_name}): " + "  ".join(f"{L}={v:.1f}" for L, v in means.items()))

    print(f"\n== Notable labeled rows that moved (top {args.movers} by |new-old|) ==")
    for r in sorted(lab, key=lambda r: -abs(r["new"] - r["old"]))[:args.movers]:
        print(f"  [{r['label']}] {r['old']:>5.1f} -> {r['new']:>5.1f}  {r['title'][:70]} @ {r['company'][:28]}"
              f"  ({r['source']}/{r['region']}/{r['job_type']})")
    print("\n  R-labeled rows still below 70 (new):")
    for r in sorted((r for r in lab if r["label"] == "R" and r["new"] < 70), key=lambda r: r["new"]):
        print(f"    {r['old']:>5.1f} -> {r['new']:>5.1f}  {r['title'][:70]} @ {r['company'][:28]}")
    print("  I-labeled rows at >=75 (new):")
    for r in sorted((r for r in lab if r["label"] == "I" and r["new"] >= 75), key=lambda r: -r["new"]):
        print(f"    {r['old']:>5.1f} -> {r['new']:>5.1f}  {r['title'][:70]} @ {r['company'][:28]}")

    # ---- named false positives ------------------------------------------------------
    print("\n== Named false positives (all matching rows in the DB) ==")
    for name, trx, crx, jt in NAMED:
        hits = [r for r in rows if re.search(trx, r["title"] or "", re.I)
                and (crx is None or re.search(crx, r["company"] or "", re.I))
                and (jt is None or r["job_type"] == jt)]
        if not hits:
            print(f"  {name:<34} (no rows)")
            continue
        o = [r["old"] for r in hits]
        n = [r["new"] for r in hits]
        ex = max(hits, key=lambda r: r["old"])
        print(f"  {name:<34} rows={len(hits):<4} old {min(o):g}-{max(o):g}  ->  new {min(n):g}-{max(n):g}"
              f"   e.g. '{ex['title'][:55]}' @ {ex['company'][:25]}")

    print("\n== Synthetic probes (no description, posted 1 day before scrape) ==")
    for title, company, location, job_type in PROBES:
        row = {"title": title, "company": company, "location": location, "description": "",
               "job_type": job_type, "date_posted": "2026-09-20", "scraped_at": "2026-09-21",
               "is_remote": 0, "salary_min": None}
        print(f"  old {_call(old, row):>5.1f}  new {_call(new, row):>5.1f}   {title[:60]} @ {company} ({job_type})")


# Hand labels: job_id -> (label, stratum weight, part)
LABELS = {
    '-9039352590121255347': ('R', 1.00, 'strat'),
    '1880644410788134587': ('I', 9.55, 'strat'),
    '-7474109254679510543': ('B', 9.55, 'strat'),
    '-9217477988580543948': ('B', 9.55, 'strat'),
    '-8025830532788838906': ('I', 9.55, 'strat'),
    '-4186859398528644940': ('R', 9.55, 'strat'),
    '-8463299710591009578': ('R', 9.55, 'strat'),
    '-8963068358706955654': ('I', 9.55, 'strat'),
    '-6892561147729966103': ('R', 9.55, 'strat'),
    '-5568810117945752431': ('R', 9.55, 'strat'),
    '1641765973252133314': ('B', 9.55, 'strat'),
    '177646961935603098': ('B', 9.55, 'strat'),
    '-7716906951376576480': ('B', 9.55, 'strat'),
    '4382828795093813659': ('B', 9.55, 'strat'),
    '-8269734905390846798': ('I', 9.55, 'strat'),
    '-6911388119707684457': ('I', 9.55, 'strat'),
    '-4009083767197547802': ('I', 9.55, 'strat'),
    '8631501132759873809': ('I', 9.55, 'strat'),
    '2954096499159966064': ('R', 9.55, 'strat'),
    '-288111739096459304': ('B', 9.55, 'strat'),
    '4779117094017221576': ('B', 9.55, 'strat'),
    '-1780363060644279224': ('B', 9.55, 'strat'),
    '8692374334274119939': ('B', 9.55, 'strat'),
    '-1413402883468887860': ('B', 9.55, 'strat'),
    '1228032105117203604': ('I', 9.55, 'strat'),
    '618661056719886355': ('R', 9.55, 'strat'),
    '-3037541778288327370': ('B', 9.55, 'strat'),
    '-4043841476544902969': ('B', 9.55, 'strat'),
    '-2514449188991924098': ('R', 9.55, 'strat'),
    '6494934433714740274': ('I', 9.55, 'strat'),
    '3792251965356343446': ('R', 9.55, 'strat'),
    '8114893117338203022': ('B', 9.55, 'strat'),
    '-1877304236202809267': ('B', 1.03, 'strat'),
    '-7574043730301616807': ('I', 1.03, 'strat'),
    '-673971015690753987': ('R', 1.03, 'strat'),
    '-1377126172258678100': ('B', 1.03, 'strat'),
    '-3030412637860116970': ('R', 1.03, 'strat'),
    '2612724805051591590': ('R', 1.03, 'strat'),
    '-713253762306172089': ('B', 1.03, 'strat'),
    '2719671317235972310': ('I', 1.03, 'strat'),
    '-2896210475748238035': ('R', 1.03, 'strat'),
    '8798351744574863723': ('I', 1.03, 'strat'),
    '867759679700088530': ('B', 1.03, 'strat'),
    '4239555624442977583': ('B', 1.03, 'strat'),
    '-2913450443961902750': ('I', 1.03, 'strat'),
    '-5472771418518283843': ('B', 1.03, 'strat'),
    '-4173427829318700197': ('I', 1.03, 'strat'),
    '1664071157986120351': ('B', 1.03, 'strat'),
    '-424458744793019120': ('I', 1.03, 'strat'),
    '788917536275136818': ('B', 1.03, 'strat'),
    '-6518590441267730072': ('I', 1.03, 'strat'),
    '-6886011927636430945': ('B', 1.03, 'strat'),
    '3945528826379775581': ('I', 1.03, 'strat'),
    '-1683199108437482943': ('B', 1.03, 'strat'),
    '4059905880510204499': ('B', 1.03, 'strat'),
    '-2897943666322211884': ('B', 1.03, 'strat'),
    '-7473025196316860563': ('B', 1.03, 'strat'),
    '-2844506346976639445': ('I', 1.03, 'strat'),
    '1340779312803772094': ('R', 1.03, 'strat'),
    '-1013341099829894307': ('B', 1.03, 'strat'),
    '3888242152922577566': ('B', 1.03, 'strat'),
    '3262060072007049772': ('I', 1.03, 'strat'),
    '4808495259127474232': ('B', 1.03, 'strat'),
    '7989637231550898635': ('R', 1.00, 'strat'),
    '1072002572433244480': ('R', 1.00, 'strat'),
    '-3812073467306290640': ('R', 1.00, 'strat'),
    '-4417850812904641758': ('B', 1.00, 'strat'),
    '-4797040779161781414': ('I', 1.00, 'strat'),
    '8775587365790512178': ('R', 1.00, 'strat'),
    '-4692385402139544949': ('R', 1.00, 'strat'),
    '1696012500190997366': ('B', 46.10, 'strat'),
    '-7347595980376527396': ('I', 46.10, 'strat'),
    '-6481747443516351152': ('I', 46.10, 'strat'),
    '2206235691555452078': ('R', 46.10, 'strat'),
    '6050388065016685596': ('B', 46.10, 'strat'),
    '6445462610527032022': ('B', 46.10, 'strat'),
    '-7081659907104031534': ('R', 46.10, 'strat'),
    '-5617600091991008685': ('B', 46.10, 'strat'),
    '-6140193176856832552': ('R', 46.10, 'strat'),
    '-8775348714350934686': ('B', 46.10, 'strat'),
    '-4325332583156375400': ('I', 46.10, 'strat'),
    '-642377784450628838': ('B', 46.10, 'strat'),
    '-4100940215452446656': ('B', 46.10, 'strat'),
    '-3793511851800424593': ('R', 46.10, 'strat'),
    '-1514465880172204931': ('R', 46.10, 'strat'),
    '-3287007610452948810': ('I', 46.10, 'strat'),
    '-8347013262824574149': ('I', 46.10, 'strat'),
    '-7912579850929482295': ('B', 46.10, 'strat'),
    '4083754336708357179': ('B', 46.10, 'strat'),
    '7609234678638580771': ('I', 46.10, 'strat'),
    '-8516593521891664392': ('I', 46.10, 'strat'),
    '-7371730334787177699': ('B', 46.10, 'strat'),
    '6018855352268961642': ('R', 46.10, 'strat'),
    '13660625057931121': ('I', 46.10, 'strat'),
    '-3137152288390409728': ('B', 46.10, 'strat'),
    '3698180829917071899': ('B', 46.10, 'strat'),
    '-747734227614800007': ('I', 46.10, 'strat'),
    '-6858824398945945786': ('B', 46.10, 'strat'),
    '-727947841237109755': ('I', 46.10, 'strat'),
    '-4397143221322807230': ('B', 46.10, 'strat'),
    '3902740401488025789': ('I', 3.53, 'strat'),
    '-1332210048614315076': ('R', 3.53, 'strat'),
    '3563505112719444815': ('I', 3.53, 'strat'),
    '-366729923665678730': ('B', 3.53, 'strat'),
    '-612452331032832735': ('I', 3.53, 'strat'),
    '9147278446747103463': ('I', 3.53, 'strat'),
    '-9020095595558436048': ('R', 3.53, 'strat'),
    '5933721004412477629': ('R', 3.53, 'strat'),
    '2029662053836063720': ('R', 3.53, 'strat'),
    '-3370032670058802443': ('B', 3.53, 'strat'),
    '956382983084578504': ('B', 3.53, 'strat'),
    '-1778019939030570338': ('R', 3.53, 'strat'),
    '3503963999357796573': ('I', 3.53, 'strat'),
    '3094760870319877111': ('I', 3.53, 'strat'),
    '-1528709746320063739': ('B', 3.53, 'strat'),
    '-2370661856371960748': ('I', 3.53, 'strat'),
    '6570289340666878553': ('B', 3.53, 'strat'),
    '5330077028094567142': ('I', 3.53, 'strat'),
    '4829389892071129955': ('I', 3.53, 'strat'),
    '2461943639504247981': ('I', 3.53, 'strat'),
    '-9142714513155343314': ('B', 3.53, 'strat'),
    '-2922014864953954467': ('B', 3.53, 'strat'),
    '-1983974927187802400': ('B', 3.53, 'strat'),
    '-879207056411829234': ('I', 3.53, 'strat'),
    '6187506979877481065': ('B', 3.53, 'strat'),
    '-6535809409065121912': ('I', 3.53, 'strat'),
    '-9220254636809646187': ('I', 3.53, 'strat'),
    '-5856705151776745860': ('I', 3.53, 'strat'),
    '-1553901639168447004': ('B', 3.53, 'strat'),
    '395591604201342975': ('B', 3.53, 'strat'),
    '-788203008046068610': ('R', 43.12, 'strat'),
    '-5385153064531370905': ('B', 43.12, 'strat'),
    '2775533115350934800': ('B', 43.12, 'strat'),
    '-4490257248762733863': ('B', 43.12, 'strat'),
    '-4666460927992322231': ('I', 43.12, 'strat'),
    '8572520187853324233': ('R', 43.12, 'strat'),
    '8016792667442325431': ('I', 43.12, 'strat'),
    '-8136099933818992597': ('I', 43.12, 'strat'),
    '-1938091363062581239': ('B', 43.12, 'strat'),
    '5379059753884172822': ('B', 43.12, 'strat'),
    '-2022649584890558725': ('B', 43.12, 'strat'),
    '1797916280990667085': ('B', 43.12, 'strat'),
    '7964511667720789942': ('R', 43.12, 'strat'),
    '241325806749298712': ('I', 43.12, 'strat'),
    '-251832413177848421': ('B', 43.12, 'strat'),
    '4326143677024955276': ('B', 43.12, 'strat'),
    '3085034955602083529': ('I', 6.75, 'strat'),
    '7936857567369961277': ('B', 6.75, 'strat'),
    '6107479965197758957': ('I', 6.75, 'strat'),
    '-2148076330955663865': ('B', 6.75, 'strat'),
    '6003843700526500983': ('I', 6.75, 'strat'),
    '-4363834278653158850': ('I', 6.75, 'strat'),
    '5607989804040433660': ('R', 6.75, 'strat'),
    '-5471228474996869941': ('R', 6.75, 'strat'),
    '678256118642192328': ('I', 6.75, 'strat'),
    '-8236550846350544119': ('I', 6.75, 'strat'),
    '4172390273407708305': ('R', 6.75, 'strat'),
    '7957542439014286626': ('I', 6.75, 'strat'),
    '8673180378890837180': ('I', 6.75, 'strat'),
    '6864456496493295349': ('B', 6.75, 'strat'),
    '1648993453848667371': ('B', 6.75, 'strat'),
    '3600762835049038511': ('B', 6.75, 'strat'),
    '-228313856276777884': ('B', 2.31, 'strat'),
    '9174400855171376021': ('B', 2.31, 'strat'),
    '8769474567727723045': ('I', 2.31, 'strat'),
    '8141890783487080139': ('I', 2.31, 'strat'),
    '3052528516293985888': ('I', 2.31, 'strat'),
    '-4560429697264116495': ('B', 2.31, 'strat'),
    '7620003146197031424': ('I', 2.31, 'strat'),
    '-3133478172546538589': ('I', 2.31, 'strat'),
    '636454241596777033': ('B', 2.31, 'strat'),
    '4823453776812842721': ('B', 2.31, 'strat'),
    '3230157767928944362': ('I', 2.31, 'strat'),
    '-3829453105690389980': ('I', 2.31, 'strat'),
    '-8945365679946240297': ('I', 2.31, 'strat'),
    '-6619336214101549692': ('I', 2.31, 'strat'),
    '-4069479405212143824': ('B', 2.31, 'strat'),
    '571577501213624448': ('B', 2.31, 'strat'),
    '6968420488916003145': ('I', 1.00, 'strat'),
    '-6687933725760754051': ('R', 173.69, 'strat'),
    '-8126468944487033796': ('R', 173.69, 'strat'),
    '-4465175142838372447': ('B', 173.69, 'strat'),
    '3646069069087742726': ('B', 173.69, 'strat'),
    '-6931913360176779256': ('B', 173.69, 'strat'),
    '9170807567567158329': ('I', 173.69, 'strat'),
    '251443395769386003': ('I', 173.69, 'strat'),
    '-2524586294982139360': ('I', 173.69, 'strat'),
    '1885184395023860061': ('R', 173.69, 'strat'),
    '-8011736954145501393': ('B', 173.69, 'strat'),
    '3222947805747512962': ('R', 173.69, 'strat'),
    '-3923795090853139773': ('R', 173.69, 'strat'),
    '3145912313344120925': ('B', 173.69, 'strat'),
    '7919609603496066770': ('I', 173.69, 'strat'),
    '-6047798176526258973': ('B', 173.69, 'strat'),
    '-1041986510146584087': ('R', 173.69, 'strat'),
    '-7675202543612080962': ('B', 32.07, 'strat'),
    '-2830825890706253345': ('R', 32.07, 'strat'),
    '5397249205177607414': ('R', 32.07, 'strat'),
    '7560962626010756529': ('B', 32.07, 'strat'),
    '6393140538303793986': ('R', 32.07, 'strat'),
    '3514605551947468957': ('B', 32.07, 'strat'),
    '6597811772979490721': ('B', 32.07, 'strat'),
    '705401641363575601': ('I', 32.07, 'strat'),
    '-3801817888271095716': ('B', 32.07, 'strat'),
    '4672720773181650272': ('I', 32.07, 'strat'),
    '3561713025084611261': ('I', 32.07, 'strat'),
    '1353024803787663718': ('B', 32.07, 'strat'),
    '8550554831374378451': ('B', 32.07, 'strat'),
    '-158716164631162229': ('B', 32.07, 'strat'),
    '-3918542593343451749': ('I', 32.07, 'strat'),
    '2494168049852773715': ('B', 187.11, 'strat'),
    '-7806925377111835347': ('I', 187.11, 'strat'),
    '3985164882337355802': ('I', 187.11, 'strat'),
    '8545647142194502653': ('R', 187.11, 'strat'),
    '-1378426478555934279': ('B', 187.11, 'strat'),
    '-429699442926399190': ('B', 187.11, 'strat'),
    '1276045609322894458': ('B', 187.11, 'strat'),
    '-7327959577806736252': ('R', 187.11, 'strat'),
    '-5693976377875159429': ('B', 187.11, 'strat'),
    '-8771042053597909517': ('R', 28.44, 'strat'),
    '-247120638637968884': ('I', 28.44, 'strat'),
    '1957876288382272935': ('B', 28.44, 'strat'),
    '6153613627720589949': ('I', 28.44, 'strat'),
    '-1836442210718093292': ('I', 28.44, 'strat'),
    '-5942557273917606417': ('I', 28.44, 'strat'),
    '-7839852712471393820': ('I', 28.44, 'strat'),
    '-5485758426516291803': ('B', 28.44, 'strat'),
    '4507197784658458084': ('B', 28.44, 'strat'),
    '3803736140338673634': ('I', 48.67, 'strat'),
    '-764771135239465285': ('I', 48.67, 'strat'),
    '95335354770815257': ('I', 48.67, 'strat'),
    '-6129273449824133212': ('B', 48.67, 'strat'),
    '-3208329515983410292': ('B', 48.67, 'strat'),
    '880719228138397614': ('I', 48.67, 'strat'),
    '-2337362796519324729': ('B', 48.67, 'strat'),
    '1791045536388552130': ('B', 48.67, 'strat'),
    '-3399782518049086728': ('I', 48.67, 'strat'),
    '4147777246487854279': ('I', 3.11, 'strat'),
    '-8935378888643182503': ('I', 3.11, 'strat'),
    '-467373823405587882': ('B', 3.11, 'strat'),
    '6068191925665936512': ('I', 3.11, 'strat'),
    '-5729601020574954374': ('B', 3.11, 'strat'),
    '-7114608497059411850': ('B', 3.11, 'strat'),
    '-5939650896392187594': ('I', 3.11, 'strat'),
    '-6655142972298630428': ('B', 3.11, 'strat'),
    '5024655407410894891': ('B', 3.11, 'strat'),
    '2245287326483391478': ('R', 744.11, 'strat'),
    '1293453000200654797': ('I', 744.11, 'strat'),
    '-9052659854544026862': ('R', 744.11, 'strat'),
    '5538751634044220976': ('B', 744.11, 'strat'),
    '5479059913333278578': ('B', 744.11, 'strat'),
    '-1807967439238183373': ('I', 744.11, 'strat'),
    '1968761516995151239': ('B', 744.11, 'strat'),
    '-5809245902311259877': ('B', 744.11, 'strat'),
    '2005618592956078371': ('R', 744.11, 'strat'),
    '-4767222493198628582': ('I', 131.89, 'strat'),
    '4301458504133362191': ('R', 131.89, 'strat'),
    '-4814612607665468556': ('I', 131.89, 'strat'),
    '-985193088934589917': ('I', 131.89, 'strat'),
    '-4728023725177771000': ('R', 131.89, 'strat'),
    '1805432294781760084': ('B', 131.89, 'strat'),
    '-7752263873121660273': ('B', 131.89, 'strat'),
    '7237071288392613084': ('B', 131.89, 'strat'),
    '-8471874343654628735': ('I', 131.89, 'strat'),
    '-2753313648091224744': ('I', 143.75, 'strat'),
    '9192233782837251548': ('B', 143.75, 'strat'),
    '6115558854912659643': ('B', 143.75, 'strat'),
    '2460409481074686972': ('I', 143.75, 'strat'),
    '5167038597918129908': ('I', 143.75, 'strat'),
    '-8321587437711838157': ('B', 143.75, 'strat'),
    '-5647848213813365373': ('B', 143.75, 'strat'),
    '-2227635011374997909': ('B', 143.75, 'strat'),
    '7749682349465701098': ('I', 14.12, 'strat'),
    '-3256100672814901580': ('R', 14.12, 'strat'),
    '8857506827202557763': ('I', 14.12, 'strat'),
    '-2804892260391631484': ('B', 14.12, 'strat'),
    '-4534249854698494583': ('B', 14.12, 'strat'),
    '-2619888089199695928': ('B', 14.12, 'strat'),
    '-9035712233317388661': ('B', 14.12, 'strat'),
    '-2655542423701511801': ('B', 14.12, 'strat'),
    '-5268097156427217706': ('I', 628.11, 'strat'),
    '-147858149399421991': ('B', 628.11, 'strat'),
    '2412002888132983277': ('B', 628.11, 'strat'),
    '-5914292995926979450': ('I', 628.11, 'strat'),
    '-1941589464779032478': ('I', 628.11, 'strat'),
    '5001173817615869910': ('B', 628.11, 'strat'),
    '3246969607245411334': ('I', 628.11, 'strat'),
    '-8797371536240403': ('I', 628.11, 'strat'),
    '4681537607427448424': ('I', 628.11, 'strat'),
    '6153019096238691249': ('B', 76.00, 'strat'),
    '-2871373518176364852': ('I', 76.00, 'strat'),
    '8968975676361361561': ('B', 76.00, 'strat'),
    '502626417935209225': ('I', 76.00, 'strat'),
    '-7266213551430775161': ('I', 76.00, 'strat'),
    '2753105220989488931': ('I', 76.00, 'strat'),
    '-2755830029036185153': ('I', 76.00, 'strat'),
    '732188364631414456': ('I', 76.00, 'strat'),
    '-2555593303088443988': ('I', 76.00, 'strat'),
    '7450691229198929129': ('I', 147.22, 'strat'),
    '-8484281667235075918': ('I', 147.22, 'strat'),
    '2333757324247266629': ('I', 147.22, 'strat'),
    '7472294311086077508': ('I', 147.22, 'strat'),
    '-111981389792932726': ('I', 147.22, 'strat'),
    '-6238310711378864057': ('I', 147.22, 'strat'),
    '-2515659305580481805': ('I', 147.22, 'strat'),
    '2106148720232921633': ('I', 147.22, 'strat'),
    '8803970171900531423': ('I', 147.22, 'strat'),
    '1699733694019853657': ('I', 23.89, 'strat'),
    '-4768450105241336108': ('I', 23.89, 'strat'),
    '-281137548873062715': ('B', 23.89, 'strat'),
    '5174934051325550864': ('I', 23.89, 'strat'),
    '8543454408182386708': ('I', 23.89, 'strat'),
    '-2539259643790950589': ('I', 23.89, 'strat'),
    '2467514431322533617': ('I', 23.89, 'strat'),
    '-3976088038771333205': ('I', 23.89, 'strat'),
    '-6718554456514814428': ('I', 23.89, 'strat'),
    '-3037328362607637228': ('R', 1215.33, 'strat'),
    '-7807783091867584132': ('R', 1215.33, 'strat'),
    '9081605505867365665': ('R', 1215.33, 'strat'),
    '-6220253730188431877': ('I', 1215.33, 'strat'),
    '-2730594630556950879': ('B', 1215.33, 'strat'),
    '781310653315090779': ('I', 1215.33, 'strat'),
    '-4287227402306698408': ('B', 1215.33, 'strat'),
    '-9023086402686249640': ('I', 1215.33, 'strat'),
    '-1542781811145802934': ('R', 1215.33, 'strat'),
    '7192734056818778092': ('I', 248.44, 'strat'),
    '-5054334269280178811': ('B', 248.44, 'strat'),
    '4538945734128435564': ('I', 248.44, 'strat'),
    '-1622943717883907502': ('B', 248.44, 'strat'),
    '-5210597116068540972': ('R', 248.44, 'strat'),
    '4299583667072521803': ('B', 248.44, 'strat'),
    '3220054103864622558': ('I', 248.44, 'strat'),
    '4004840277123896390': ('R', 248.44, 'strat'),
    '-3270429246117117634': ('I', 248.44, 'strat'),
    '-1602810175674270660': ('I', 381.12, 'strat'),
    '7896953342076886771': ('I', 381.12, 'strat'),
    '-6592167017864056701': ('I', 381.12, 'strat'),
    '-6604291068824572993': ('I', 381.12, 'strat'),
    '7869383142580870372': ('I', 381.12, 'strat'),
    '5005009548584124092': ('B', 381.12, 'strat'),
    '8632771309280987314': ('I', 381.12, 'strat'),
    '-7546077538954612430': ('B', 381.12, 'strat'),
    '1791708631833068800': ('I', 66.38, 'strat'),
    '-2638472251670805818': ('I', 66.38, 'strat'),
    '327474249680471583': ('I', 66.38, 'strat'),
    '-1587526851264299658': ('B', 66.38, 'strat'),
    '-6745604205391153396': ('I', 66.38, 'strat'),
    '-8896975221150850534': ('I', 66.38, 'strat'),
    '-1665762661807312035': ('B', 66.38, 'strat'),
    '-1017756119791638455': ('I', 66.38, 'strat'),
    '-7035225712057204522': ('I', 2089.71, 'strat'),
    '-7151030769231716549': ('I', 2089.71, 'strat'),
    '-7536557096856681248': ('I', 2089.71, 'strat'),
    '2614584973346918641': ('I', 2089.71, 'strat'),
    '-1939276320872272170': ('I', 2089.71, 'strat'),
    '5375940315308851925': ('I', 2089.71, 'strat'),
    '-7107791058382369094': ('I', 2089.71, 'strat'),
    '-2685165447856969299': ('I', 632.71, 'strat'),
    '6019287417047919740': ('I', 632.71, 'strat'),
    '-1644437428705997200': ('I', 632.71, 'strat'),
    '-4551121996212004786': ('I', 632.71, 'strat'),
    '6661526558972145485': ('I', 632.71, 'strat'),
    '6517110587560192815': ('I', 632.71, 'strat'),
    '-4960209024793872382': ('I', 632.71, 'strat'),
    '-2942717475868566126': ('I', 62.00, 'strat'),
    '-8009651384414920733': ('I', 62.00, 'strat'),
    '-2321074900155324191': ('I', 62.00, 'strat'),
    '8291134984178410111': ('I', 62.00, 'strat'),
    '3431630799197396727': ('I', 62.00, 'strat'),
    '3265526834591059928': ('I', 62.00, 'strat'),
    '-2643442663601763237': ('I', 10.67, 'strat'),
    '-8658500212945441940': ('I', 10.67, 'strat'),
    '-9022576235621659057': ('I', 10.67, 'strat'),
    '-5027806894995246645': ('I', 10.67, 'strat'),
    '440738110316546080': ('R', 10.67, 'strat'),
    '-7792561546828419953': ('B', 10.67, 'strat'),
    '-5835611401925490534': ('B', 3122.33, 'strat'),
    '-3867632376234537762': ('I', 3122.33, 'strat'),
    '7178318543556902708': ('R', 3122.33, 'strat'),
    '2166946070814152623': ('B', 3122.33, 'strat'),
    '-7881194248571208163': ('I', 3122.33, 'strat'),
    '-8877137054371265042': ('B', 3122.33, 'strat'),
    '1790381603409708245': ('I', 1390.00, 'strat'),
    '3181382776621714788': ('I', 1390.00, 'strat'),
    '8804839592616501845': ('I', 1390.00, 'strat'),
    '4967434009314828664': ('B', 1390.00, 'strat'),
    '5632933704328928063': ('I', 1390.00, 'strat'),
    '2455974713606277083': ('I', 1390.00, 'strat'),
    '2302686448174039808': ('I', 77.00, 'strat'),
    '-3354148834149749793': ('I', 77.00, 'strat'),
    '3026824609032922981': ('I', 77.00, 'strat'),
    '153936968928928786': ('I', 77.00, 'strat'),
    '5332645359450898408': ('B', 77.00, 'strat'),
    '1713770476635445289': ('I', 77.00, 'strat'),
    '-9129644443454873801': ('I', 46.83, 'strat'),
    '2304836311490443134': ('B', 46.83, 'strat'),
    '-730862142093631379': ('I', 46.83, 'strat'),
    '1204612374705186646': ('I', 46.83, 'strat'),
    '-571782525142579767': ('I', 46.83, 'strat'),
    '-1337077167759923675': ('I', 46.83, 'strat'),
    '25b4d39aa25d28e9': ('R', 2.38, 'holdout'),
    '-3649722723294938445': ('R', 2.38, 'holdout'),
    '-3543053419608458343': ('R', 2.38, 'holdout'),
    '5531193348658323974': ('R', 2.38, 'holdout'),
    '-7630241262067899018': ('R', 2.38, 'holdout'),
    '-5248614098753547964': ('R', 2.38, 'holdout'),
    '3025829099737341153': ('B', 2.38, 'holdout'),
    '-3059983299613392551': ('R', 2.38, 'holdout'),
    '-5081719297239679615': ('B', 2.38, 'holdout'),
    '4535580967411472474': ('R', 2.38, 'holdout'),
    '-1972897307173400958': ('R', 2.38, 'holdout'),
    '-7099237041210774116': ('R', 2.38, 'holdout'),
    '7599762327803122993': ('R', 2.38, 'holdout'),
    '5650582783129340511': ('B', 2.38, 'holdout'),
    '-3501054659302099231': ('R', 2.38, 'holdout'),
    '8589368140553659931': ('R', 2.38, 'holdout'),
    '-1762887309774777247': ('R', 2.38, 'holdout'),
    '-7637014660301604776': ('B', 2.38, 'holdout'),
    '-4194434833000622713': ('B', 2.38, 'holdout'),
    '1625007074093864358': ('B', 2.38, 'holdout'),
    '8535885092880124494': ('R', 2.38, 'holdout'),
    '3101757659144190604': ('R', 2.38, 'holdout'),
    'a9b0a4b026390ed0': ('R', 2.38, 'holdout'),
    '3356110210198686119': ('B', 2.38, 'holdout'),
    '-4764280229380121185': ('R', 2.38, 'holdout'),
    '1840567895589397596': ('R', 2.38, 'holdout'),
    '-4637412222630691585': ('R', 2.38, 'holdout'),
    '-42914188163203239': ('B', 2.38, 'holdout'),
    '8983509056912390427': ('R', 2.38, 'holdout'),
    '2519148970291068354': ('B', 2.38, 'holdout'),
    '9127873007396833142': ('B', 2.38, 'holdout'),
    '-5135773654016434239': ('R', 2.38, 'holdout'),
    '-8396105146081679826': ('I', 2.38, 'holdout'),
    '5864636439972140748': ('B', 2.38, 'holdout'),
    '-6897679098953405828': ('R', 2.38, 'holdout'),
    '6010367297361571795': ('R', 2.38, 'holdout'),
    '4092455251660315543': ('R', 2.38, 'holdout'),
    '5163248703472780571': ('R', 2.38, 'holdout'),
    '693925358885225521': ('R', 2.38, 'holdout'),
    'abdd7af94d03d1b7': ('B', 2.38, 'holdout'),
    '-3322044514810576272': ('R', 2.38, 'holdout'),
    '6156625468136721011': ('R', 2.38, 'holdout'),
    '-8672161160063681868': ('B', 2.38, 'holdout'),
    '16db0790a09bc696': ('R', 2.38, 'holdout'),
    '-7881797461181183794': ('R', 2.38, 'holdout'),
    '-9096008607209597910': ('R', 2.38, 'holdout'),
    'b4743668b84108e3': ('R', 2.38, 'holdout'),
    '4212873626179645993': ('R', 2.38, 'holdout'),
    '1118607098720222515': ('R', 2.38, 'holdout'),
    '7204294892729483547': ('R', 2.38, 'holdout'),
    '-6269458842144636178': ('R', 2.38, 'holdout'),
    'e0cb5938c97f21f0': ('R', 2.38, 'holdout'),
    '8867713615097385021': ('R', 2.38, 'holdout'),
    '4689078483690736896': ('B', 2.38, 'holdout'),
    '-5533173961582371141': ('R', 2.38, 'holdout'),
    '-6384479633737780451': ('B', 2.38, 'holdout'),
    '-2373860320871258148': ('R', 2.38, 'holdout'),
    '-3568309473897773179': ('R', 2.38, 'holdout'),
    '-4060198754024012079': ('R', 2.38, 'holdout'),
    '4592483633051194603': ('B', 2.38, 'holdout'),
    '-9030092181113871155': ('R', 2.38, 'holdout'),
    '-5936792514138931032': ('B', 2.38, 'holdout'),
    '996982060452748933': ('B', 2.38, 'holdout'),
    '-1310018899980377525': ('R', 2.38, 'holdout'),
    '2856392365228070652': ('B', 2.38, 'holdout'),
    '5953177930126411061': ('B', 2.38, 'holdout'),
    '2006574994198438023': ('R', 2.38, 'holdout'),
    '6781424719011136425': ('B', 2.38, 'holdout'),
    '-4378786941034680659': ('R', 2.38, 'holdout'),
    '-4423140811437257162': ('R', 2.38, 'holdout'),
    '2253553737761429270': ('B', 2.38, 'holdout'),
    '-1919381376350760497': ('B', 2.38, 'holdout'),
    '-2537669428578575243': ('B', 2.38, 'holdout'),
    '458052461092386190': ('R', 2.38, 'holdout'),
    'aeb84f4fc8df1988': ('R', 2.38, 'holdout'),
    '-235439684162515303': ('R', 2.38, 'holdout'),
    'c79c67af6587285a': ('R', 2.38, 'holdout'),
    '-1339708047055554089': ('I', 2.38, 'holdout'),
    '3769287043285979347': ('B', 2.38, 'holdout'),
    '5461766629991835426': ('B', 2.38, 'holdout'),
    '-2270659775575100493': ('R', 2.38, 'holdout'),
    'fceebce2dd2c64ee': ('R', 2.38, 'holdout'),
    '-3837099805655681405': ('R', 2.38, 'holdout'),
    '5145834799701627190': ('R', 2.38, 'holdout'),
    '5692762889130394512': ('R', 2.38, 'holdout'),
    '6131512236987393551': ('B', 2.38, 'holdout'),
    '-1841750011788552072': ('R', 2.38, 'holdout'),
    '4107418874909809078': ('R', 2.38, 'holdout'),
    '2429021192801405038': ('R', 2.38, 'holdout'),
    'f959925fe1e114ce': ('R', 2.38, 'holdout'),
    '-5115575935412304129': ('R', 2.38, 'holdout'),
    '8057086928643986137': ('R', 2.38, 'holdout'),
    '-1917142487720034927': ('I', 2.38, 'holdout'),
    '171655560300568800': ('R', 2.38, 'holdout'),
    '-5126606969633994363': ('R', 2.38, 'holdout'),
    '9010488076254930372': ('R', 2.38, 'holdout'),
    '-2226551489871858755': ('B', 2.38, 'holdout'),
    '-5879767723325967663': ('R', 2.38, 'holdout'),
    '-5608254060318339174': ('R', 2.38, 'holdout'),
    '8308420563432583020': ('B', 2.38, 'holdout'),
    '-8067037744404120819': ('B', 68.46, 'holdout80'),
    '-8542151088433717288': ('B', 68.46, 'holdout80'),
    '-6627671124845439338': ('R', 68.46, 'holdout80'),
    '8410221910194825815': ('B', 68.46, 'holdout80'),
    '4064453977667862191': ('B', 68.46, 'holdout80'),
    '8169628113223437375': ('B', 68.46, 'holdout80'),
    '7732601965730504124': ('B', 68.46, 'holdout80'),
    'ba025c6b1a91e175': ('B', 68.46, 'holdout80'),
    '1389994176190683141': ('B', 68.46, 'holdout80'),
    '6902294999892901490': ('B', 68.46, 'holdout80'),
    '2061133806128433787': ('I', 68.46, 'holdout80'),
    '9132203293057663908': ('B', 68.46, 'holdout80'),
    '-1339001393414022254': ('R', 68.46, 'holdout80'),
    '-5807115486700258528': ('R', 68.46, 'holdout80'),
    '5091269515595356743': ('B', 68.46, 'holdout80'),
    '-5641036556244055425': ('R', 68.46, 'holdout80'),
    'aafd8c46f04dab19': ('R', 68.46, 'holdout80'),
    '38305b9d35eee845': ('B', 68.46, 'holdout80'),
    '4137375212558856405': ('B', 68.46, 'holdout80'),
    '-9016499412282617567': ('B', 68.46, 'holdout80'),
    '-2079184819421740199': ('B', 68.46, 'holdout80'),
    '-8681770316342114267': ('B', 68.46, 'holdout80'),
    '-6292419498113870576': ('R', 68.46, 'holdout80'),
    '-6745118248871820981': ('R', 68.46, 'holdout80'),
    '1419193582121409692': ('B', 68.46, 'holdout80'),
    '2796361438966931101': ('B', 68.46, 'holdout80'),
    '6603476971757908943': ('R', 68.46, 'holdout80'),
    '-2870824064883157232': ('R', 68.46, 'holdout80'),
    '-7949890167311822587': ('I', 68.46, 'holdout80'),
    '3884333056238093100': ('R', 68.46, 'holdout80'),
    '-1507425238494302547': ('R', 68.46, 'holdout80'),
    '-2563176391654051899': ('R', 68.46, 'holdout80'),
    '-4017021849587711766': ('R', 68.46, 'holdout80'),
    '-64224734801640497': ('R', 68.46, 'holdout80'),
    '2566870593686228973': ('R', 68.46, 'holdout80'),
    '-5240144696867909567': ('B', 68.46, 'holdout80'),
    '1560255229930671058': ('R', 68.46, 'holdout80'),
    '-9110015739753499511': ('R', 68.46, 'holdout80'),
    '-1348066655370765738': ('B', 68.46, 'holdout80'),
    '-8875604480376928396': ('B', 68.46, 'holdout80'),
    '-6334921007427870736': ('B', 68.46, 'holdout80'),
    '-3899228836098167046': ('B', 68.46, 'holdout80'),
    '6063336305708848177': ('R', 68.46, 'holdout80'),
    '2237275183478645813': ('R', 68.46, 'holdout80'),
    '7806688350964893805': ('B', 68.46, 'holdout80'),
    '7082066513903308631': ('B', 68.46, 'holdout80'),
    '-869561925845020494': ('B', 68.46, 'holdout80'),
    '7128150228690726682': ('B', 68.46, 'holdout80'),
    '-7623055969281105789': ('B', 68.46, 'holdout80'),
    '-5735276112522678799': ('R', 68.46, 'holdout80'),
}

if __name__ == "__main__":
    main()
