"""Remove duplicate rows for the same posting URL, then enforce one row per URL.

Duplicates came from a bug: generate_job_id used Python's hash(), which is
salted per process, so each scrape gave the same posting a new id and
INSERT OR IGNORE inserted it again. The id is stable now (hashlib); this cleans
up the rows the old ids left behind and creates the unique index that makes
INSERT OR IGNORE dedup by URL from here on.

Same URL = same posting, so this is identity dedup, not content matching.

Per URL, keeps (in order): a row she has an application for or dismissed,
then an AI-scored row, then the highest score, then the earliest seen.

Usage: python scripts/dedupe_jobs.py [--apply]   (dry run without --apply)
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src import user_store  # noqa: E402

DB = ROOT / "data" / "jobs.db"


def main():
    apply = "--apply" in sys.argv
    protected = {a["job_id"] for a in user_store.get("applications") if a.get("job_id")}
    protected |= {d["job_id"] for d in user_store.get("dismissed_jobs") if d.get("job_id")}

    conn = sqlite3.connect(DB, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    rows = conn.execute("""
        SELECT job_id, job_url, score, score_breakdown, scraped_at FROM jobs
        WHERE job_url IN (SELECT job_url FROM jobs WHERE job_url != ''
                          GROUP BY job_url HAVING COUNT(*) > 1)
    """).fetchall()

    def is_ai(breakdown):
        try:
            return json.loads(breakdown or "{}").get("scorer") == "ai"
        except (ValueError, AttributeError):
            return False

    by_url = {}
    for r in rows:
        by_url.setdefault(r[1], []).append(r)

    drop = []
    for url, group in by_url.items():
        group.sort(key=lambda r: (
            r[0] not in protected,      # tracked/dismissed first
            not is_ai(r[3]),            # then AI-scored
            -(r[2] or 0),               # then highest score
            r[4] or "",                 # then earliest seen
        ))
        drop += [r[0] for r in group[1:]]

    protected_dropped = [j for j in drop if j in protected]
    print(f"{len(by_url):,} URLs with duplicates · {len(drop):,} rows to remove")
    if protected_dropped:
        # Two of her records point at the same posting; keep all rather than guess.
        drop = [j for j in drop if j not in protected]
        print(f"  keeping {len(protected_dropped)} extra rows she tracked/dismissed")

    if not apply:
        print("Dry run — re-run with --apply to delete.")
        return

    with conn:  # one transaction
        conn.executemany("DELETE FROM jobs WHERE job_id = ?", [(j,) for j in drop])
        if not protected_dropped:  # index would fail while kept duplicates remain
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url ON jobs(job_url) WHERE job_url != ''"
            )
    left = conn.execute(
        "SELECT COUNT(*) - COUNT(DISTINCT job_url) FROM jobs WHERE job_url != ''"
    ).fetchone()[0]
    index = "not created (resolve the kept duplicates first)" if protected_dropped else "created"
    print(f"Removed {len(drop):,} rows; duplicate URLs left: {left}; unique index on job_url {index}.")
    conn.close()


if __name__ == "__main__":
    main()
