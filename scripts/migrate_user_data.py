"""One-time migration: aux tables in SQLite → data/user_data.json.

Her data used to live in `jobs.db` and get copied into `jobs_deploy.db`, where
every deploy overwrote whatever she'd typed on the hosted dashboard. It now
lives in `data/user_data.json` instead — see src/user_store.py.

Reads both DBs and merges, so nothing is lost if one holds rows the other
doesn't. Idempotent: re-running merges into the existing JSON without
duplicating (dedup is by the natural key of each collection).

Usage: python scripts/migrate_user_data.py [--dry-run]
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import user_store

DATA = Path(__file__).parent.parent / "data"
SOURCES = [DATA / "jobs.db", DATA / "jobs_deploy.db"]

# collection -> (sqlite table, dedup key fields)
TABLES = {
    "applications": ("applications", ("job_id",)),
    "coffee_chats": ("coffee_chats", ("chat_date", "person_name")),
    "todos": ("todos", ("item",)),
    "interview_recs": ("interview_recs", ("recommendation",)),
    "star_stories": ("star_stories", ("title",)),
}


def read_table(db, table):
    if not db.exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
    except sqlite3.OperationalError:
        conn.close()
        return []  # table absent in this DB
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows


def key_of(row, fields):
    return tuple(str(row.get(f, "")).strip().lower() for f in fields)


def main():
    dry = "--dry-run" in sys.argv
    data = user_store.load(force=True)

    summary = []
    for collection, (table, dedup) in TABLES.items():
        existing = data.setdefault(collection, [])
        seen = {key_of(r, dedup) for r in existing}
        added = 0

        for db in SOURCES:
            for row in read_table(db, table):
                k = key_of(row, dedup)
                if k in seen or not any(k):
                    continue
                seen.add(k)
                row["id"] = max((r.get("id", 0) for r in existing), default=0) + 1
                existing.append(row)
                added += 1

        summary.append((collection, len(existing), added))

    for collection, total, added in summary:
        print(f"  {collection:16} {total:4d} records (+{added} new)")

    if dry:
        print("\n--dry-run: nothing written.")
        return

    user_store.save(data, "Migrate user data from SQLite to JSON store")
    print(f"\nWrote {user_store.PATH}")
    print("Remaining step: commit data/user_data.json so the dashboard can read it.")


if __name__ == "__main__":
    main()
