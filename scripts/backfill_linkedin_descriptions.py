"""Fetch job descriptions for top LinkedIn postings, so Claude (Pass 2) can read them.

LinkedIn search results come back with NO description (JobSpy only fetches it with
one extra request per posting). That hides most of the home-area panel from Pass 2:
most home-area full-time jobs rated 80+ had no description.

Fetches serially with a 0.6-1.2s pause, and stops early if LinkedIn stops returning
descriptions (a 429 looks like an empty page), so a block is reported, not hidden.
Closed postings are recognised by LinkedIn's `expired_jd_redirect` and recorded in
`expired_postings`; the export deletes them (they'd otherwise keep their keyword
score and sit at the top of the list unread).
Rows that get a description are rescored with Pass 1 — its years / clearance /
sponsorship rules can now fire. One DB writer: don't run it during a scrape.

Usage: python scripts/backfill_linkedin_descriptions.py [--limit 300] [--region seattle|national|all] [--min-score 80] [--shard K/N]

--shard K/N splits the queue so N copies can run side by side without overlap (e.g. 0/2 and 1/2).
"""

import argparse
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup  # noqa: E402
from jobspy.linkedin import LinkedIn  # noqa: E402
from jobspy.model import DescriptionFormat  # noqa: E402
from jobspy.util import markdown_converter, remove_attributes  # noqa: E402

from src.quick_scorer import score_row  # noqa: E402

DB = ROOT / "data" / "jobs.db"
STOP_AFTER_EMPTY = 8  # consecutive empty responses → check whether LinkedIn is blocking us


def _fetch(li, linkedin_id):
    """(markdown description or None, closed?). Same page and parsing as JobSpy's
    _get_job_details, which returns {} for closed postings and failed requests alike."""
    try:
        r = li.session.get(f"{li.base_url}/jobs/view/{linkedin_id}", timeout=10)
    except Exception:
        return None, False
    if "expired_jd_redirect" in r.url:
        return None, True
    if r.status_code != 200 or "linkedin.com/signup" in r.url:
        return None, False
    div = BeautifulSoup(r.text, "html.parser").find(
        "div", class_=lambda c: c and "show-more-less-html__markup" in c)
    if div is None:
        return None, False
    return markdown_converter(remove_attributes(div).prettify(formatter="html")), False


def _save(conn, updates):
    with conn:
        conn.executemany("UPDATE jobs SET description=?, score=?, score_breakdown=?, scored_at=? "
                         "WHERE job_id=?", updates)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--region", default="seattle", choices=["seattle", "national", "all"])
    ap.add_argument("--min-score", type=float, default=80)
    ap.add_argument("--shard", default="0/1", help="K/N: take only this slice of the queue")
    a = ap.parse_args()
    k, n = (int(x) for x in a.shard.split("/"))

    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    # Postings that returned nothing before (usually expired) — skip them next time
    conn.execute("CREATE TABLE IF NOT EXISTS linkedin_fetch_misses (job_id TEXT PRIMARY KEY, at TEXT)")
    # Postings LinkedIn says are closed — the export deletes these rows
    conn.execute("CREATE TABLE IF NOT EXISTS expired_postings (job_id TEXT PRIMARY KEY, job_url TEXT, at TEXT)")
    conn.commit()
    region_sql = "" if a.region == "all" else "AND region = ?"
    params = [a.min_score] + ([] if a.region == "all" else [a.region]) + [n, n, n, k, a.limit]
    rows = conn.execute(f"""
        SELECT job_id, job_url, title, company, location, job_type, date_posted, scraped_at,
               is_remote, salary_min, score
        FROM jobs
        WHERE source = 'linkedin' AND job_type = 'fulltime' AND LENGTH(COALESCE(description, '')) < 50
          AND score >= ? {region_sql}
          AND json_extract(score_breakdown, '$.scorer') IS NULL
          AND job_id NOT IN (SELECT job_id FROM linkedin_fetch_misses)
          AND job_id NOT IN (SELECT job_id FROM expired_postings)
          AND ((CAST(job_id AS INTEGER) % ?) + ?) % ? = ?
          AND COALESCE(date_posted, substr(scraped_at, 1, 10)) >= date('now', '-90 days')
        ORDER BY score DESC LIMIT ?""", params).fetchall()
    print(f"{len(rows)} LinkedIn postings to fetch ({a.region}, score ≥ {a.min_score:g})", flush=True)

    li = LinkedIn()
    li.scraper_input = SimpleNamespace(description_format=DescriptionFormat.MARKDOWN)
    # A posting known to be live: if it also comes back empty, LinkedIn is blocking us
    probe = conn.execute("""SELECT job_url FROM jobs WHERE source='linkedin' AND LENGTH(description) > 500
                            ORDER BY scraped_at DESC LIMIT 1""").fetchone()
    probe_id = re.search(r"/jobs/view/(\d+)", probe[0]).group(1) if probe else None
    misses, expired = [], []
    got, empty_streak, updates, t0, saved, below = 0, 0, [], time.time(), 0, 0
    for i, (job_id, url, title, company, loc, jtype, posted, scraped, remote, sal, old) in enumerate(rows, 1):
        m = re.search(r"/jobs/view/(\d+)", url or "")
        if not m:
            continue
        if conn.execute("SELECT LENGTH(COALESCE(description,'')) >= 50 FROM jobs WHERE job_id=?",
                        (job_id,)).fetchone()[0]:
            continue  # another copy (or an earlier run) already fetched it
        time.sleep(random.uniform(0.6, 1.2))
        desc, closed = _fetch(li, m.group(1))
        if closed:  # a real answer from LinkedIn, so not evidence of a block
            expired.append((job_id, url, datetime.now().isoformat()))
            continue
        if not desc:
            empty_streak += 1
            misses.append((job_id, datetime.now().isoformat()))
            if empty_streak >= STOP_AFTER_EMPTY:
                # Expired postings also come back empty; tell them apart from a block by
                # fetching one we know is live.
                time.sleep(1.5)
                if probe_id and _fetch(li, probe_id)[0]:
                    print(f"  {STOP_AFTER_EMPTY} empty in a row, but a known-live posting still loads — "
                          f"expired postings, continuing", flush=True)
                    empty_streak = 0
                else:
                    misses = misses[:-STOP_AFTER_EMPTY]  # don't blame these postings for a block
                    print(f"  stopping: LinkedIn is blocking (a known-live posting came back empty too; "
                          f"requested {i})", flush=True)
                    break
            continue
        empty_streak = 0
        got += 1
        desc = desc[:10000]
        score, breakdown = score_row(title, company, loc, desc, jtype, posted, scraped, remote, sal)
        updates.append((desc, score, breakdown, datetime.now().isoformat(), job_id))
        below += score < 70
        if len(updates) >= 50:  # save as we go: a long run shouldn't lose everything to one crash
            _save(conn, updates); saved += len(updates); updates = []
        if i % 25 == 0:
            print(f"  {i}/{len(rows)} requested, {got} descriptions ({time.time()-t0:.0f}s)", flush=True)

    _save(conn, updates); saved += len(updates)
    with conn:
        conn.executemany("INSERT OR IGNORE INTO linkedin_fetch_misses VALUES (?, ?)", misses)
        conn.executemany("INSERT OR IGNORE INTO expired_postings VALUES (?, ?, ?)", expired)
    conn.close()
    print(f"Done: requested {i if rows else 0}, got {got} descriptions, {len(expired)} closed "
          f"(export removes them), {len(misses)} empty (skipped next time) in {time.time()-t0:.0f}s. "
          f"Rescored with Pass 1: {below} now below 70 (description revealed years/clearance/etc).")


if __name__ == "__main__":
    main()
