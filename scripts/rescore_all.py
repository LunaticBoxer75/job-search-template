"""One-time: re-derive job_type (from title) and region (from location), and
rescore every row with Pass 1.

Run after changing src/quick_scorer.py's rules or the internship classifier, so
old and new rows share one scale. Never touches rows Claude scored in Pass 2
(score_breakdown.scorer == "ai") — those scores are the better ones.

Single transaction, so it either fully applies or not at all. Don't run it while a
scrape is writing to data/jobs.db.

Usage: python scripts/rescore_all.py [--apply]   (dry run without --apply)
"""

import collections
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.quick_scorer import score_row  # noqa: E402
from src.locations import region_of  # noqa: E402
from src.scraper import classify_job_type  # noqa: E402

DB = ROOT / "data" / "jobs.db"


def band(s):
    return "90+" if s >= 90 else "80-89" if s >= 80 else "70-79" if s >= 70 else "<70"


def main():
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    rows = conn.execute("""
        SELECT job_id, title, company, location, description, job_type, date_posted,
               scraped_at, is_remote, salary_min, score, region
        FROM jobs
        WHERE COALESCE(json_extract(score_breakdown, '$.scorer'), '') != 'ai'
    """).fetchall()

    now = datetime.now().isoformat()
    updates, moved = [], collections.Counter()
    before, after = collections.Counter(), collections.Counter()
    for (job_id, title, company, location, desc, job_type, posted, scraped,
         remote, sal, old, region) in rows:
        new_type = classify_job_type(title)
        if new_type != job_type:
            moved[f"{job_type} → {new_type}"] += 1
        new_region = region_of(location, remote, title)
        if new_region != region:
            moved[f"region {region} → {new_region}"] += 1
        score, breakdown = score_row(title, company, location, desc, new_type,
                                     posted, scraped, remote, sal)
        updates.append((new_type, new_region, score, breakdown, now, job_id))
        before[band(old or 0)] += 1
        after[band(score)] += 1

    print(f"{len(rows):,} rows (Pass-2 rows untouched)")
    print("job_type changes:", dict(moved) or "none")
    print(f"{'band':6} {'before':>8} {'after':>8}")
    for b in ("90+", "80-89", "70-79", "<70"):
        print(f"{b:6} {before[b]:>8,} {after[b]:>8,}")

    if not apply:
        print("Dry run — re-run with --apply to write.")
        return
    with conn:
        conn.executemany(
            "UPDATE jobs SET job_type = ?, region = ?, score = ?, score_breakdown = ?, scored_at = ? "
            "WHERE job_id = ?", updates)
    left = conn.execute("SELECT COUNT(*) FROM jobs WHERE score IS NULL").fetchone()[0]
    conn.close()
    print(f"Applied. Unscored rows: {left}")
    if left:
        sys.exit("INVARIANT VIOLATED: unscored rows remain")


if __name__ == "__main__":
    main()
