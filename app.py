"""Job Search Dashboard — Streamlit web UI.

Two data sources, deliberately disjoint:
  data/jobs_deploy.db  — scraped jobs. Read-only here; refreshed by /find-jobs.
  data/user_data.json  — her data, via src/user_store.py. Read-write here.

They must stay separate. Her data used to live in the deploy DB, so every
/find-jobs push replaced it and destroyed whatever she'd typed on the hosted
dashboard (Streamlit Cloud's filesystem is ephemeral).
"""

import html
import json
import re
import sqlite3
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from src import company_size, jobs_data, locations, profile, user_store

PROJECTS_DIR = Path(__file__).parent / "projects"
DB_PATH = Path(__file__).parent / "data" / "jobs_deploy.db"

_ID = profile.identity()
NAME, HOME = profile.first_name(), _ID["home_city"]
st.set_page_config(page_title=_ID["app_title"], page_icon="🎯", layout="wide")


def get_db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def dismissed_ids():
    """Job IDs she has dismissed. Lives in user_store so dismissals survive a deploy."""
    return {d["job_id"] for d in user_store.get("dismissed_jobs") if d.get("job_id")}


@st.cache_data(ttl=60)
def _load_jobs_raw(min_score):
    conn = get_db()
    df = pd.read_sql_query("""
        SELECT * FROM jobs
        WHERE score IS NOT NULL AND score >= ?
        AND title IS NOT NULL AND title != ''
        AND company IS NOT NULL AND company != ''
        ORDER BY score DESC
    """, conn, params=(min_score,))
    conn.close()
    # No early return when empty: pages index the derived columns below.
    # Claude read this job (Pass 2) vs keyword score only (Pass 1)
    df["ai_read"] = df["score_breakdown"].fillna("").str.contains(r'"scorer"\s*:\s*"ai"', regex=True)
    # Posting date when real, else first-seen date
    posted = df["date_posted"].where(df["date_posted"].astype(str).str.match(r"^\d{4}-\d{2}-\d{2}"), None)
    df["eff_posted"] = posted.fillna(df["scraped_at"].astype(str).str[:10])
    # Big-metro name for Rest-of-USA jobs (home-area jobs have their own tab)
    metros = {l: locations.metro_of(l) for l in df["location"].fillna("").unique()}
    df["metro"] = df["location"].fillna("").map(metros)
    df.loc[df["is_remote"].fillna(0).astype(bool) & df["metro"].isna(), "metro"] = "Remote"
    # Company size tier (curated table of the ~500 most frequent employers)
    sizes = {c: company_size.lookup(c) for c in df["company"].unique()}
    df["company_tier"] = df["company"].map(lambda c: sizes[c]["tier"])
    df["company_employees"] = df["company"].map(lambda c: sizes[c]["employees"])
    df["company_staffing"] = df["company"].map(lambda c: sizes[c]["staffing"])
    # Thousands of jobs share a score; within a tie, Claude-read first, then newest.
    # (Without this, ties came back in arbitrary order — effectively alphabetical.)
    return df.sort_values(["score", "ai_read", "eff_posted"], ascending=False,
                          kind="stable").reset_index(drop=True)


def load_jobs(min_score=50):
    """Scored jobs, with dismissals filtered out at read time."""
    df = _load_jobs_raw(min_score)
    dropped = dismissed_ids()
    if dropped and not df.empty:
        df = df[~df["job_id"].isin(dropped)]
    return df


def applications_map():
    """job_id -> application record."""
    return {a["job_id"]: a for a in user_store.get("applications") if a.get("job_id")}


def load_applications():
    """Her applications, enriched with job details from the deploy DB."""
    apps = user_store.get("applications")
    if not apps:
        return pd.DataFrame()
    ids = [a["job_id"] for a in apps if a.get("job_id")]
    if not ids:
        return pd.DataFrame()
    conn = get_db()
    jobs = pd.read_sql_query(
        f"""SELECT job_id, title, company, location, score, job_url FROM jobs
            WHERE job_id IN ({",".join("?" * len(ids))})""",
        conn, params=ids,
    )
    conn.close()
    df = pd.DataFrame(apps)
    snap = df.copy()  # her record, including any saved job details
    df = df.drop(columns=[c for c in ("title", "company", "location", "job_url") if c in df.columns]).merge(
        jobs, on="job_id", how="left")
    # A tracked job can leave the published DB (e.g. rescored below 70); use the copy
    # saved on her application record so the row never goes blank.
    for col in ("title", "company", "location", "job_url"):
        if col in snap.columns:
            df[col] = df[col].fillna(snap[col])
    order = {"interviewing": 1, "applied": 2, "offer": 3}
    df["_rank"] = df["status"].map(lambda s: order.get(s, 4))
    return df.sort_values(["_rank", "score"], ascending=[True, False]).drop(columns=["_rank"])


def dismiss_job(job_id):
    if job_id in dismissed_ids():
        return
    user_store.add("dismissed_jobs", {"job_id": job_id}, message="Job dismissed")
    _load_jobs_raw.clear()


def update_application(job_id, status, notes=None, follow_up_date=None):
    existing = applications_map().get(job_id)
    # Auto-set 7-day follow-up when applying — unless one is already queued
    if status == "applied" and not follow_up_date and not (existing or {}).get("follow_up_date"):
        follow_up_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
    # None means "leave unchanged": moving to 'interviewing' must not erase her
    # notes or the queued follow-up.
    fields = {"status": status}
    if notes is not None:
        fields["notes"] = notes
    if follow_up_date is not None:
        fields["follow_up_date"] = follow_up_date
    if not (existing or {}).get("title"):
        # Keep a copy of the job on her record — the posting may later leave the DB.
        conn = get_db()
        row = conn.execute("SELECT title, company, location, job_url FROM jobs WHERE job_id = ?",
                           (job_id,)).fetchone()
        conn.close()
        if row:
            fields.update(title=row[0], company=row[1], location=row[2], job_url=row[3])
    if status == "applied" and not (existing or {}).get("applied_date"):
        fields["applied_date"] = datetime.now().isoformat()
    if existing:
        user_store.update("applications", existing["id"], fields,
                          message=f"Marked {status}")
    else:
        user_store.add("applications", {"job_id": job_id, **fields},
                       message=f"Marked {status}")


def get_application_status(job_id):
    """Current application status for a job, or None."""
    app = applications_map().get(job_id)
    return app.get("status") if app else None


def get_todays_top_jobs(limit=5):
    """Composite priority: score × freshness × not-applied."""
    df = load_jobs(min_score=70)
    if df.empty:
        return df
    acted = {jid for jid, a in applications_map().items() if a.get("status") not in (None, "new")}
    if acted:
        df = df[~df["job_id"].isin(acted)]
    return df.head(limit)  # load_jobs already ranks: score, Claude-read, newest


def get_comparable_jobs(current_job, limit=3):
    """Find similar roles to the currently selected job."""
    conn = get_db()
    title = str(current_job.get("title", "") or "")
    job_id = current_job.get("job_id", "")

    # Extract key tokens (skip generic words)
    skip_words = {"sr.", "sr", "senior", "jr.", "junior", "ii", "iii", "i", "the", "of", "and", "for", "in"}
    tokens = [t for t in title.lower().split() if t not in skip_words and len(t) > 2][:3]

    if not tokens:
        return pd.DataFrame()

    # Build LIKE clauses for each token
    where_clauses = " AND ".join([f"LOWER(title) LIKE ?" for _ in tokens])
    params = [f"%{t}%" for t in tokens]
    params.append(job_id)
    # Over-fetch so dismissed rows can be dropped below and still leave `limit`
    params.append(limit + len(dismissed_ids()))

    try:
        df = pd.read_sql_query(f"""
            SELECT job_id, title, company, location, score, salary_min, salary_max, job_url
            FROM jobs
            WHERE {where_clauses}
            AND job_id != ?
            AND score >= 60
            ORDER BY score DESC
            LIMIT ?
        """, conn, params=params)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    dropped = dismissed_ids()
    if dropped and not df.empty:
        df = df[~df["job_id"].isin(dropped)]
    return df.head(limit)


# Per-theme colors for the custom styles below. Streamlit's own widgets follow
# .streamlit/config.toml; these follow whichever mode is active (st.context.theme).
PALETTES = {
    "light": {"card": "#F6F4FB", "border": "#E7E3F1", "muted": "#6B7280", "title": "#111827",
              "pill-bg": "#F3F4F6", "pill": "#374151", "accent-bg": "#EDE9FE", "accent": "#4B2E83",
              "good-bg": "#DCFCE7", "good": "#166534", "warn-bg": "#FEF3C7", "warn": "#92400E",
              "pay-bg": "#ECFDF5", "pay": "#047857"},
    "dark": {"card": "#1B1A26", "border": "#2E2B40", "muted": "#A1A1AA", "title": "#F3F4F6",
             "pill-bg": "#262436", "pill": "#D1D5DB", "accent-bg": "#2E2450", "accent": "#C4B5FD",
             "good-bg": "#0F2E1B", "good": "#86EFAC", "warn-bg": "#3A2A0A", "warn": "#FCD34D",
             "pay-bg": "#0F2A22", "pay": "#6EE7B7"},
}


def theme_vars():
    kind = getattr(getattr(st.context, "theme", None), "type", None)
    pal = PALETTES["dark" if kind == "dark" else "light"]
    return "<style>:root {" + "".join(f"--m-{k}:{v};" for k, v in pal.items()) + "}</style>"


CSS = """
<style>
/* tighter page top; readable max width */
.block-container {padding-top: 3.6rem; padding-bottom: 3rem; max-width: 1180px;}  /* clears Streamlit's toolbar */
/* slim brand bar replaces the big repeated title */
.brand {display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.4rem;
        padding:.55rem .9rem; margin-bottom:.6rem; border-radius:.7rem;
        background:linear-gradient(90deg,#4B2E83 0%,#6B4FA6 100%); color:#fff; font-size:.92rem;}
.brand b {font-weight:700; letter-spacing:.2px;}
.brand .fresh {background:rgba(255,255,255,.16); padding:.12rem .55rem; border-radius:999px; font-size:.8rem;}
.brand .fresh.stale {background:#FEF3C7; color:#92400E;}
/* metric cards */
[data-testid="stMetric"] {background:var(--m-card); border:1px solid var(--m-border); border-radius:.7rem; padding:.55rem .8rem;}
[data-testid="stMetricLabel"] p {font-size:.8rem; color:var(--m-muted);}
/* job cards */
.job {display:flex; gap:.75rem; align-items:flex-start; padding:.45rem 0;}
.job-main {min-width:0;}
.job-title {font-weight:650; font-size:.95rem; line-height:1.3; color:var(--m-title);}
.job-meta {font-size:.8rem; color:var(--m-muted); margin-top:.2rem; line-height:1.7;}
.score {flex:0 0 auto; min-width:2.35rem; text-align:center; font-weight:700; font-size:.85rem;
        padding:.3rem .35rem; border-radius:.55rem; margin-top:.05rem;}
.score.s90 {background:var(--m-good-bg); color:var(--m-good);}
.score.s80 {background:var(--m-accent-bg); color:var(--m-accent);}
.score.s70 {background:var(--m-warn-bg); color:var(--m-warn);}
.score.slow {background:var(--m-pill-bg); color:var(--m-muted);}
.pill {display:inline-block; font-size:.72rem; padding:.05rem .45rem; margin-left:.3rem; border-radius:999px;
       background:var(--m-pill-bg); color:var(--m-pill); white-space:nowrap;}
.pill.ai {background:var(--m-accent-bg); color:var(--m-accent);}
.pill.pay {background:var(--m-pay-bg); color:var(--m-pay);}
.pill.warn {background:var(--m-warn-bg); color:var(--m-warn);}
.pill.done {background:var(--m-good-bg); color:var(--m-good);}
.open-link {font-size:.82rem; font-weight:600; padding:.25rem .6rem; border:1px solid var(--m-border);
            border-radius:999px; white-space:nowrap;}
.open-link:hover {background:var(--m-card);}
/* compact action buttons on job cards */
[class*="st-key-ok_"] button, [class*="st-key-x_"] button, [class*="st-key-sel_"] button {
    min-height:2rem; padding:.1rem .55rem; font-size:.85rem;}
[class*="st-key-sel_"] button p {white-space:nowrap;}
[class*="st-key-x_"] button:hover {border-color:#DC2626; color:#DC2626;}
[class*="st-key-ok_"] button:hover {border-color:#16A34A; color:#16A34A;}
/* phones: metrics two per row, and job cards keep their actions on the same row */
@media (max-width: 640px) {
  [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) {flex-wrap:wrap !important; gap:.5rem;}
  [data-testid="stHorizontalBlock"]:has([data-testid="stMetric"]) > div {
      flex:1 1 calc(50% - .5rem) !important; min-width:calc(50% - .5rem) !important;}
  [data-testid="stHorizontalBlock"]:has(.job) {flex-wrap:nowrap !important; gap:.3rem;}
  [data-testid="stHorizontalBlock"]:has(.job) > div:first-child {flex:1 1 auto !important; min-width:0 !important;}
  [data-testid="stHorizontalBlock"]:has(.job) > div:not(:first-child) {flex:0 0 auto !important; width:auto !important; min-width:0 !important;}
  .open-link {padding:.2rem .45rem;}
}
</style>
"""


# --- SHARED UI HELPERS ---

# Save confirmations: user_store calls this after every successful write. Queued
# in session state because most saves are followed by st.rerun(), which would
# drop a toast shown immediately.
_QUIET = ("Seed ", "Record ", "Import ")  # background writes she didn't trigger


def _queue_toast(message):
    if message and not message.startswith(_QUIET):
        st.session_state.setdefault("_toasts", []).append(message)


def flush_toasts():
    for m in st.session_state.pop("_toasts", []):
        st.toast(f"Saved — {m}", icon="✅")


user_store.on_write = _queue_toast


@st.cache_data(ttl=600, show_spinner=False)
def jobs_updated_date():
    """Date of the newest scrape in the published jobs DB."""
    conn = get_db()
    try:
        v = conn.execute("SELECT MAX(scraped_at) FROM jobs").fetchone()[0]
    except sqlite3.Error:
        v = None
    conn.close()
    return str(v)[:10] if v else None


def visit_baseline():
    """Her previous visit day, for "new since your last visit".

    Written at most once per day (one small commit). Caveat: opening the
    dashboard locally with the token set counts as her visit too.
    """
    if "visit_baseline" in st.session_state:
        return st.session_state["visit_baseline"]
    today = date.today().isoformat()
    rec = next((m for m in user_store.get("meta") if m.get("key") == "visits"), None)
    baseline = None
    try:
        if rec is None:
            user_store.add("meta", {"key": "visits", "last": today, "prev": None},
                           message="Record first visit")
        elif rec.get("last") != today:
            baseline = rec.get("last")
            user_store.update("meta", rec["id"], {"prev": baseline, "last": today},
                              message="Record visit")
        else:
            baseline = rec.get("prev")
    except user_store.StoreError:
        baseline = (rec or {}).get("prev")  # read-only mode: still show counts
    st.session_state["visit_baseline"] = baseline
    return baseline


def save(fn, *args, **kwargs):
    """Run a write to her store; on failure show why instead of a traceback. Returns success."""
    try:
        fn(*args, **kwargs)
        return True
    except user_store.StoreError as e:
        st.error(f"Couldn't save — {e}. Nothing was changed; try again in a moment.")
        return False


def _safe_url(u):
    """Only http(s) links — a scraped `javascript:` URL would run on click."""
    u = str(u or "")
    return u if u.startswith(("http://", "https://")) else ""


def _pill(text, kind=""):
    return f"<span class='pill {kind}'>{html.escape(str(text))}</span>"


def _score_class(score):
    return "s90" if score >= 90 else "s80" if score >= 80 else "s70" if score >= 70 else "slow"


def job_row(row, key, apps, selectable=True):
    """One job as a card with inline actions. Shared by the Today and Jobs pages.

    Scraped text is HTML-escaped: it's rendered with unsafe_allow_html.
    "Open" never tracks anything; only ✓ records an application.
    """
    job_id = row.get("job_id", "")
    score = float(row.get("score") or 0)
    status = (apps.get(job_id) or {}).get("status")

    meta = [html.escape(str(row.get("company", ""))), html.escape(str(row.get("location", "")))]
    pills = []
    if row.get("ai_read"):
        pills.append(_pill("🤖 Claude read", "ai"))
    if row.get("company_staffing"):
        pills.append(_pill("via agency", "warn"))
    elif row.get("company_tier") not in (None, "unknown") and isinstance(row.get("company_tier"), str):
        pills.append(_pill(f"🏢 {row['company_tier']}"))
    m = row.get("metro")
    if row.get("region") != "seattle" and isinstance(m, str) and m:
        pills.append(_pill(f"📍 {m}"))
    lo, hi = row.get("salary_min"), row.get("salary_max")
    if pd.notna(lo):
        pills.append(_pill(f"${int(lo/1000)}K–${int(hi/1000)}K" if pd.notna(hi) else f"${int(lo/1000)}K+", "pay"))
    if status in ("applied", "interviewing", "offer"):
        pills.append(_pill(f"✔ {status}", "done"))

    cols = st.columns([8, 1.05, 0.55, 0.55] + ([1.15] if selectable else []), vertical_alignment="center")
    with cols[0]:
        st.markdown(
            f"<div class='job'><span class='score {_score_class(score)}' "
            f"title=\"{'Claude read this posting' if row.get('ai_read') else 'Keyword score'}\">{score:.0f}</span>"
            f"<div class='job-main'><div class='job-title'>{html.escape(str(row.get('title', '')))}</div>"
            f"<div class='job-meta'>{' · '.join(x for x in meta if x)} {''.join(pills)}</div></div></div>",
            unsafe_allow_html=True)
    with cols[1]:
        url = _safe_url(row.get("job_url"))
        if url:
            st.markdown(f"<a class='open-link' href='{html.escape(url, quote=True)}' target='_blank' "
                        f"rel='noopener'>Open ↗</a>", unsafe_allow_html=True)
    with cols[2]:
        if status not in ("applied", "interviewing", "offer") and st.button(
                "✓", key=f"ok_{key}", help="Mark applied (queues a 7-day follow-up)"):
            update_application(job_id, "applied")
            st.rerun()
    with cols[3]:
        if st.button("✕", key=f"x_{key}", help="Dismiss — hide this job everywhere"):
            dismiss_job(job_id)
            st.rerun()
    if selectable:
        with cols[4]:
            if st.button("Details", key=f"sel_{key}"):
                st.session_state["selected_job_idx"] = row.get("index")


# --- HEADER ---
st.markdown(theme_vars() + CSS, unsafe_allow_html=True)
flush_toasts()  # confirmations queued before the last rerun


@st.cache_data(ttl=600, show_spinner=False)
def _published_jobs_sha():
    """Head of the jobs-data branch, re-checked at most every 10 minutes."""
    return jobs_data.published_sha()


# Must run before anything opens the DB: sqlite3.connect() on a missing file
# silently creates an empty one, and the app would then show zero jobs.
try:
    with st.spinner("Loading latest jobs…"):
        if jobs_data.ensure(_published_jobs_sha()):
            st.cache_data.clear()  # new jobs published — drop cached queries
except jobs_data.JobsDataError as e:
    st.error(f"⚠️ {e}")
    st.stop()

try:
    user_store.load()
except user_store.StoreError as e:
    st.error(f"⚠️ Your saved data can't be read: {e}")
    st.stop()
_updated = jobs_updated_date()
_fresh = ""
if _updated:
    _age = (date.today() - date.fromisoformat(_updated)).days
    _fresh = (f"<span class='fresh stale'>⚠️ Jobs updated {_updated} · {_age} days ago</span>" if _age > 3
              else f"<span class='fresh'>Jobs updated {_updated}</span>")
st.markdown(f"<div class='brand'><span>🎯 <b>{html.escape(_ID['app_title'])}</b> · "
            f"{html.escape(_ID['brand_subtitle'])}</span>{_fresh}</div>", unsafe_allow_html=True)

if user_store.is_degraded():
    st.warning("⚠️ Can't reach GitHub right now — showing your last saved data. "
               "Saving is paused; changes made now would not be kept. Retrying automatically.")

def filter_jobs(df):
    if df.empty:
        return df
    filtered = df.copy()
    if "region" not in filtered.columns:
        filtered["region"] = "seattle"
    else:
        filtered["region"] = filtered["region"].fillna("seattle")
    if "job_type" not in filtered.columns:
        filtered["job_type"] = "fulltime"
    else:
        filtered["job_type"] = filtered["job_type"].fillna("fulltime")
    if region_filter != "All":
        filtered = filtered[filtered["region"] == region_filter]
    if type_filter != "All":
        filtered = filtered[filtered["job_type"] == type_filter]
    if search_query:
        q = search_query.lower()
        mask = (
            filtered["title"].fillna("").str.lower().str.contains(q, na=False) |
            filtered["company"].fillna("").str.lower().str.contains(q, na=False) |
            filtered["location"].fillna("").str.lower().str.contains(q, na=False)
        )
        filtered = filtered[mask]
    if date_filter != "All Time" and "date_posted" in filtered.columns:
        days_map = {"Last 24 hours": 1, "Last 3 days": 3, "Last 7 days": 7, "Last 30 days": 30}
        days = days_map.get(date_filter, 9999)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        # Only real ISO dates count. Undated rows (~9K LinkedIn) fall back to the
        # date we first saw them. A plain string compare used to let the literal
        # "nan" pass every filter, since "nan" > any "2026-..." string.
        posted = filtered["date_posted"].where(
            filtered["date_posted"].astype(str).str.match(r"^\d{4}-\d{2}-\d{2}"), None)
        effective = posted.fillna(filtered["scraped_at"].astype(str).str[:10])
        filtered = filtered[effective >= cutoff]
    if company_filter:
        filtered = filtered[filtered["company"].isin(company_filter)]
    if title_filter:
        filtered = filtered[filtered["title"].isin(title_filter)]
    if metro_filter:  # applies to Rest-of-USA jobs; home-area jobs are unaffected
        filtered = filtered[(filtered["region"] == "seattle") | filtered["metro"].isin(metro_filter)]
    return filtered



# === TAB: JOBS ===
def page_jobs():
    # Sidebar filters live only on this page; filter_jobs() reads them as globals.
    global search_query, min_score, region_filter, type_filter, date_filter, company_filter, title_filter, metro_filter, all_jobs, scored
    # --- SIDEBAR ---
    st.sidebar.header("Filters")
    search_query = st.sidebar.text_input("🔍 Search (title, company, location)", placeholder="e.g. supply chain manager")
    min_score = st.sidebar.slider("Minimum Score", 0, 100, 60)
    region_filter = st.sidebar.selectbox("Region", ["All", "seattle", "national"])
    type_filter = st.sidebar.selectbox("Job Type", ["All", "fulltime", "internship"])
    date_options = ["Last 30 days", "Last 7 days", "Last 3 days", "Last 24 hours", "All Time"]
    date_filter = st.sidebar.selectbox("Posted", date_options, index=0)

    # Company filter
    company_options = ["All Companies"]
    if DB_PATH.exists():
        _conn = get_db()
        top_companies = _conn.execute("""
            SELECT company, COUNT(*) as cnt FROM jobs
            WHERE score >= 60
            AND company IS NOT NULL AND company != ''
            GROUP BY company ORDER BY cnt DESC LIMIT 400
        """).fetchall()
        _conn.close()
        # Biggest employers first (by headcount), then by number of openings
        top_companies = sorted(top_companies, key=lambda r: (
            -(company_size.lookup(r[0])["employees"] or 0), -r[1]))
        company_options += [r[0] for r in top_companies]

    def _company_label(c):
        tier = company_size.lookup(c)["tier"]
        return f"{c} · {tier}" if tier != "unknown" else c

    company_filter = st.sidebar.multiselect("Company (largest first)", company_options[1:], default=[],
                                            format_func=_company_label,
                                            placeholder="Type to search companies...")
    if not company_filter:
        company_filter = None

    # Job title filter
    title_options = ["All Titles"]
    if DB_PATH.exists():
        _conn2 = get_db()
        top_titles = _conn2.execute("""
            SELECT title, COUNT(*) as cnt FROM jobs
            WHERE score >= 60 AND (dismissed IS NULL OR dismissed = 0)
            AND title IS NOT NULL AND title != ''
            GROUP BY title ORDER BY cnt DESC LIMIT 50
        """).fetchall()
        _conn2.close()
        title_options += [r[0] for r in top_titles]
    title_filter = st.sidebar.multiselect("Job Title", title_options[1:], default=[], placeholder="Type to search titles...")
    if not title_filter:
        title_filter = None

    # Big cities for the Rest-of-USA panels, most openings first
    _metro_counts = load_jobs(min_score=60)["metro"].value_counts()
    metro_filter = st.sidebar.multiselect(
        "City (Rest of USA)", list(_metro_counts.index),
        format_func=lambda m: f"{m} ({_metro_counts[m]:,})",
        placeholder="All cities") or None

    # --- LOAD DATA ---
    all_jobs = load_jobs(min_score=min_score)
    scored = all_jobs if not all_jobs.empty else pd.DataFrame()

    # --- METRICS ---
    if not scored.empty:
        col1, col2, col3, col4 = st.columns(4)
        week = (date.today() - timedelta(days=7)).isoformat()
        col1.metric("Jobs", f"{len(scored):,}")
        col2.metric("Claude has read", f"{int(scored['ai_read'].sum()):,}")
        col3.metric("Posted this week", f"{int((scored['eff_posted'] >= week).sum()):,}")
        sal = scored["salary_min"].dropna()
        col4.metric("Median salary", f"${sal.median()/1000:.0f}K" if not sal.empty else "—")

        st.divider()

    if scored.empty:
        st.info("No scored jobs yet. Run `/find-jobs` in Claude Code.")
    else:
        jobs = filter_jobs(scored)
        sort_by = st.radio("Sort", ["Best match", "Largest companies first"], horizontal=True,
                           key="job_sort", label_visibility="collapsed")
        if sort_by == "Largest companies first":
            # Headcount first (unknown sizes last), then match score
            jobs = jobs.assign(_emp=jobs["company_employees"].fillna(-1)).sort_values(
                ["_emp", "score"], ascending=False, kind="stable").drop(columns="_emp")
        # Full-time first: internships are listed as a fallback.
        sub_tabs = st.tabs([f"{HOME} area FT", "Big cities & rest of USA FT", f"{HOME} Intern (fallback)",
                            "USA Intern (fallback)", "All"])

        def render_table(df, name, key):
            if df.empty:
                st.info(f"No {name} jobs matching filters.")
                return
            display = df.reset_index(drop=False)

            # Paginate — show 25 at a time for fast interaction
            page_size = 25
            page_key = f"page_{key}"
            if page_key not in st.session_state:
                st.session_state[page_key] = 0
            page = st.session_state[page_key]
            total_pages = (len(display) - 1) // page_size + 1
            page_slice = display.iloc[page * page_size:(page + 1) * page_size]

            apps = applications_map()
            with st.container(height=400):
                for i, row in page_slice.iterrows():
                    job_row(row, f"{key}_{i}", apps)

            # Pagination controls
            if total_pages > 1:
                pc1, pc2, pc3 = st.columns([1, 2, 1])
                with pc1:
                    if page > 0 and st.button("← Prev", key=f"prev_{key}"):
                        st.session_state[page_key] = page - 1
                        st.rerun()
                with pc2:
                    st.caption(f"Page {page+1}/{total_pages} ({len(display)} jobs)")
                with pc3:
                    if page < total_pages - 1 and st.button("Next →", key=f"next_{key}"):
                        st.session_state[page_key] = page + 1
                        st.rerun()

        with sub_tabs[0]:
            sea_ft = jobs[(jobs["region"] == "seattle") & (jobs["job_type"] != "internship")]
            render_table(sea_ft, f"{HOME} full-time", "t_sea_ft")
        with sub_tabs[1]:
            nat_ft = jobs[(jobs["region"] == "national") & (jobs["job_type"] != "internship")]
            render_table(nat_ft, "National full-time", "t_nat_ft")
        with sub_tabs[2]:
            sea_int = jobs[(jobs["region"] == "seattle") & (jobs["job_type"] == "internship")]
            render_table(sea_int, f"{HOME} internship", "t_sea_int")
        with sub_tabs[3]:
            nat_int = jobs[(jobs["region"] == "national") & (jobs["job_type"] == "internship")]
            render_table(nat_int, "National internship", "t_nat_int")
        with sub_tabs[4]:
            render_table(jobs, "all", "t_all")

        # Job detail panel
        if not jobs.empty:
            st.divider()
            sel_idx = st.session_state.get("selected_job_idx")
            if sel_idx is None or sel_idx not in jobs.index:
                sel_idx = jobs.index[0]
            job = jobs.loc[sel_idx]

            from src.sponsor_data import get_sponsor_badge
            badge_emoji, badge_label, badge_color = get_sponsor_badge(str(job.get("company", "") or ""))

            st.markdown(f"### 📄 {job['title']}")
            st.markdown(f"**{job['company']}** · {job.get('location', '')} · Score: **{int(job['score'])}/100**")
            st.markdown(f"{badge_emoji} **Visa:** {badge_label}")

            c1, c2 = st.columns([2, 1])
            with c1:
                if job.get("job_url"):
                    st.markdown(f"🔗 [Apply Here]({job['job_url']})")
                if job.get("description"):
                    with st.expander("Full Description"):
                        st.markdown(str(job["description"])[:3000])
            with c2:
                breakdown = {}
                if job.get("score_breakdown"):
                    try:
                        breakdown = json.loads(job["score_breakdown"]) if isinstance(job["score_breakdown"], str) else (job["score_breakdown"] or {})
                    except (json.JSONDecodeError, TypeError):
                        pass
                if breakdown.get("reasoning"):
                    st.info(breakdown["reasoning"])
                if breakdown.get("highlights"):
                    st.success("**Highlights:** " + ", ".join(breakdown["highlights"]))
                if breakdown.get("red_flags") and breakdown["red_flags"]:
                    st.warning("**Concerns:** " + ", ".join(breakdown["red_flags"]))

                st.divider()
                track_st = st.selectbox("Track:", ["—", "applied", "interviewing", "skipped"], key="trk")
                tc1, tc2 = st.columns(2)
                with tc1:
                    if track_st != "—" and st.button("Update Status"):
                        update_application(job.get("job_id", ""), track_st)
                        st.success(f"Marked as {track_st}!")
                        st.rerun()
                with tc2:
                    if st.button("🗑️ Dismiss"):
                        dismiss_job(job.get("job_id", ""))
                        st.success("Removed!")
                        st.rerun()

            # Referral links for selected job
            import urllib.parse
            company = str(job.get("company", "") or "")
            title = str(job.get("title", "") or "")
            job_id_curr = job.get("job_id", "")

            if company:
                # Comparable roles (B5)
                comparable = get_comparable_jobs(job, limit=3)
                if not comparable.empty:
                    st.markdown("---")
                    st.markdown("#### 🔗 Similar Roles")
                    for _, c in comparable.iterrows():
                        sal = ""
                        if pd.notna(c.get("salary_min")) and pd.notna(c.get("salary_max")):
                            sal = f" · ${int(c['salary_min']/1000)}K-${int(c['salary_max']/1000)}K"
                        c_cols = st.columns([0.3, 5, 1])
                        with c_cols[0]:
                            st.markdown(f"`{int(c['score'])}`")
                        with c_cols[1]:
                            st.markdown(f"**{c['title']}** @ {c['company']} · {c['location']}{sal}")
                        with c_cols[2]:
                            if c.get("job_url"):
                                st.link_button("Apply", c["job_url"], width="stretch")

                st.markdown("---")
                st.markdown("#### ⚡ Quick Actions")
                applied_status = get_application_status(job_id_curr)
                act_cols = st.columns([1.5, 1.5, 3])
                with act_cols[0]:
                    if job.get("job_url"):
                        st.link_button("📝 Open Posting", job["job_url"], width="stretch")
                with act_cols[1]:
                    if applied_status == "applied":
                        st.success("✓ Applied")
                    else:
                        if st.button("✅ Mark Applied", width="stretch", help="Marks applied + queues 7-day follow-up"):
                            update_application(job_id_curr, "applied")
                            st.success("Marked applied + 7-day follow-up queued!")
                            st.rerun()
                with act_cols[2]:
                    from src.outreach import referral_message, referral_note
                    note = referral_note(title, company)
                    st.code(note, language=None)
                    st.caption(f"Connection note ({len(note)}/300 chars) — replace [Name]. "
                               "For a DM or email with your resume attached:")
                    st.code(referral_message(title, company), language=None)

                st.markdown("#### 🤝 Find People at " + company)
                role_kw = title.lower().replace("sr.", "").replace("senior", "").strip()
                ref_cols = st.columns(4)
                ref_cols[0].link_button("👥 Same Role", f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(role_kw)}&company={urllib.parse.quote(company)}", width="stretch")
                ref_cols[1].link_button("🎯 Recruiters", f"https://www.linkedin.com/search/results/people/?keywords=recruiter%20talent%20acquisition&company={urllib.parse.quote(company)}", width="stretch")
                ref_cols[2].link_button("📦 SC Team", f"https://www.linkedin.com/search/results/people/?keywords=supply%20chain%20procurement&company={urllib.parse.quote(company)}", width="stretch")
                ref_cols[3].link_button(f"🎓 {_ID['school_short']} Alumni", f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(company)}&schoolFilter=%5B%22{_ID['linkedin_school_id']}%22%5D", width="stretch")

                # Resume tailor (B1+B2)
                desc = job.get("description", "") or ""
                if desc:
                    with st.expander("📝 Tailor Resume for This Job", expanded=False):
                        from src.resume_rephraser import (
                            keyword_match_analysis, rephrase_all_bullets, generate_tailored_docx
                        )

                        matched, missing = keyword_match_analysis(desc)

                        # ATS keyword chips
                        st.markdown("**ATS Keyword Match**")
                        kc1, kc2 = st.columns(2)
                        with kc1:
                            st.markdown("✅ **In your resume:**")
                            if matched:
                                st.markdown(" · ".join([f"`{k}`" for k in matched]))
                            else:
                                st.caption("None of the JD's key terms found")
                        with kc2:
                            st.markdown("❌ **Missing — consider adding:**")
                            if missing:
                                st.markdown(" · ".join([f"`{k}`" for k in missing]))
                            else:
                                st.caption("Resume covers all key terms")

                        st.markdown("---")
                        st.markdown("**Suggested Rephrases**")
                        rephrases = rephrase_all_bullets(desc)
                        any_changes = False
                        for r in rephrases:
                            if r["changed"]:
                                any_changes = True
                                st.markdown(f"- ~~{r['original']}~~")
                                st.markdown(f"  → **{r['rephrased']}**")
                                if r["changes"]:
                                    st.caption(f"  Changes: {', '.join(r['changes'])}")
                        if not any_changes:
                            st.caption("No automatic rephrases suggested — your bullets already match well.")

                        # Download DOCX
                        st.markdown("---")
                        try:
                            docx_buf = generate_tailored_docx(desc)
                            if docx_buf:
                                st.download_button(
                                    "📥 Download Tailored Resume Bullets (DOCX)",
                                    data=docx_buf,
                                    file_name=f"resume-bullets-{company.replace(' ','_')}.docx",
                                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                                )
                        except Exception as e:
                            st.caption(f"DOCX generation unavailable: {e}")

# === TAB: TRACKER ===
TRACK_STATUSES = ["applied", "interviewing", "offer", "rejected", "skipped"]


def page_tracker():
    st.subheader("📊 Application Pipeline")
    apps = load_applications()
    if apps.empty:
        st.info("No applications tracked yet — use ✓ on any job, or Track on its detail panel.")
        return
    counts = apps["status"].value_counts()
    for col, s_, emoji in zip(st.columns(5), TRACK_STATUSES, ["📨", "🎤", "🎉", "❌", "⏭️"]):
        col.metric(f"{emoji} {s_.title()}", int(counts.get(s_, 0)))
    st.divider()
    for _, a in apps.iterrows():
        jid = a["job_id"]
        title = a.get("title") if isinstance(a.get("title"), str) else "(posting no longer listed)"
        head = f"**{html.escape(title)}** @ {html.escape(str(a.get('company') or ''))} — {a.get('status')}"
        with st.expander(head, expanded=a.get("status") == "interviewing"):
            url = _safe_url(a.get("job_url"))
            if url:
                st.markdown(f"[Open posting ↗]({url})")
            with st.form(f"trk_{jid}"):
                c1, c2 = st.columns([1, 1])
                cur = a.get("status") if a.get("status") in TRACK_STATUSES else "applied"
                new_status = c1.selectbox("Status", TRACK_STATUSES, index=TRACK_STATUSES.index(cur))
                fu = pd.to_datetime(a.get("follow_up_date"), errors="coerce")
                follow = c2.date_input("Follow up by", value=None if pd.isna(fu) else fu.date())
                notes = st.text_area("Notes", value=a.get("notes") if isinstance(a.get("notes"), str) else "",
                                     height=70)
                if st.form_submit_button("Save"):
                    update_application(jid, new_status, notes=notes,
                                       follow_up_date=follow.isoformat() if follow else "")
                    st.rerun()

# === TAB: REFERRALS ===
def page_referrals():
    scored = load_jobs(min_score=60)
    st.subheader("🤝 Find People for Referrals")
    if scored.empty:
        st.info("No scored jobs yet.")
    else:
        from src.sponsor_data import lookup_sponsor
        st.caption("Jobs you've applied to first, then your strongest full-time matches. Staffing "
                   "agencies and U.S.-person-only employers are left out — a referral can't fix those.")
        apps = applications_map()
        active = {j for j, a in apps.items() if a.get("status") in ("applied", "interviewing")}
        pool = scored[(scored["job_type"] != "internship") & (~scored["company_staffing"].fillna(False))]
        pool = pool[~pool["company"].map(lambda c: bool((lookup_sponsor(c) or {}).get("us_only")))]
        top_jobs = pd.concat([pool[pool["job_id"].isin(active)],
                              pool[(pool["score"] >= 75) & (~pool["job_id"].isin(active))]]).head(40)
        seen_companies = set()
        for _, job in top_jobs.iterrows():
            company = str(job.get("company", "") or "")
            if not company or company in seen_companies:
                continue
            if len(seen_companies) >= 15:
                break
            seen_companies.add(company)
            title = str(job.get("title", "") or "")
            # Search for the role, not the posting's exact wording: drop "(Starlink Aviation)",
            # "Sr./Senior", roman-numeral levels
            role_kw = re.sub(r"\(.*?\)|\b(sr|senior|jr|junior|i{1,3}|iv|lead)\b", " ", title.lower().replace(".", " "))
            role_kw = " ".join(re.split(r"[,\-–|/]", role_kw)[0].split()[:4])
            tag = " · ✅ applied" if job["job_id"] in active else ""
            with st.expander(f"**{company}** — {title} · {job.get('location', '')}{tag}"):
                ref_cols = st.columns(4)
                ref_cols[0].link_button("👥 Same Role", f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(role_kw)}&company={urllib.parse.quote(company)}", width="stretch")
                ref_cols[1].link_button("🎯 Recruiters", f"https://www.linkedin.com/search/results/people/?keywords=recruiter%20talent%20acquisition&company={urllib.parse.quote(company)}", width="stretch")
                ref_cols[2].link_button("📦 SC Team", f"https://www.linkedin.com/search/results/people/?keywords=supply%20chain%20procurement&company={urllib.parse.quote(company)}", width="stretch")
                ref_cols[3].link_button(f"🎓 {_ID['school_short']} Alumni", f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(company)}&schoolFilter=%5B%22{_ID['linkedin_school_id']}%22%5D", width="stretch")
                from src.outreach import referral_note
                msg = referral_note(title, company)
                st.code(msg, language=None)

# === TAB: SPONSORS ===
def page_sponsors():
    st.subheader("🛂 Top H1B / STEM-OPT Sponsors for Supply Chain")
    st.caption("E-Verify enrolled employers that sponsor F1/H1B for SCM/Operations roles. STEM OPT extension (24 months) requires E-Verify.")

    from src.sponsor_data import get_top_sponsors_for_scm

    top_sponsors = get_top_sponsors_for_scm()
    fy = top_sponsors[0]["fiscal_year"] if top_sponsors else ""
    st.markdown(f"**{len(top_sponsors)} supply-chain employers**, ranked by actual USCIS H-1B approvals "
                f"(FY{fy}) — apply directly at their careers pages, even if not in our scrape:")

    cols = st.columns(3)
    for i, sp in enumerate(top_sponsors):
        n = sp["approvals"]
        if sp["us_only"]:
            emoji, note = "🔴", "Most roles need U.S.-person status / clearance"
        elif n:
            emoji = "🟢" if n >= 100 else "🟡"
            note = f"{n:,} H-1B approvals FY{fy}"
        else:
            emoji, note = "⚪", f"Not in USCIS's FY{fy} top 1,000 — check each posting"
        with cols[i % 3]:
            q = urllib.parse.quote_plus(f"{sp['company']} careers supply chain")
            st.markdown(f"{emoji} **[{sp['company']}](https://www.google.com/search?q={q})**")
            st.caption(note)

    # Verified top sponsors from USCIS data
    st.divider()
    try:
        import json as _json
        from pathlib import Path as _P
        vpath = _P(__file__).parent / "config" / "h1b_sponsors_verified.json"
        if vpath.exists():
            vdata = _json.loads(vpath.read_text())
            sponsors = vdata.get("sponsors", {})
            fy = vdata.get("fiscal_year", "")
            st.markdown(f"### 📊 Top 40 H1B Sponsors — USCIS Verified (FY{fy})")
            st.caption(f"Actual petition approval counts from {vdata.get('source','USCIS')}. These are the highest-volume sponsors nationally.")
            top40 = list(sponsors.items())[:40]
            vc = st.columns(2)
            for i, (norm, d) in enumerate(top40):
                with vc[i % 2]:
                    careers = "https://www.google.com/search?q=" + urllib.parse.quote_plus(f"{d['employer']} careers")
                    st.markdown(f"{i+1}. **[{d['employer'].title()}]({careers})** — {d['h1b_approvals']:,} approvals")
            st.caption("Note: Top nationally = mostly tech/consulting. Use the SCM-focused list above for role-relevant targets. Refresh with `python scripts/refresh_h1b_sponsors.py`.")
    except Exception as e:
        st.caption(f"Verified data unavailable: {e}")

    st.divider()
    st.info("""
    **Why this matters for STEM OPT:** a STEM-designated degree gives 36 months of OPT.
    But the 24-month extension REQUIRES the employer to be E-Verify enrolled. The SCM list above is
    curated for role relevance; the verified list shows actual USCIS approval volume.

    **Strategy:** Apply directly at these companies' careers pages, not just from job board scrapes.
    """)

# === TAB: SKILLS GAP ===
@st.cache_data(ttl=600, show_spinner=False)
def load_skill_insights():
    """Skill demand computed at export time from FULL descriptions (see skills_insights).
    Falls back to computing from this DB — approximate, since the deploy copy truncates."""
    from src import skills_insights
    ins = skills_insights.load_precomputed(DB_PATH)
    if ins:
        return ins, True
    return skills_insights.analyze_skills(min_score=70, db_path=DB_PATH), False


def _skills_caption(ins, exact):
    src = (f"{ins['total']:,} full job descriptions scoring {ins.get('min_score', 70)}+, "
           f"computed {str(ins.get('computed_at', ''))[:10]}") if exact else \
          f"{ins['total']:,} descriptions — ⚠️ approximate (truncated text); refreshes on the next data publish"
    st.caption(f"Based on {src}. LinkedIn postings have no description and aren't counted.")


def page_skills():
    st.subheader("🎓 Skills Gap")
    ins, exact = load_skill_insights()
    if not ins["total"]:
        st.info("No job descriptions to analyze yet.")
        return
    _skills_caption(ins, exact)
    gaps = sorted([r for r in ins["technical"] + ins["certs"] if not r.get("has") and r["pct"] >= 2],
                  key=lambda r: -r["pct"])
    have = sorted([r for r in ins["technical"] + ins["soft"] + ins["certs"] if r.get("has")],
                  key=lambda r: -r["pct"])
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### ❌ Gaps to close (by demand)")
        st.dataframe(pd.DataFrame([{"Skill": r["skill"], "Asked for in": f"{r['pct']}%"} for r in gaps]),
                     hide_index=True, width="stretch")
    with c2:
        st.markdown("#### ✅ You already have")
        st.dataframe(pd.DataFrame([{"Skill": r["skill"], "Asked for in": f"{r['pct']}%"} for r in have]),
                     hide_index=True, width="stretch")
    st.caption("Full demand tables are on Skills Insights; Projects has build guides for the top gaps.")

# === TAB: SKILLS INSIGHTS ===
def page_insights():
    st.subheader("📈 Skills & Certifications Demand")
    insights, exact = load_skill_insights()
    if insights["total"] == 0:
        st.info("No scored jobs to analyze yet.")
    else:
        _skills_caption(insights, exact)
        st.caption(f"✅ = {NAME} already has it, ❌ = gap to build.")

        def render_skill_table(items, title):
            st.markdown(f"#### {title}")
            df = pd.DataFrame([{
                "Skill": r["skill"],
                "Demand": f"{r['pct']}%",
                "Status": "✅ Have" if r.get("has") else "❌ Gap",
                "_pct": r["pct"],
            } for r in items[:12]])
            st.dataframe(
                df.drop(columns=["_pct"]),
                hide_index=True, width="stretch",
                column_config={"Demand": st.column_config.TextColumn(width="small")},
            )

        c1, c2 = st.columns(2)
        with c1:
            render_skill_table(insights["technical"], "🔧 Top Technical Skills")
        with c2:
            render_skill_table(insights["soft"], "🤝 Top Management / Soft Skills")

        render_skill_table(insights["certs"], "📜 Certifications Mentioned")

        # Highlight top gaps
        tech_gaps = [r for r in insights["technical"] if not r.get("has") and r["pct"] >= 8][:4]
        if tech_gaps:
            st.divider()
            st.markdown("#### 🎯 Highest-ROI Skill Gaps to Close")
            for g in tech_gaps:
                st.markdown(f"- **{g['skill']}** — requested in {g['pct']}% of relevant jobs")

# === TAB: FELLOWSHIPS ===
def page_fellowships():
    st.subheader("🏆 Fellowships, Competitions & Programs")
    st.caption("Curated opportunities from config/opportunities.json. Verify live dates each fall — most LDPs recruit Aug-Nov for next year.")

    import json as _json
    opp_path = Path(__file__).parent / "config" / "opportunities.json"
    if opp_path.exists():
        opps = _json.loads(opp_path.read_text())

        # LDPs first: they're the only full-time pipelines here (full-time is the priority)
        # and they recruit Aug-Nov for the following year.
        section_meta = [
            ("rotational_ldps", "🔄 Rotational / Leadership Development Programs — full-time, recruiting now"),
            ("fellowships", "🎖️ Fellowships & Scholarships"),
            ("case_competitions", "🎓 Case Competitions"),
            ("hackathons_datathons", "💻 Hackathons & Datathons"),
            ("associations", "🤝 Professional Associations"),
        ]

        for key, title in section_meta:
            items = opps.get(key, [])
            if not items:
                continue
            st.markdown(f"### {title}")
            for o in items:
                f1 = o.get("f1_ok")
                not_viable = "not viable" in (o.get("tier") or "").lower()
                flag = "🔴" if not_viable else ("🟢" if f1 else "🟡")
                tier = f" · _{o['tier']}_" if o.get("tier") else ""
                with st.expander(f"{flag} **{o['name']}** — {o['org']}{tier}"):
                    st.markdown(f"**Timeline:** {o.get('timeline','—')}")
                    st.markdown(f"**Eligibility:** {o.get('eligibility','—')}")
                    st.markdown(f"**About:** {o.get('desc','')}")
                    if o.get("url"):
                        st.markdown(f"[Official page →]({o['url']})")
            st.markdown("")

        st.divider()
        st.info("🟢 = F1-friendly · 🟡 = verify sponsorship per posting · 🔴 = generally not viable "
                "(U.S.-person / clearance roles).\n\n"
                "**On sponsorship questions:** answer \"Yes\" to \"will you now or in the future require "
                "sponsorship?\" — you'll need an H-1B after OPT. Do mention that your STEM degree gives up to "
                "3 years of OPT work authorization first; the 24-month STEM extension needs an E-Verify "
                "employer and a signed I-983 training plan.")
    else:
        st.info("Opportunities data not found.")

# === TAB: PROJECTS ===
def page_projects():
    st.subheader("🛠️ Portfolio Projects")
    plan = Path(__file__).parent / "career-plan" / "02-projects.md"
    if plan.exists():
        st.markdown("Your portfolio plan (10 projects: 3 flagship + 7 light) lives in the career plan. "
                    "It supersedes the older starter guides below.")
        with st.expander("📋 Portfolio plan — the 10 projects", expanded=True):
            # Relative links between career-plan pages don't resolve here; show their text
            st.markdown(re.sub(r"\[([^\]]+)\]\((?!https?://)[^)]+\)", r"\1", plan.read_text()))
        st.download_button("Download the portfolio plan", plan.read_text(), "portfolio-plan.md", "text/markdown")
        st.divider()

    st.markdown("#### Starter build guides")
    st.caption("Step-by-step technical guides. Download a guide's files to run it on your laptop.")
    if not PROJECTS_DIR.exists():
        st.info("No starter guides yet.")
        return
    for pdir in sorted(d for d in PROJECTS_DIR.iterdir() if d.is_dir() and (d / "README.md").exists()):
        content = (pdir / "README.md").read_text()
        title = content.split("\n")[0].replace("# ", "")
        with st.expander(f"📁 {title}"):
            st.markdown(content)  # the whole guide — it used to be cut at 5,000 chars mid-code-block
            files = [f for f in sorted(pdir.iterdir()) if f.is_file()]
            for col, f in zip(st.columns(max(len(files), 1)), files):
                col.download_button(f"⬇ {f.name}", f.read_bytes(), f.name, key=f"dl_{pdir.name}_{f.name}")

# === TAB: TO-DOS ===
def page_todos():
    st.subheader("✅ To-Dos & Action Items")
    st.caption("Auto-imports action items from coffee chats. Add your own too.")

    from src.todos import add_todo, delete_todo, get_todos, import_coffee_chat_actions, set_status

    # Pull in new coffee-chat action items (one batched write; none if nothing is new).
    # If GitHub is unreachable, skip quietly — the page still works read-only.
    try:
        import_coffee_chat_actions()
    except user_store.StoreError:
        pass

    todos = get_todos()
    open_todos = [t for t in todos if t["status"] == "open"]
    done_todos = [t for t in todos if t["status"] == "done"]

    c1, c2 = st.columns(2)
    c1.metric("Open", len(open_todos))
    c2.metric("Done", len(done_todos))

    st.markdown("### 📌 Open")
    if not open_todos:
        st.success("All caught up!")
    else:
        # Group by category
        cats = {}
        for t in open_todos:
            cats.setdefault(t.get("category", "general"), []).append(t)
        for cat, items in sorted(cats.items()):
            st.markdown(f"**{cat.replace('-', ' ').title()}**")
            for t in items:
                cols = st.columns([0.5, 6, 0.5])
                with cols[0]:
                    if st.button("✓", key=f"done_{t['id']}", help="Mark done") and save(set_status, t["id"], "done"):
                        st.rerun()
                with cols[2]:
                    if st.button("🗑", key=f"del_{t['id']}", help="Delete") and save(delete_todo, t["id"]):
                        st.rerun()
                with cols[1]:
                    pri = t.get("priority", "medium")
                    pri_emoji = "🔴" if pri == "high" else "🟡" if pri == "medium" else "⚪"
                    st.markdown(f"{pri_emoji} {t['item']}")
                    if t.get("source") and t["source"] != "manual":
                        st.caption(f"from {t['source']}")

    if done_todos:
        with st.expander(f"✅ Completed ({len(done_todos)})"):
            for t in done_todos:
                dc = st.columns([6, 1])
                dc[0].markdown(f"~~{t['item']}~~")
                if dc[1].button("↩", key=f"reopen_{t['id']}", help="Reopen") and save(set_status, t["id"], "open"):
                    st.rerun()

    st.divider()
    st.markdown("### ➕ Add To-Do")
    with st.form("add_todo_form"):
        new_item = st.text_input("Task")
        tc1, tc2 = st.columns(2)
        new_cat = tc1.text_input("Category", value="general")
        new_pri = tc2.selectbox("Priority", ["high", "medium", "low"], index=1)
        if st.form_submit_button("Add"):
            if not new_item.strip():
                st.warning("Type a task first.")
            elif any(x.get("item") == new_item.strip() for x in todos):
                st.info("That's already on your list.")
            elif save(add_todo, new_item.strip(), category=new_cat, priority=new_pri):
                st.rerun()

# === TAB: INTERVIEW PREP ===
def page_interview():
    st.subheader("🎤 Interview Prep")

    from src import prep_content as pc
    from src.interview_prep import (
        get_recommendations, seed_recommendations,
        get_stories, add_story, delete_story, STORY_THEMES, story_coverage,
        generate_mock_interview, get_interview_experience_sources,
        save_mock_session, get_mock_sessions, delete_mock_session, practice_cadence,
    )
    seed_recommendations()

    # --- Context bar: domain focus + which company she's prepping for ---
    apps_df = load_applications()
    tracked = []
    if not apps_df.empty and "company" in apps_df.columns:
        tracked = sorted({c for c in apps_df["company"].dropna() if str(c).strip()})

    ctx1, ctx2, ctx3 = st.columns([2, 2, 1.4])
    domain = ctx1.selectbox("Focus domain", ["All"] + pc.DOMAINS, key="prep_domain")
    target = ctx2.selectbox(
        "Prepping for", ["(no specific company)"] + tracked, key="prep_target",
        help="Companies from your Tracker. Pick one to see its loop, values, and relevant cases.",
    )
    cad = practice_cadence()
    if cad["days_since"] is None:
        ctx3.metric("Last mock", "never", delta="overdue", delta_color="inverse")
    else:
        ctx3.metric("Last mock", f"{cad['days_since']}d ago",
                    delta="overdue" if cad["overdue"] else "on track",
                    delta_color="inverse" if cad["overdue"] else "normal")

    company = pc.find_company(target) if target != "(no specific company)" else None
    if target != "(no specific company)" and not company:
        st.caption(f"No curated prep for **{target}** yet — showing generic guidance. "
                   "Company Prep lists who is covered.")

    iv_sub = st.tabs([
        "🗺️ Loop Map", "🧩 Frameworks", "🧪 Case Bank", "⭐ STAR Stories",
        "🎯 Mock", "🏢 Company Prep", "📚 Resources",
    ])

    # ============ 1. LOOP MAP ============
    with iv_sub[0]:
        st.caption("What each round actually screens for, and how to prepare for it. "
                   "Mentor advice is attached to the round where it applies.")

        if company and company.get("interview_format"):
            st.info(f"**{company['name']} loop:** {company['interview_format']}")

        recs = get_recommendations()
        by_cat = {}
        for r in recs:
            by_cat.setdefault(r["category"], []).append(r)

        stages = pc.by_domain(pc.loop_map(), domain)
        if not stages:
            st.warning("Loop map content missing — check content/loop_map.json.")
        for s in stages:
            with st.expander(f"**{s.get('order')}. {s.get('stage')}** · {s.get('typical_duration','')} · {s.get('who','')}"):
                st.markdown(f"**What happens:** {s.get('what_happens','')}")
                st.markdown(f"**What they screen for:** {s.get('what_they_screen_for','')}")
                if s.get("how_to_prepare"):
                    st.markdown("**How to prepare**")
                    for a in s["how_to_prepare"]:
                        st.markdown(f"- {a}")
                if s.get("common_mistakes"):
                    st.markdown("**Common mistakes**")
                    for m in s["common_mistakes"]:
                        st.markdown(f"- {m}")
                if s.get("f1_notes"):
                    st.warning(f"🛂 **F-1 note:** {s['f1_notes']}")

        st.divider()
        st.markdown("### 💡 Mentor recommendations")
        st.caption("From coffee chats. Grouped by theme.")
        for cat, items in by_cat.items():
            st.markdown(f"**{cat}**")
            for r in items:
                st.markdown(f"- {r['recommendation']}")
                who = r.get("recommended_by") or ""
                role = f", {r['person_role']}" if r.get("person_role") else ""
                if who:
                    st.caption(f"— {who}{role}")

    # ============ 2. FRAMEWORKS ============
    with iv_sub[1]:
        st.caption("Named, citable frameworks. Never cite one you cannot define — interviewers probe.")
        fws = pc.by_domain(pc.frameworks(), domain)
        st.markdown(f"**{len(fws)} frameworks** for {domain}")
        for f in fws:
            with st.expander(f"**{f.get('name')}** — {f.get('one_liner','')}"):
                st.markdown(f"**When to use:** {f.get('when_to_use','')}")
                if f.get("steps"):
                    st.markdown("**Steps**")
                    for i, stp in enumerate(f["steps"], 1):
                        st.markdown(f"{i}. **{stp.get('label','')}** — {stp.get('detail','')}")
                if f.get("worked_example"):
                    st.markdown("**Worked example**")
                    st.info(f["worked_example"])
                src_name, src_url = f.get("source_name", ""), f.get("source_url", "")
                if src_url:
                    st.caption(f"Source: [{src_name}]({src_url}) · {f.get('license_note','')}")
                elif src_name:
                    st.caption(f"Source: {src_name} · {f.get('license_note','')}")

    # ============ 3. CASE BANK ============
    with iv_sub[2]:
        st.caption("Worked cases. Read the prompt, answer out loud, THEN open the structure — "
                   "reading the answer first teaches you nothing.")

        cases = pc.by_domain(pc.cases(), domain)
        cb1, cb2 = st.columns([1, 3])
        diff = cb1.selectbox("Difficulty", ["All", "core", "stretch"], key="case_diff")
        if diff != "All":
            cases = [c for c in cases if c.get("difficulty") == diff]
        cb2.markdown(f"### {len(cases)} cases")

        for c in cases:
            badge = "🔴" if c.get("difficulty") == "stretch" else "🟢"
            with st.expander(f"{badge} **{c.get('title')}** · {c.get('domain')}"
                             + (f" · {c['framework']}" if c.get("framework") else "")):
                st.markdown(f"> {c.get('prompt','')}")
                st.caption(f"Screens for: {c.get('why_asked','')}")

                st.text_area("Your answer (practice before revealing)",
                             key=f"case_ans_{c.get('id')}", height=100,
                             placeholder="Structure your answer here first…")

                with st.expander("🔓 Reveal answer structure"):
                    if c.get("framework"):
                        st.markdown(f"**Framework:** {c['framework']}")
                    st.markdown("**How a strong answer walks through it**")
                    for i, s in enumerate(c.get("structure", []), 1):
                        st.markdown(f"{i}. {s}")
                    if c.get("quant_hooks"):
                        st.markdown("**Numbers they may push on**")
                        for q in c["quant_hooks"]:
                            st.markdown(f"- {q}")
                    gs1, gs2 = st.columns(2)
                    with gs1:
                        st.markdown("**✅ Good signals**")
                        for g in c.get("good_signals", []):
                            st.markdown(f"- {g}")
                    with gs2:
                        st.markdown("**❌ Failure modes**")
                        for fm in c.get("failure_modes", []):
                            st.markdown(f"- {fm}")
                    if c.get("followups"):
                        st.markdown("**Follow-ups to expect**")
                        for fu in c["followups"]:
                            st.markdown(f"- {fu}")
                    if c.get("candidate_angle"):
                        st.success(f"**Your angle:** {c['candidate_angle']}")

    # ============ 4. STAR STORIES ============
    with iv_sub[3]:
        cov = story_coverage()
        total = len(STORY_THEMES)
        st.progress(len(cov["covered"]) / total if total else 0,
                    text=f"Story coverage: {len(cov['covered'])}/{total} themes")
        if cov["missing"]:
            st.warning("**No story yet for:** " + " · ".join(cov["missing"])
                       + "\n\nA gap here is the most common cause of a blown behavioral round.")
        else:
            st.success("Every behavioral theme has at least one story.")

        st.caption("Say \"I\", not \"we\". Quantify the Result. Expect three layers of follow-up.")
        stories = get_stories()

        if stories:
            by_theme = {}
            for s in stories:
                by_theme.setdefault(s["theme"], []).append(s)
            for theme, items in by_theme.items():
                st.markdown(f"#### {theme} ({len(items)})")
                for s in items:
                    with st.expander(s["title"]):
                        st.markdown(f"**Situation:** {s.get('situation','')}")
                        st.markdown(f"**Task:** {s.get('task','')}")
                        st.markdown(f"**Action:** {s.get('action','')}")
                        st.markdown(f"**Result:** {s.get('result','')}")
                        if s.get("tags"):
                            st.caption(f"Tags: {s['tags']}")
                        if st.button("Delete", key=f"del_story_{s['id']}"):
                            delete_story(s["id"])
                            st.rerun()

            import io, csv
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["Theme", "Title", "Situation", "Task", "Action", "Result", "Tags"])
            for s in stories:
                w.writerow([s.get("theme"), s.get("title"), s.get("situation"),
                            s.get("task"), s.get("action"), s.get("result"), s.get("tags", "")])
            st.download_button("📥 Export All Stories (CSV → open in Excel)",
                               buf.getvalue(), "star_stories.csv", "text/csv")
        else:
            st.info("No stories yet. Add your first below.")

        st.divider()
        st.markdown("### ➕ Add STAR Story")
        default_theme = cov["missing"][0] if cov["missing"] else STORY_THEMES[0]
        with st.form("add_story_form"):
            s_title = st.text_input("Story title (short label)")
            s_theme = st.selectbox("Behavioral theme", STORY_THEMES,
                                   index=STORY_THEMES.index(default_theme))
            s_sit = st.text_area("Situation")
            s_task = st.text_area("Task")
            s_action = st.text_area("Action")
            s_result = st.text_area("Result (with metrics if possible)")
            s_tags = st.text_input("Tags (optional, comma-separated)")
            if st.form_submit_button("Save Story") and s_title:
                add_story(s_title, s_theme, s_sit, s_task, s_action, s_result, s_tags)
                st.success("Saved!")
                st.rerun()

    # ============ 5. MOCK ============
    with iv_sub[4]:
        if cad["overdue"]:
            st.warning("Mock is overdue — the cadence your mentor recommended is one every 2 weeks.")
        st.caption("Your school's career center may run free mock interviews in "
                   "functional, behavioral, and case formats. Use this for solo reps between those.")

        if "mock_questions" not in st.session_state:
            st.session_state["mock_questions"] = generate_mock_interview(5)
        mc1, mc2 = st.columns([1, 1])
        if mc1.button("🎲 New mock (5 questions)"):
            st.session_state["mock_questions"] = generate_mock_interview(5)
            st.rerun()
        mock_company = mc2.text_input("Company (optional, saved with the session)",
                                      value="" if not company else company["name"])

        st.markdown("---")
        qs = st.session_state["mock_questions"]
        answers = {}
        for i, q in enumerate(qs, 1):
            st.markdown(f"**Q{i}. [{q['category']}]**")
            st.markdown(f"> {q['question']}")
            answers[i] = st.text_area(f"Answer Q{i}", key=f"mock_ans_{i}", height=80,
                                      label_visibility="collapsed")

        if st.button("💾 Save this session", type="primary"):
            if any(a.strip() for a in answers.values()):
                save_mock_session(qs, answers, company=mock_company)
                st.success("Saved. Strong answers here are your first draft of a STAR story.")
                st.rerun()
            else:
                st.warning("Nothing to save — write at least one answer first.")

        past = get_mock_sessions()
        if past:
            st.divider()
            st.markdown(f"### 📜 Past sessions ({len(past)})")
            for sess in past:
                label = f"{sess.get('date','')}"
                if sess.get("company"):
                    label += f" · {sess['company']}"
                answered = sum(1 for it in sess.get("items", []) if (it.get("answer") or "").strip())
                with st.expander(f"{label} — {answered}/{len(sess.get('items', []))} answered"):
                    for it in sess.get("items", []):
                        st.markdown(f"**[{it.get('category','')}]** {it.get('question','')}")
                        if (it.get("answer") or "").strip():
                            st.markdown(f"> {it['answer']}")
                        else:
                            st.caption("— not answered —")
                    if st.button("Delete session", key=f"del_mock_{sess['id']}"):
                        delete_mock_session(sess["id"])
                        st.rerun()

        pb = pc.playbook()
        proto = pb.get("self_mock_protocol") or {}
        if proto:
            st.divider()
            with st.expander("🤖 Run a rigorous solo mock with an LLM"):
                st.markdown(proto.get("summary", ""))
                for i, s in enumerate(proto.get("steps", []), 1):
                    st.markdown(f"{i}. {s}")
                if proto.get("prompt_template"):
                    st.markdown("**Paste this into Claude or ChatGPT:**")
                    st.code(proto["prompt_template"], language="text")

    # ============ 6. COMPANY PREP ============
    with iv_sub[5]:
        st.caption("Values, loop format, and F-1 posture per company. "
                   "Unverified entries say so — do not treat them as fact.")
        comps = pc.companies()
        names = [c["name"] for c in comps]
        pick = st.selectbox(
            "Company", names,
            index=names.index(company["name"]) if company and company["name"] in names else 0,
        )
        c = next((x for x in comps if x["name"] == pick), None)
        if c:
            if not c.get("verified"):
                st.warning("⚠️ Not verified from an official source this session — "
                           "confirm before relying on it.")
            st.markdown(f"**HQ:** {c.get('hq','—')}")
            st.markdown(f"**Why it matters for you:** {c.get('why_relevant','')}")
            if c.get("interview_format"):
                st.info(f"**Loop:** {c['interview_format']}")
            if c.get("values"):
                st.markdown(f"### {c.get('values_framework') or 'Values'} ({len(c['values'])})")
                for v in c["values"]:
                    st.markdown(f"- **{v.get('name')}** — {v.get('detail','')}")
            if c.get("prep_notes"):
                st.markdown("### Prep notes")
                for n in c["prep_notes"]:
                    st.markdown(f"- {n}")
            if c.get("visa_note"):
                st.markdown(f"🛂 **Visa/sponsorship:** {c['visa_note']}")
            if c.get("links"):
                st.markdown("### Official links")
                for l in c["links"]:
                    st.markdown(f"- [{l.get('label')}]({l.get('url')})")

            stories = get_stories()
            if c.get("values") and stories:
                st.divider()
                st.markdown("### Map your stories to their values")
                st.caption("Tag a story with a value name and it shows up here.")
                for v in c["values"]:
                    matched = [s for s in stories
                               if v.get("name", "").lower() in (s.get("tags", "") or "").lower()]
                    icon = "✅" if matched else "⬜"
                    st.markdown(f"{icon} **{v.get('name')}** — "
                                + (", ".join(s["title"] for s in matched) if matched else "_no story tagged_"))

    # ============ 7. RESOURCES ============
    with iv_sub[6]:
        pb = pc.playbook()
        if pb:
            st.markdown("### 🗓️ How people actually prepare")
            fm = pb.get("funnel_math") or {}
            if fm:
                st.info(f"**The funnel:** {fm.get('summary','')}")
                for d in fm.get("detail", []):
                    st.markdown(f"- {d}")
            for ph in pb.get("phases", []):
                with st.expander(f"**{ph.get('label')}** — {ph.get('focus','')}"):
                    for a in ph.get("actions", []):
                        st.markdown(f"- {a}")
                    if ph.get("output"):
                        st.success(f"**By the end:** {ph['output']}")
            wc = pb.get("weekly_cadence") or {}
            if wc.get("blocks"):
                st.markdown("#### A sustainable prep week")
                st.caption(wc.get("summary", ""))
                st.dataframe(pd.DataFrame(wc["blocks"]), hide_index=True,
                             width="stretch")
            for key, title in [("story_bank_method", "Story bank method"),
                               ("debrief_protocol", "Debrief every interview")]:
                sec = pb.get(key) or {}
                if sec:
                    with st.expander(f"**{title}**"):
                        st.markdown(sec.get("summary", ""))
                        for s in sec.get("steps", []):
                            st.markdown(f"- {s}")
                        for t in sec.get("coverage_targets", []):
                            st.markdown(f"- {t}")
        st.divider()
        st.markdown("### 🔎 Find real interview experiences")
        ie_c1, ie_c2 = st.columns(2)
        ie_company = ie_c1.text_input("Company", value="" if not company else company["name"],
                                      placeholder="e.g. Amazon")
        ie_role = ie_c2.text_input("Role", value="supply chain")
        for s in get_interview_experience_sources(ie_company, ie_role):
            st.markdown(f"**[{s['name']}]({s.get('search_url', s['url'])})**")
            st.caption(s["desc"])
        st.info("Launchers only — we never scrape these. Glassdoor and Blind have the best "
                "coverage for non-software management roles.")
        st.divider()
        st.markdown("### 📚 Resources")
        st.caption("Curated and link-out only — we deliberately do not scrape these. "
                   "Grouped by what you'd reach for.")

        groups = pc.resources()
        total_items = sum(len(g.get("items", [])) for g in groups)
        st.markdown(f"**{total_items} resources** across {len(groups)} groups")

        rc1, rc2 = st.columns(2)
        cost_filter = rc1.multiselect(
            "Cost", ["Free", "Freemium", "Paid", "Free with school login"], default=[])
        group_names = [g.get("group", "") for g in groups]
        group_filter = rc2.multiselect("Group", group_names, default=[])

        for g in groups:
            if group_filter and g.get("group") not in group_filter:
                continue
            items = g.get("items", [])
            if domain != "All":
                items = [i for i in items
                         if domain in (i.get("domains") or []) or "All" in (i.get("domains") or [])]
            if cost_filter:
                items = [i for i in items if i.get("cost") in cost_filter]
            if not items:
                continue
            st.markdown(f"### {g.get('group')}")
            if g.get("group_blurb"):
                st.caption(g["group_blurb"])
            for i in items:
                cost = i.get("cost", "")
                tag = {"Free": "🟢", "Freemium": "🟡", "Paid": "🔴",
                       "Free with school login": "🎓"}.get(cost, "")
                st.markdown(f"{tag} **[{i.get('name')}]({i.get('url')})** · {cost}")
                st.caption(i.get("what_it_is", ""))
                if i.get("best_for"):
                    st.caption(f"**Best for:** {i['best_for']}")
                if i.get("meta"):
                    st.caption(f"_{i['meta']}_")
                if i.get("caveat"):
                    st.caption(f"⚠️ {i['caveat']}")

# === TAB: NETWORKING ===
def page_networking():
    st.subheader("☕ Coffee Chats & Networking Log")
    st.caption("Log conversations, capture takeaways, and track action items from every chat.")

    from src.networking import delete_chat, get_all_action_items, get_all_chats, log_chat
    from src.todos import get_todos

    chats = get_all_chats()

    # Action items rollup across all chats
    action_items = get_all_action_items()
    if action_items:
        done = {x.get("item") for x in get_todos("done")}
        with st.expander(f"✅ All Action Items ({len(action_items)}) — tracked in To-Dos"):
            for ai in action_items:
                mark = "~~" if ai["item"] in done else ""
                st.markdown(f"- {mark}{html.escape(ai['item'])}{mark}  \n  <span style='font-size:0.8em;color:#888'>"
                            f"from {html.escape(str(ai['from']))} · {ai['date']}</span>", unsafe_allow_html=True)

    st.divider()

    # Show logged chats
    if not chats:
        st.info("No coffee chats logged yet. Add your first one below.")
    else:
        st.markdown(f"### 📇 {len(chats)} Chats Logged")
        for chat in chats:
            with st.expander(f"**{chat['person_name']}** — {chat['person_role']} @ {chat['person_company']} · {chat['chat_date']}"):
                if chat.get("linkedin_url"):
                    st.markdown(f"[LinkedIn]({chat['linkedin_url']})")
                if chat.get("how_connected"):
                    st.caption(f"How connected: {chat['how_connected']}")
                if chat.get("summary"):
                    st.markdown("**Summary**")
                    st.markdown(chat["summary"])
                if chat.get("key_takeaways"):
                    st.markdown("**Key Takeaways**")
                    st.markdown(chat["key_takeaways"])
                if chat.get("action_items"):
                    st.markdown("**Action Items**")
                    st.markdown(chat["action_items"])
                if chat.get("follow_up_date"):
                    st.info(f"Follow up by: {chat['follow_up_date']}")
                if st.button("🗑 Delete this chat", key=f"del_chat_{chat['id']}") and save(delete_chat, chat["id"]):
                    st.rerun()

    # Add new chat form
    st.divider()
    st.markdown("### ➕ Log a New Coffee Chat")
    with st.form("add_chat_form"):
        fc1, fc2 = st.columns(2)
        person_name = fc1.text_input("Name")
        person_role = fc2.text_input("Role")
        fc3, fc4 = st.columns(2)
        person_company = fc3.text_input("Company")
        linkedin_url = fc4.text_input("LinkedIn URL (optional)")
        chat_date = st.date_input("Date", value=date.today())
        how_connected = st.text_input("How did you connect? (optional)")
        summary = st.text_area("Summary of conversation")
        key_takeaways = st.text_area("Key takeaways (one per line)")
        action_items = st.text_area("Action items (one per line)")
        follow_up_date = st.date_input("Follow-up date (optional)", value=None)
        submitted = st.form_submit_button("Save Chat")
        if submitted:
            if not person_name.strip():
                st.warning("Add the person's name first.")
            elif save(log_chat, person_name.strip(), person_role, person_company, chat_date.isoformat(),
                      linkedin_url, how_connected, summary, key_takeaways, action_items,
                      follow_up_date.isoformat() if follow_up_date else ""):
                st.rerun()

# === TAB: TOOLS ===
def page_tools():
    st.subheader("⚡ Application Tools")

    st.markdown("### 📌 ATS Auto-Fill Bookmarklet")
    st.markdown("""
    A one-click form filler for Workday, Greenhouse, Lever, iCIMS, and similar ATS systems.
    Saves 5-10 minutes per application.

    **How to install (one-time setup):**
    1. Make sure your bookmarks bar is visible (`Cmd+Shift+B` or `Ctrl+Shift+B`)
    2. Click and **drag the link below** to your bookmarks bar
    3. Done!

    **How to use:**
    1. Open any job application form (Workday, Greenhouse, etc.)
    2. Click the bookmark in your bookmarks bar
    3. It fills only the safe fields: name, email, phone, LinkedIn, city, school, degree
    4. **You** answer sponsorship, work authorization, EEO, salary and start date — it never touches
       those, because a wrong auto-answer there costs more than typing it (see below)
    5. Review everything, then submit
    """)

    from src.bookmarklet import MANUAL_ANSWERS, generate_bookmarklet_js, get_profile_data
    encoded, raw_js = generate_bookmarklet_js()

    st.markdown(f'<a href="{encoded}" style="display:inline-block; padding:10px 20px; background:#1a73e8; color:white; text-decoration:none; border-radius:8px; font-weight:bold;">📌 Auto-Fill (drag to bookmarks)</a>', unsafe_allow_html=True)
    st.caption("👆 Drag this button to your bookmarks bar — clicking here won't do anything useful")

    with st.expander("What will it fill in?"):
        st.json(get_profile_data())
        st.caption("To change these, ask to have your profile updated.")

    st.markdown("### ✍️ Answer these yourself — consistently")
    st.caption("Copy-ready answers for the questions the bookmarklet deliberately skips.")
    for q, a in MANUAL_ANSWERS.items():
        st.markdown(f"**{q}**")
        st.code(a, language=None)

    with st.expander("View the JavaScript code"):
        st.code(raw_js, language="javascript")

# === TAB: EXPORT ===
def page_export():
    scored = load_jobs(min_score=60)
    st.subheader("📤 Export Data")
    st.markdown("#### Your data (backup)")
    st.caption("Everything you've entered — applications, coffee chats, to-dos, STAR stories, mock "
               "interviews, dismissed jobs. Keep a copy now and then.")
    st.download_button("📥 Download all my data (JSON)", json.dumps(user_store.load(), indent=2),
                       f"my-job-search-data-{date.today().isoformat()}.json", "application/json")
    st.markdown("#### Jobs")
    if not scored.empty:
        cols = [c for c in ["title", "company", "location", "metro", "company_tier", "job_url", "score",
                            "ai_read", "eff_posted", "job_type", "region", "salary_min", "salary_max"]
                if c in scored.columns]
        csv = scored[cols].rename(columns={"ai_read": "claude_read", "eff_posted": "posted"}).to_csv(index=False)
        st.download_button("📥 Download Jobs (CSV)", csv, "jobs.csv", "text/csv")
    apps_exp = load_applications()
    if not apps_exp.empty:
        st.download_button("📥 Download Applications (CSV)", apps_exp.to_csv(index=False), "applications.csv", "text/csv")

def page_today():
    from src.interview_prep import practice_cadence, story_coverage
    from src.todos import get_todos, set_status

    st.subheader("☀️ Today")
    jobs = load_jobs(min_score=70)
    apps = applications_map()
    today = date.today()
    baseline = visit_baseline()

    acted = {j for j, a in apps.items() if a.get("status") not in (None, "new")}
    due = [(jid, a) for jid, a in apps.items()
           if a.get("status") in ("applied", "interviewing")
           and str(a.get("follow_up_date") or "")[:10]
           and str(a["follow_up_date"])[:10] <= today.isoformat()]
    high = [t for t in get_todos("open") if t.get("priority") == "high"]
    chat_due = [c for c in user_store.get("coffee_chats")
                if str(c.get("follow_up_date") or "")[:10] and str(c["follow_up_date"])[:10] <= today.isoformat()]
    cad = practice_cadence()

    m1, m2, m3, m4 = st.columns(4)
    if baseline and not jobs.empty:
        m1.metric("New since your last visit", int((jobs["scraped_at"].astype(str).str[:10] > baseline).sum()))
    else:
        m1.metric("New since your last visit", "—", help="Counts start from your next visit.")
    m2.metric("Follow-ups due", len(due) + len(chat_due))
    m3.metric("High-priority to-dos", len(high))
    m4.metric("Last mock interview", "never" if cad["days_since"] is None else f"{cad['days_since']}d ago",
              delta="overdue" if cad["overdue"] else None, delta_color="inverse")

    # --- Shortlist: full-time, not yet acted on, fresh, max 2 per company ---
    st.markdown("### 🎯 Your shortlist")
    if jobs.empty:
        st.info("No jobs published yet.")
    else:
        ft = jobs[(jobs["job_type"] != "internship") & (~jobs["job_id"].isin(acted))]
        fresh = ft[ft["eff_posted"] >= (today - timedelta(days=14)).isoformat()]
        pool = fresh if not fresh.empty else ft
        pool = pool.groupby("company", sort=False).head(2)  # keeps the list varied; order preserved
        if fresh.empty:
            st.caption("Nothing posted in the last 14 days — showing the best older postings.")
        st.caption("🤖 = Claude read the posting." if pool["ai_read"].any() else
                   "Ranked by keyword score (Claude hasn't read these yet); ties go to the newest posting.")
        for label, region, n in ((f"{HOME} & area", "seattle", 8), ("Big cities & rest of USA", "national", 4)):
            rows = pool[pool["region"] == region].head(n)
            st.markdown(f"**{label}**")
            if rows.empty:
                st.caption("Nothing new here.")
            for i, row in rows.iterrows():
                job_row(row, f"today_{region}_{i}", apps, selectable=False)
        st.page_link(JOBS_PAGE, label="Browse and filter all jobs", icon="📋")

    # --- Follow-ups ---
    if due:
        st.markdown("### 📬 Follow-ups due")
        conn = get_db()
        ids = [jid for jid, _ in due]
        info = {r[0]: r[1:] for r in conn.execute(
            f"SELECT job_id, title, company, job_url FROM jobs WHERE job_id IN ({','.join('?' * len(ids))})", ids)}
        conn.close()
        for jid, a in sorted(due, key=lambda x: str(x[1].get("follow_up_date"))):
            title, company, url = info.get(jid, ("(posting no longer listed)", "", ""))
            link = f" · [open ↗]({_safe_url(url)})" if _safe_url(url) else ""
            st.markdown(f"- **{title}** @ {company} — {a.get('status')}, follow up by "
                        f"{str(a['follow_up_date'])[:10]}{link}")

    if chat_due:
        st.markdown("### ☕ Coffee-chat follow-ups due")
        for c in sorted(chat_due, key=lambda c: str(c.get("follow_up_date"))):
            st.markdown(f"- **{html.escape(str(c.get('person_name', '')))}** "
                        f"({html.escape(str(c.get('person_role') or ''))} @ {html.escape(str(c.get('person_company') or ''))}) "
                        f"— follow up by {str(c['follow_up_date'])[:10]}")

    # --- To-dos ---
    if high:
        st.markdown("### ✅ High-priority to-dos")
        for t_ in high[:5]:
            c1, c2 = st.columns([8, 1])
            c1.markdown(t_.get("item", ""))
            if c2.button("Done", key=f"today_todo_{t_['id']}"):
                set_status(t_["id"], "done")
                st.rerun()
        if len(high) > 5:
            st.page_link(TODOS_PAGE, label=f"All {len(high)} high-priority to-dos", icon="✅")

    # --- Prep nudges ---
    cov = story_coverage()
    if cad["overdue"] or cov["missing"]:
        st.markdown("### 🎤 Interview prep")
        if cad["overdue"]:
            st.warning("A mock interview is due — your mentor's cadence is one every 2 weeks.")
        if cov["missing"]:
            st.info(f"{len(cov['missing'])} behavioral themes have no STAR story yet "
                    f"(e.g. {', '.join(cov['missing'][:3])}).")
        st.page_link(INTERVIEW_PAGE, label="Open Interview Prep", icon="🎤")


# --- NAVIGATION ---
# Pages, not st.tabs: st.tabs executes EVERY tab's code on every click (3,871
# elements per rerun). With st.navigation only the open page runs.
JOBS_PAGE = st.Page(page_jobs, title="Jobs", icon="📋", url_path="jobs")
TODOS_PAGE = st.Page(page_todos, title="To-Dos", icon="✅", url_path="todos")
INTERVIEW_PAGE = st.Page(page_interview, title="Interview Prep", icon="🎤", url_path="interview")
pg = st.navigation({
    "": [st.Page(page_today, title="Today", icon="☀️", url_path="today", default=True)],
    "Find": [
        JOBS_PAGE,
        st.Page(page_referrals, title="Referrals", icon="🤝", url_path="referrals"),
        st.Page(page_sponsors, title="Sponsors", icon="🛂", url_path="sponsors"),
    ],
    "Apply & Track": [
        st.Page(page_tracker, title="Tracker", icon="📊", url_path="tracker"),
        TODOS_PAGE,
        st.Page(page_networking, title="Networking", icon="☕", url_path="networking"),
    ],
    "Prepare": [
        INTERVIEW_PAGE,
        st.Page(page_skills, title="Skills Gap", icon="🎓", url_path="skills"),
        st.Page(page_insights, title="Skills Insights", icon="📈", url_path="insights"),
        st.Page(page_projects, title="Projects", icon="🛠️", url_path="projects"),
        st.Page(page_fellowships, title="Fellowships", icon="🏆", url_path="fellowships"),
    ],
    "Tools": [
        st.Page(page_tools, title="Tools", icon="⚡", url_path="tools"),
        st.Page(page_export, title="Export", icon="📤", url_path="export"),
    ],
})
pg.run()

# --- FOOTER ---
flush_toasts()  # confirmations from saves this run that weren't followed by a rerun
