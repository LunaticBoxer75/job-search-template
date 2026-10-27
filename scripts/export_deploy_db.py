"""Export the deploy DB: top jobs only.

Run after scraping/scoring to refresh data/jobs_deploy.db for the dashboard.
Keeps jobs scoring 70+, full-time and internship alike.

Internships and full-time jobs share the same 70 bar.

The candidate's own data (applications, coffee chats, todos, recommendations, STAR
stories, mock sessions) deliberately does NOT live here — it lives in
data/user_data.json via src/user_store.py. It used to be copied into this file,
which meant every push of this DB overwrote whatever she'd typed on the hosted
dashboard. Keep those two files disjoint.
"""

import os
import sqlite3
import sys
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"
SRC = DATA / "jobs.db"
DST = DATA / "jobs_deploy.db"


def _tracked_job_ids():
    """Job IDs she applied to or dismissed — these survive every prune."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src import user_store

    if not user_store.is_remote():
        if not user_store.PATH.exists():
            raise SystemExit(
                "No MSCM_GITHUB_TOKEN and no local data/user_data.json snapshot — can't see "
                "which jobs she's tracking, so refusing to prune. Add the token to "
                ".streamlit/secrets.toml (see secrets.toml.example)."
            )
        print("WARNING: no MSCM_GITHUB_TOKEN — protecting tracked jobs using the LOCAL "
              "snapshot of her data, which may be stale.")
    ids = {a["job_id"] for a in user_store.get("applications") if a.get("job_id")}
    ids |= {d["job_id"] for d in user_store.get("dismissed_jobs") if d.get("job_id")}
    return sorted(ids)


def main():
    src = sqlite3.connect(SRC)

    # Prune postings older than 90 days (keep any that are tracked).
    # Age = when the posting was published; undated rows fall back to when we
    # first saw them. NOT scraped_at alone — the scraper uses INSERT OR IGNORE, so
    # scraped_at is first-seen, and pruning on it deleted still-live postings.
    #
    # _tracked_job_ids() raises if user_data.json is unreadable, which aborts the
    # export BEFORE anything is deleted — an empty tracked list would prune her
    # tracked jobs.
    #
    # Sentinel when nothing is tracked: `NOT IN (NULL)` yields NULL in SQL, which
    # would silently prune nothing. An empty string can never be a real job_id.
    tracked = _tracked_job_ids() or [""]
    placeholders = ",".join("?" * len(tracked))
    pruned = src.execute(
        f"""DELETE FROM jobs
            WHERE COALESCE(date_posted, substr(scraped_at, 1, 10)) < date('now','-90 days')
            AND job_id NOT IN ({placeholders})""",
        tracked,
    ).rowcount
    # Retention: nothing under 70 is kept (user's rule, 2026-09-27) — including jobs Claude
    # read and rated below 70 ("remove low-rated automatically, even the existing ones").
    # Those URLs go to rejected_urls first so the scraper skips them; otherwise the next
    # scrape re-inserts them at their keyword score and Claude re-reads them every day.
    # Kept regardless: jobs she applied to or dismissed (her records).
    src.execute("""CREATE TABLE IF NOT EXISTS rejected_urls
                   (job_url TEXT PRIMARY KEY, reason TEXT, rejected_at TEXT)""")
    src.execute(
        f"""INSERT OR IGNORE INTO rejected_urls
            SELECT job_url, 'claude rated ' || CAST(score AS INT), datetime('now') FROM jobs
            WHERE COALESCE(score, 0) < 70 AND job_url != ''
            AND json_extract(score_breakdown, '$.scorer') = 'ai'
            AND job_id NOT IN ({placeholders})""", tracked)
    low = src.execute(
        f"""DELETE FROM jobs WHERE COALESCE(score, 0) < 70 AND job_id NOT IN ({placeholders})""",
        tracked,
    ).rowcount
    # Closed postings (LinkedIn's expired redirect, found by backfill_linkedin_descriptions.py):
    # they can't be read or applied to, yet kept their keyword score at the top of the list.
    src.execute("CREATE TABLE IF NOT EXISTS expired_postings (job_id TEXT PRIMARY KEY, job_url TEXT, at TEXT)")
    src.execute(
        f"""INSERT OR IGNORE INTO rejected_urls
            SELECT job_url, 'closed posting', datetime('now') FROM jobs
            WHERE job_id IN (SELECT job_id FROM expired_postings) AND job_url != ''
            AND job_id NOT IN ({placeholders})""", tracked)
    closed = src.execute(
        f"""DELETE FROM jobs WHERE job_id IN (SELECT job_id FROM expired_postings)
            AND job_id NOT IN ({placeholders})""", tracked).rowcount
    # The memory only needs to outlast the scraper's lookback (7 days); keep 30
    src.execute("DELETE FROM rejected_urls WHERE rejected_at < datetime('now', '-30 days')")
    src.commit()
    if pruned:
        print(f"Pruned {pruned} jobs older than 90 days")
    if low:
        print(f"Removed {low} jobs scoring below 70")
    if closed:
        print(f"Removed {closed} closed postings")

    dst = sqlite3.connect(DST)

    # Jobs (trimmed)
    dst.execute("DROP TABLE IF EXISTS jobs")
    dst.execute("""CREATE TABLE jobs (
        job_id TEXT PRIMARY KEY, title TEXT, company TEXT, location TEXT,
        job_url TEXT, description TEXT, job_type TEXT, date_posted TEXT,
        salary_min REAL, salary_max REAL, salary_currency TEXT, source TEXT,
        search_term TEXT, search_category TEXT, scraped_at TEXT, score REAL,
        score_breakdown TEXT, scored_at TEXT, is_remote INTEGER DEFAULT 0,
        notified INTEGER DEFAULT 0, region TEXT DEFAULT 'seattle', dismissed INTEGER DEFAULT 0)""")
    # Jobs she applied to always ship (not ones she dismissed), so Tracker rows keep details
    from src import user_store
    applied = [a["job_id"] for a in user_store.get("applications") if a.get("job_id")] or [""]
    tracked_ph = ",".join("?" * len(applied))
    rows = src.execute(f"""
        SELECT job_id, title, company, location, job_url,
               SUBSTR(description, 1, 800), job_type, date_posted,
               salary_min, salary_max, salary_currency, source, search_term,
               search_category, scraped_at, score, score_breakdown, scored_at,
               is_remote, notified, region, dismissed
        FROM jobs WHERE (dismissed IS NULL OR dismissed = 0)
        AND title IS NOT NULL AND title != '' AND company IS NOT NULL AND company != ''
        AND (score >= 70 OR job_id IN ({tracked_ph}))
    """, applied).fetchall()
    dst.executemany("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    # Drop any aux tables left over from before her data moved to user_data.json.
    # Leaving them would let a stale copy shadow the real store.
    for tbl in ("applications", "coffee_chats", "todos", "interview_recs", "star_stories"):
        dst.execute(f"DROP TABLE IF EXISTS {tbl}")

    # Skills demand, from FULL descriptions (the deploy copy truncates them to 800 chars)
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src import skills_insights
    ins = skills_insights.analyze_skills(min_score=70, db_path=SRC)
    skills_insights.store(dst, ins)
    print(f"Skills insights: {ins['total']:,} full descriptions analysed")

    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    src.close()

    # A fresh local export is authoritative on this machine: drop the marker that
    # would let src/jobs_data.py overwrite it with the last published copy.
    (DATA / ".jobs_deploy.sha").unlink(missing_ok=True)

    size_mb = os.path.getsize(DST) / 1024 / 1024
    print(f"Exported {len(rows)} jobs ({size_mb:.1f}MB)")


if __name__ == "__main__":
    main()
