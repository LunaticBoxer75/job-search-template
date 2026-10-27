# Daily Job Search

Full pipeline: preflight → scrape → Pass 1 check → Pass 2 (Claude reads) → 4-panel report → pulse →
export → publish → health checks. Run every command from the repo root.

**Cadence markers:** `[REQUIRED]` every run. `[OPTIONAL]` run if asked or if something looks off.
`[WEEKLY]` too slow or too broad for a daily run — see the Weekly section; keep them out of the daily path.

**Interpreter.** Run everything inside the project's Python 3.12 venv, so `python` and `python3` are
the same interpreter and both have `jobspy`. Anything that imports `src.scraper`, `src.scorer`,
`src.quick_scorer`, `src.locations` or `src.reporter` needs `jobspy` installed.

**Home area.** The home area is set in `config/profile.json` → `scoring.home_area`. Its internal region
id in the database is `seattle` (stored rows use it; do not rename it). `national` is everything else.

**Keep this file current.** When a decision or code change affects the pipeline, update this file in
the same change.

---

## Standing mechanisms — do not reverse without asking the user

| Mechanism | What it means here |
|---|---|
| **Nothing under 70 is kept — including jobs Claude rated under 70** | Scraper skips <70 (`min_store_score`); export deletes every <70 row except tracked/dismissed jobs. Claude-rated ones go to `rejected_urls` (30 days) so the scraper doesn't re-add them |
| **Full-time first when `scoring.internships` = `fallback`** | Report full-time first; Pass 2 reads all FT in its bands, internships capped at 5 + 5. Internship-titled senior roles are capped |
| **Claude reads every home-area FT job at 70+ and every national FT job at 85+** — LinkedIn descriptions fetched first | Steps 3b + 4a; national 70-84 stays Pass-1-only |
| **Role priority comes from `scoring.role_families`** (points per family) | Encoded in Pass 1; mirror it in `pass2_rubric` for Pass 2 |
| **Technical PM cap** when `scoring.technical_pm_cap` is set | TPM / technical program-project-product manager titles capped at that score in Pass 1; say the same in `pass2_rubric` |
| **Home area first (own tab), then big US metros, then the rest** | `src/locations.py`; region comes from the job's location, not the search that found it |
| **Closed postings are removed**, not left at their old keyword score | Step 3b detects LinkedIn's expired redirect; step 7 deletes (tracked/dismissed kept) |
| **Dedup by posting URL only** — no content/title dedup (the same role in 3 offices shows 3 times) | Unique index on `job_url` |
| **90-day prune by posting date** (first-seen date if undated) | Export step |
| **Company size = curated tiers for the top employers** (JobSpy only gives Indeed counts) | `config/company_sizes.json` |
| **Skill-demand stats: whole words, Markdown un-escaped, full descriptions only** — substring counts inflate "Excel" ("excellent") | `src/skills_insights.py`, computed at export |
| **Never scrape** LinkedIn people search, Glassdoor, Blind, Levels.fyi (ToS) | Deep links only |

---

## 0. Preflight [REQUIRED]

**Where things live.** Three branches on the `github` remote, deliberately separate:

| Branch | Holds | Written by |
|---|---|---|
| `main` | Code + `content/` only | Normal reviewed commits |
| `user-data` | `data/user_data.json` — the candidate's applications, chats, to-dos, stories, mocks, dismissals, visit dates | Dashboard + local scripts, via the GitHub API |
| `jobs-data` | `data/jobs_deploy.db` — ONE commit, force-replaced each run | Step 8 |

The candidate's data is never on `main`, so nothing here can conflict with or clobber it.
`data/user_data.json` and `data/jobs_deploy.db` in the working tree are local copies only (gitignored).

**0a. The candidate's store must be reachable** — the export protects the jobs they applied to or
dismissed by reading it; without the token it falls back to a possibly-stale local snapshot. (In local
`MSCM_STORE_MODE=file` mode the local file is the store.)

```
python3 -c "from src import user_store as u; print('store:', 'REMOTE (user-data branch)' if u.is_remote() else 'LOCAL SNAPSHOT ONLY — add MSCM_GITHUB_TOKEN to .streamlit/secrets.toml'); print(u.stats())"
```

**0b. DB integrity** before anything writes to it:

```
sqlite3 "file:data/jobs.db?mode=ro" "PRAGMA quick_check;"
```

---

## 1. Scrape [REQUIRED]

Indeed + LinkedIn, 168h lookback (tolerates missed days). Searches = queries in
`config/search_queries.json` × 2 job types × the locations in `config/profile.json`
(`search_preferences.locations_home` + `locations_national`). This takes hours — run it in the
background and monitor it.

- Each search takes ~50s, almost all of it LinkedIn pausing 3-7s between its ~10 page requests
  (Indeed: <1s). Searches run in parallel (`scrape_workers` in `config/profile.json`
  search_preferences, default 3); every DB write stays on one thread.
- Every posting is **scored before it's stored**; under 70 it is skipped (`min_store_score`, default
  70). The progress line shows `+N new (M below 70 skipped so far)`.
- **Region comes from the job's location**, not the search that found it; **internship vs full-time
  comes from the title** (intern / internship / co-op / "<season> 20xx"). New-grad = full-time.
- A LinkedIn 429 pauses all workers 2 minutes and shows `N LinkedIn blocks`; if blocks keep coming,
  lower `scrape_workers`.
- It commits after every search and prints one `[done/total] … ~Nm left` line each, so a hung run is
  a log that stops advancing. "new" counts only rows actually inserted.

```
python -u -W ignore -c "
from src.scraper import scrape_all_jobs
jobs = scrape_all_jobs(sites=['indeed', 'linkedin'], hours_old=168)
print(f'Inserted {len(jobs)} new jobs')
" 2>&1 | tee /tmp/scrape.log
```

Per-search failures (use `;` not `&&` — `grep -c` exits 1 when the count is 0):

```
grep -c '\[!\]' /tmp/scrape.log; grep '\[!\]' /tmp/scrape.log | head
```

---

## 2. Pass 1 check — nothing left unscored [REQUIRED]

Rows are scored at insert, so this should find nothing; it is the safety net for the standing
invariant "no job is ever left unscored" (a NULL-score row is invisible everywhere).

```
python -c "
from src.reporter import get_stats
from src.quick_scorer import score_all_unscored
n = get_stats()['unscored_jobs']
print('unscored before:', n)
if n: print('rescued:', score_all_unscored()); print('unscored after:', get_stats()['unscored_jobs'])
assert get_stats()['unscored_jobs'] == 0, 'INVARIANT VIOLATED: unscored jobs remain'"
```

**If `src/quick_scorer.py`, `classify_job_type`, `src/locations.py` or the `scoring` block of
`config/profile.json` changed since the last run**, rescore everything so old and new rows share one
scale (one transaction; never touches Pass-2 rows; dry run without `--apply`; don't run it while a
scrape is writing):

```
cp data/jobs.db /tmp/jobs_pre_rescore.db && python scripts/rescore_all.py --apply
```

---

## 3. Run coverage by panel [REQUIRED]

Proves the run produced rows in all four panels. `STALE!` means the newest data isn't from today — this
run inserted nothing. A dead panel shows as an explicit `0`. Counts are post-filter (≥70 only).
(`seattle` = the home area.)

```
sqlite3 -header -column "file:data/jobs.db?mode=ro" "
WITH latest(d) AS (SELECT MAX(substr(scraped_at,1,10)) FROM jobs),
     panels(region, job_type) AS (VALUES ('seattle','fulltime'),('seattle','internship'),('national','fulltime'),('national','internship'))
SELECT (SELECT d FROM latest) AS run_date,
       CASE WHEN (SELECT d FROM latest) = date('now','localtime') THEN 'fresh' ELSE 'STALE!' END AS status,
       p.region, p.job_type,
       COUNT(j.job_id) AS new_rows,
       SUM(CASE WHEN j.source='indeed'   THEN 1 ELSE 0 END) AS indeed,
       SUM(CASE WHEN j.source='linkedin' THEN 1 ELSE 0 END) AS linkedin
FROM panels p
LEFT JOIN jobs j
       ON j.region = p.region AND j.job_type = p.job_type
      AND substr(j.scraped_at,1,10) = (SELECT d FROM latest)
GROUP BY p.region, p.job_type
ORDER BY p.region, p.job_type;"
```

A zero home-area panel is a real, recurring failure. Report it; the `[!]` lines from step 1 are the
diagnostic.

---

## 3b. Fetch LinkedIn descriptions — home area 70+, national 85+ [REQUIRED, ~10 min]

LinkedIn search results have no description, so without this Claude can't read most of either FT
panel. One request per posting with a 0.6-1.2s pause. **Closed postings** (LinkedIn redirects them
with `expired_jd_redirect`) go in `expired_postings` and the export deletes them — otherwise closed
jobs keep their keyword score and sit unread at the top of the home-area tab. Other empty responses go
in `linkedin_fetch_misses` and are skipped next time. After 8 empties in a row it fetches a known-live
posting: if that loads, it carries on (expired postings); if not, it stops (LinkedIn is blocking).
Fetched rows are rescored with Pass 1 so its years/clearance/sponsorship rules can fire. Run four
shards side by side (`--shard K/N` splits the queue with no overlap; ≈1.6-2 s per posting each):

```
for r in seattle:70 national:85; do for k in 0 1; do
  python -u -W ignore scripts/backfill_linkedin_descriptions.py --limit 300 --region ${r%:*} \
    --min-score ${r#*:} --shard $k/2 > /tmp/bf_${r%:*}_$k.log 2>&1 &
done; done; wait; tail -n 1 /tmp/bf_*.log
```

Don't run Pass-1 rescoring (`rescore_all.py`) while these run — it would overwrite freshly fetched rows.

Expect ~85-90% success on fresh postings. If one stops for a block, carry on — Pass 2 reads fewer.

---

## 4. Pass 2 — Claude reads the descriptions [REQUIRED]

Pass 1 is keyword rules; Pass 2 is reading the actual posting. It matters: many jobs Pass 1 scores in
the 80s fall well below 75 once read, and full-text reading catches disqualifiers Pass 1 can't see
(e.g. "no sponsorship, won't sign OPT/CPT" in the legal section). There is no API key: **you** (via
subagents) are the scorer.

**4a. Select candidates** — has a description, not already read, per-panel floor and cap. Read-only.
**Read every home-area FT job at 70+ and every national FT job at 85+.** Caps are backlog guards, not
the plan. Not limited to the latest scrape day, so anything a previous run missed is picked up.

```
python3 - <<'PY'
import sqlite3, json, os
conn = sqlite3.connect("file:data/jobs.db?mode=ro", uri=True); conn.row_factory = sqlite3.Row
# Caps favour full-time: Pass 2 is the scarce resource. 'seattle' = the home area.
PANELS = [("seattle_ft","region='seattle' AND job_type!='internship'",70,600),
          ("seattle_intern","region='seattle' AND job_type='internship'",70,5),
          ("national_ft","region='national' AND job_type!='internship'",85,400),
          ("national_intern","region='national' AND job_type='internship'",75,5)]
out = []
for name, where, floor, cap in PANELS:
    rows = conn.execute(f"""SELECT job_id,title,company,location,job_type,salary_min,salary_max,description
        FROM jobs WHERE {where} AND score>=? AND LENGTH(description)>200
          AND json_extract(score_breakdown,'$.scorer') IS NULL
          AND COALESCE(date_posted, substr(scraped_at,1,10)) >= date('now','-90 days')
        ORDER BY score DESC LIMIT ?""", (floor, cap)).fetchall()
    print(f"{name}: candidates={len(rows)}{'  (HIT CAP — backlog; say so)' if len(rows) == cap else ''}")
    out += [dict(r) for r in rows]
json.dump(out, open("/tmp/pass2_candidates.json","w"))
print("total", len(out), "| bytes", os.path.getsize("/tmp/pass2_candidates.json"))
PY
```

A panel short of its daily arrivals is almost always LinkedIn rows whose fetch failed (no
description) — say so; those keep their Pass-1 score and were never read. Never present them as
AI-scored. National 70-84 is NOT read (ranked by Pass 1 only) — the report must say so.

**4b. Build the prompts** — 12 jobs per batch. The builder shows the whole stored description (up to
10,000 chars) so requirement and legal sections are visible. Clear stale files first:

```
rm -f /tmp/pass2_batch*.txt /tmp/pass2_scores*.json && python -c "
import json
from src.scorer import batch_score_prompt
jobs = json.load(open('/tmp/pass2_candidates.json'))
for i in range(0, len(jobs), 12):
    path = f'/tmp/pass2_batch{i//12+1:02d}.txt'
    open(path,'w').write(batch_score_prompt(jobs[i:i+12]))
    print(path, len(jobs[i:i+12]), 'jobs')
"
```

**4c. Score them with parallel subagents.** Give each agent ≤ 3 batch files and have it write
`/tmp/pass2_scores_partN.json`: a JSON array, one entry per job —
`{"job_id", "score" (int 0-100), "reasoning", "highlights", "red_flags"}` — then verify with python3
that it parses, the count matches the `### Job` headers, and every job_id appears exactly once. The
rubric is inside each batch file (`src/scorer.py` `batch_score_prompt` + `pass2_rubric` in
`config/profile.json`): weighted dimensions PLUS hard rules that override them. Typical hard rules:

- when the candidate needs sponsorship: U.S. citizen / security clearance / "U.S. person" / ITAR
  required → very low; won't sponsor, or won't support STEM OPT / no E-Verify → capped
- REQUIRED years far above the candidate's (`experience_summary.years`) → capped
- roles outside the candidate's field that only share a title word → very low
- technical PM roles → capped at `scoring.technical_pm_cap` when it is set
- flag a start date ONLY when the posting states one — never inferred

Tell agents to touch no database and no other file. Then merge and validate (this writes
`/tmp/pass2_scores.json`, which 4d reads):

```
python3 - <<'PY'
import json, glob, collections
cands = {c["job_id"] for c in json.load(open("/tmp/pass2_candidates.json"))}
allsc = [e for f in sorted(glob.glob("/tmp/pass2_scores_part*.json")) for e in json.load(open(f))]
ids = collections.Counter(str(e["job_id"]) for e in allsc)
problems = {"missing": cands - set(ids), "extra": set(ids) - cands,
            "duplicate": {i for i, n in ids.items() if n > 1},
            "bad_score": {e["job_id"] for e in allsc if not isinstance(e.get("score"), (int, float)) or not 0 <= e["score"] <= 100}}
assert not any(problems.values()), problems
json.dump(allsc, open("/tmp/pass2_scores.json", "w")); print(f"OK {len(allsc)} scores → /tmp/pass2_scores.json")
PY
```

**4d. Write back — one transaction, one connection.** Commits the good rows FIRST, then reports
mismatches (reporting inside the transaction would roll back the batch over one bad id).

```
python3 - <<'PY'
import sqlite3, json, datetime, sys
ai = json.load(open("/tmp/pass2_scores.json"))
assert isinstance(ai, list) and ai, "pass2_scores.json must be a non-empty JSON array"
now = datetime.datetime.now().isoformat()
rows = [(e["score"], json.dumps({**e, "scorer": "ai"}), now, str(e["job_id"])) for e in ai]
conn = sqlite3.connect("data/jobs.db", timeout=60)
conn.execute("PRAGMA busy_timeout=60000")
try:
    with conn:
        written = conn.executemany(
            "UPDATE jobs SET score=?, score_breakdown=?, scored_at=? WHERE job_id=?", rows).rowcount
    if written != len(rows):
        missing = [r[3] for r in rows if not conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (r[3],)).fetchone()]
        print(f"WARN wrote {written}/{len(rows)}; unknown job_ids: {missing}"); sys.exit(1)
    print(f"OK wrote {written}/{len(rows)} scores")
finally:
    conn.close()
PY
```

The `"scorer": "ai"` stamp makes 4a idempotent and protects these rows from the <70 cleanup and from
`rescore_all.py`.

**4e. Verify.** Every job_id in `/tmp/pass2_candidates.json` must now be stamped `scorer=ai`. (Re-running 4a
can still show candidates: panels often hold more than the per-run cap, so the next ones move up —
that's expected, not a failure.)

---

## 5. 4-panel report [REQUIRED]

Same thresholds the dashboard publishes, so nothing appears here that the candidate can't find there.

```
python -W ignore - <<'PY'
import sqlite3, sys
sys.path.insert(0, ".")
from src import user_store
from src.locations import metro_of
from src.profile import home_label
dismissed = {d.get("job_id") for d in user_store.get("dismissed_jobs") if d.get("job_id")}
conn = sqlite3.connect("file:data/jobs.db?mode=ro", uri=True)
PANELS = [(f"{home_label().upper()} - FULL-TIME (top priority)", "region='seattle' AND job_type!='internship'", 70, 12),
          ("BIG CITIES & REST OF USA - FULL-TIME",              "region='national' AND job_type!='internship'", 75, 12),
          (f"{home_label().upper()} - INTERNSHIPS",              "region='seattle' AND job_type='internship'",  70, 5),
          ("USA - INTERNSHIPS",                                  "region='national' AND job_type='internship'",  75, 5)]
for name, where, floor, lim in PANELS:
    rows = conn.execute(f"""SELECT job_id, score, title, company, location, salary_min, salary_max, job_url,
               json_extract(score_breakdown,'$.scorer') AS scorer
        FROM jobs WHERE {where} AND score>=?
        ORDER BY score DESC, scraped_at DESC LIMIT ?""", (floor, lim + len(dismissed))).fetchall()
    rows = [r for r in rows if r[0] not in dismissed][:lim]
    print(f"\n=== {name} ({len(rows)}) ===")
    for jid, sc, t, c, loc, smin, smax, url, scorer in rows:
        sal = f" | ${int(smin/1000)}K-${int(smax/1000)}K" if smin and smax else ""
        metro = f" [{metro_of(loc)}]" if metro_of(loc) else ""
        tag = "" if scorer == "ai" else "  (quick score only)"
        print(f"  [{sc:.0f}] {t} @ {c} - {loc}{metro}{sal}{tag}\n      {url}")
conn.close()
PY
```

Present it in this order: **both full-time panels first (home area, then big cities & rest of USA),
then the two internship panels** (labelled fallback when `scoring.internships` is `fallback`). Lead with
full-time even if an internship scores highest. Keep the `(quick score only)` marker — it's the
difference between "Claude read this" and "a keyword matched."

---

## 6. The candidate's pulse — follow-ups, to-dos, prep [REQUIRED, ~2s]

```
python -W ignore -c "
from src.tracker import get_follow_ups_due
rows = get_follow_ups_due()
if not rows:
    print('  No follow-ups due.')
for f in rows:
    print(f\"  [{f['follow_up_date']}] {f['title']} @ {f['company']}\" + (f\" - {f['notes']}\" if f.get('notes') else '') + (f\"\n      {f['job_url']}\" if f.get('job_url') else ''))
"
```

```
python3 -c "from src.todos import get_todos
for t in get_todos('open'):
    if t.get('priority') == 'high': print('  HIGH:', t['item'])"
```

```
python3 -c "from src.interview_prep import story_coverage
c = story_coverage(); print(f\"STAR themes covered {len(c['covered'])}/{len(c['covered'])+len(c['missing'])}\"); print('  missing:', ', '.join(c['missing'][:6]))" 2>/dev/null
```

---

## 7. Export the deploy DB [REQUIRED]

Builds `data/jobs_deploy.db` from jobs scoring 70+ (full-time and internships alike), and computes
**skill demand from the FULL descriptions** into its `insights` table — the Skills Gap and Skills
Insights pages display that (the deploy copy's descriptions are truncated to 800 chars). **Destructive on
`data/jobs.db`, the only full copy.**

Jobs the candidate has applied to always ship to the dashboard regardless of score, so their Tracker
rows keep their details.

1. **90-day prune** — postings whose posting date (first-seen if undated) is over 90 days old.
2. **Under-70 cleanup** — every row scoring below 70, including ones Claude read, EXCEPT jobs the
   candidate applied to or dismissed. Claude-rated ones are first recorded in `rejected_urls`, which
   the scraper skips, so they can't come back and be re-read (entries expire after 30 days).
3. **Closed postings** — rows in `expired_postings` (found in step 3b), same protection; URLs go to
   `rejected_urls` as "closed posting".

All three protect tracked/dismissed job_ids via `user_store`; with no token and no local snapshot the
script refuses to prune. Report the counts it prints. Before the first run after a big scoring change,
back up: `cp data/jobs.db /tmp/jobs_pre_export.db`.

```
python scripts/export_deploy_db.py
```

---

## 8. Publish [REQUIRED when hosted]

Publishes the deploy DB to the `jobs-data` branch of the `github` remote as a single, history-free
commit. The dashboard notices within ~10 minutes and swaps it in — no redeploy, no push to `main`.
Skip this step in a local-only setup.

```
python scripts/publish_jobs_db.py
```

It force-pushes `refs/heads/jobs-data` ONLY. Never force-push `main` or `user-data`. It refuses files
over 95MB. `--dry-run` builds the commit without pushing. Push `main` only when code or `content/`
changed, as a normal reviewed commit.

---

## Health checks

Run all after step 8. Any failure is a silent-failure alarm — report it.

```
python -c "
from src.reporter import get_stats
s = get_stats(); print(s)
assert s['unscored_jobs'] == 0, 'unscored jobs remain'"
```

Only protected rows below 70 (Pass-2 reads and tracked/dismissed jobs) should remain:

```
sqlite3 "file:data/jobs.db?mode=ro" "SELECT COUNT(*) AS unprotected_below_70 FROM jobs WHERE score < 70 AND COALESCE(json_extract(score_breakdown,'\$.scorer'),'') != 'ai';"
```

(A small non-zero count is tracked/dismissed jobs; thousands means the cleanup didn't run.)

```
sqlite3 "file:data/jobs.db?mode=ro" "SELECT COUNT(*)-COUNT(DISTINCT job_url) AS duplicate_urls FROM jobs WHERE job_url!='';"
```

(Should be 0 — the unique URL index enforces it; if not, `python3 scripts/dedupe_jobs.py --apply`.)

```
python3 -c "from src import prep_content; bad={k:v for k,v in prep_content.status().items() if not isinstance(v,int) or v==0}; print('CONTENT UNHEALTHY:',bad) if bad else print('content OK:',sum(prep_content.status().values()),'rows'); __import__('sys').exit(1 if bad else 0)"
```

```
python3 -c "from src import user_store; s=user_store.stats(); print(s); assert sum(s.values())>0, 'store read as empty — check MSCM_GITHUB_TOKEN / the user-data branch'"
```

(On a brand-new setup the store is legitimately empty; the assert only matters once the candidate has
saved something.)

Every dashboard page renders (headless; forces MSCM_STORE_MODE=file, never touches GitHub):

```
python scripts/smoke_test_pages.py
```

Then summarize for the candidate: new rows inserted (and skipped below 70), Pass-2 jobs actually read,
rows exported, prune + cleanup counts, deploy DB size.

---

## Failure modes

- **`database is locked`** — another writer holds the DB (a scrape, the dashboard, a rescore). The
  scraper's own connection gives up after 5s, so never run `rescore_all.py` or the export while a
  scrape is writing. Writebacks set `busy_timeout=60000`. A `mode=ro` read failing with **error 14
  "unable to open database file"** usually isn't transient: once the last writer closes, SQLite removes
  `jobs.db-shm`, and a read-only connection can't recreate it. Open it once read-write —
  `sqlite3 data/jobs.db "PRAGMA quick_check;"` — and read-only works again. Never use `immutable=1`
  (torn reads).
- **`database disk image is malformed`** — stop. Don't export or publish. Run
  `sqlite3 "file:data/jobs.db?mode=ro" "PRAGMA integrity_check;"`, copy the file aside, recover from
  the last good state (backups: `/tmp/jobs_pre_*.db`).
- **"Saving is paused" banner / `StoreError`** — GitHub unreachable or the token expired. The dashboard
  serves the last-good data read-only and refuses writes rather than dropping them. Check the token's
  expiry.
- **`jobs-data` publish rejected** — usually auth. Never "fix" it by committing the DB to `main`.
- **Scrape hangs** — the `[done/total]` log stops advancing. Everything up to the last finished search
  is committed. Kill it (`pkill -f scrape_all_jobs`), report where it stopped, re-run step 1.
- **LinkedIn blocks keep coming** (`N LinkedIn blocks` rising) — lower `scrape_workers` to 2.
- **A panel reports 0 Pass-2 candidates** — almost always missing descriptions. Say which panel and why.

---

## Weekly / optional — keep OUT of the daily path

- **[WEEKLY / after scoring changes] Pass-2 backfill of the existing top.** Also read unread full-time
  jobs across all days — home area ≥80 and national ≥85 with descriptions (run step 3b with a higher
  `--limit` first). Same 4b-4d flow.
- **[WEEKLY] Wider LinkedIn description fetch:** `scripts/backfill_linkedin_descriptions.py --region all
  --limit 400` (national too). ~1.6s per posting.
- **[WHEN RULES CHANGE] Evaluate the scorer:** `python scripts/eval_quick_scorer.py` (read-only;
  compares against its hand-labelled sample — relabel the sample for a new candidate, and spot-check
  big movers yourself). Then `scripts/rescore_all.py --apply` (step 2).
- **[QUARTERLY] Company sizes:** rebuild `config/company_sizes.json` for the current top employers
  (tiers, parent roll-up, staffing flag; verify the large ones).
- **[WEEKLY] H1B sponsor staleness (nag, don't auto-download):**
  `python3 -c "import json; d=json.load(open('config/h1b_sponsors_verified.json')); print(d['fiscal_year'])"`
- **[OPTIONAL] Markdown report:** `python -c "from src.reporter import generate_daily_report; generate_daily_report(min_score=70, limit=50)"`
- **[OPTIONAL] Tracker report:** `python -c "from src.tracker import generate_tracker_report; print(generate_tracker_report())"`
- **[OPTIONAL] Skills demand, printed** (the export already stores it for the dashboard):
  `python3 -c "from src.skills_insights import analyze_skills; r=analyze_skills(min_score=70, db_path='data/jobs.db'); print(r['total'], [(x['skill'], x['pct']) for x in r['technical'][:6]])"`
- **[OPTIONAL] Import coffee-chat action items into to-dos:**
  `python3 -c "from src.todos import import_coffee_chat_actions; print('imported', import_coffee_chat_actions())"`

---

## Known gaps — do not claim these work

- **LinkedIn rows have no description** unless step 3b fetched it — Pass 2 can't read them and Pass 1's
  description rules (years, clearance, sponsorship) can't fire. Marked `(quick score only)`.
- **Company size covers only the employers in `config/company_sizes.json`**; everyone else shows no
  size. Staffing agencies show "(via agency)", not a size.
- **The same role posted in several offices appears several times** — by decision (no content dedup).
- **`config/profile.json`'s `distance_miles`, `min_salary`, `exclude_terms` are read by nothing.**
- **Start dates** are flagged by the rubric only where a posting states a date.

---

## Key Files

- Candidate profile, scoring rules, search prefs (`min_store_score`, `scrape_workers`, locations):
  `config/profile.json`
- Searches: `config/search_queries.json` · Company sizes: `config/company_sizes.json`
- **Jobs (local, full):** `data/jobs.db` — gitignored; one row per posting URL; only 70+ kept
- **Dashboard jobs:** `data/jobs_deploy.db` → `jobs-data` branch (`scripts/publish_jobs_db.py`),
  downloaded by the app (`src/jobs_data.py`)
- **The candidate's data:** `user-data` branch `data/user_data.json` via `src/user_store.py`
- Pass 1: `src/quick_scorer.py` · Pass 2 rubric: `src/scorer.py` `batch_score_prompt` + `pass2_rubric`
- Where a job is: `src/locations.py` · Company size: `src/company_size.py`
- Scraper: `src/scraper.py` (`classify_job_type`, parallel fetch) · Export: `scripts/export_deploy_db.py`
- One-off/maintenance: `scripts/rescore_all.py`, `scripts/dedupe_jobs.py`, `scripts/eval_quick_scorer.py`,
  `scripts/smoke_test_pages.py`
- Prep content: `content/*.json` via `src/prep_content.py` · Dashboard: `app.py`
- Last-run artifacts: `/tmp/scrape.log`, `/tmp/pass2_candidates.json`, `/tmp/pass2_batch*.txt`,
  `/tmp/pass2_scores*.json`
