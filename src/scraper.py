"""Job scraper using JobSpy to aggregate listings from multiple boards."""

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
from jobspy import scrape_jobs

CONFIG_DIR = Path(__file__).parent.parent / "config"
DATA_DIR = Path(__file__).parent.parent / "data"


def load_config():
    with open(CONFIG_DIR / "profile.json") as f:
        profile = json.load(f)
    with open(CONFIG_DIR / "search_queries.json") as f:
        queries = json.load(f)
    return profile, queries


def get_db():
    DATA_DIR.mkdir(exist_ok=True)
    db_path = DATA_DIR / "jobs.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            job_url TEXT,
            description TEXT,
            job_type TEXT,
            date_posted TEXT,
            salary_min REAL,
            salary_max REAL,
            salary_currency TEXT,
            source TEXT,
            search_term TEXT,
            search_category TEXT,
            scraped_at TEXT,
            score REAL,
            score_breakdown TEXT,
            scored_at TEXT,
            is_remote INTEGER DEFAULT 0,
            notified INTEGER DEFAULT 0,
            region TEXT DEFAULT 'seattle'
        )
    """)
    # Columns added after the first schema (the export reads `dismissed`)
    for col in ("region TEXT DEFAULT 'seattle'", "dismissed INTEGER DEFAULT 0"):
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    # One row per posting URL, so INSERT OR IGNORE dedups by URL even for rows
    # stored under the old per-process random ids. Creation fails while duplicate
    # URLs exist; scripts/dedupe_jobs.py removes them and creates it.
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url ON jobs(job_url) WHERE job_url != ''")
    except sqlite3.IntegrityError:
        pass
    # Postings Claude read and rated below 70 are deleted; their URL is remembered here so
    # the next scrape (same 7-day window) doesn't re-insert them for Claude to re-read daily.
    conn.execute("""CREATE TABLE IF NOT EXISTS rejected_urls
                    (job_url TEXT PRIMARY KEY, reason TEXT, rejected_at TEXT)""")
    conn.commit()
    return conn


# ---------- parallel fetch + LinkedIn block detection ----------

_BLOCK_COOLDOWN = 120          # seconds every worker waits after LinkedIn returns 429
_cooldown_until = 0.0
_blocks = 0
_block_lock = threading.Lock()


class _BlockWatcher(logging.Handler):
    """JobSpy logs a LinkedIn 429 and silently returns partial results; catch it."""

    def emit(self, record):
        global _cooldown_until, _blocks
        if "429" in record.getMessage():
            with _block_lock:
                _blocks += 1
                _cooldown_until = time.time() + _BLOCK_COOLDOWN
            print(f"  [!] LinkedIn 429 (blocked) — all workers pausing {_BLOCK_COOLDOWN}s", flush=True)


_watcher = _BlockWatcher(level=logging.ERROR)
for _name in list(logging.root.manager.loggerDict):
    if _name.startswith("JobSpy:"):
        logging.getLogger(_name).addHandler(_watcher)


def _fetch(task, sites, results, hours_old, country):
    """Run one search (in a worker thread). Returns (task, df, error)."""
    region_tag, jtype, search_term, category, location = task
    wait = _cooldown_until - time.time()
    if wait > 0:
        time.sleep(wait)  # back off only after an actual block
    try:
        df = scrape_jobs(
            site_name=sites, search_term=search_term, location=location,
            results_wanted=results, hours_old=hours_old, country_indeed=country,
            job_type=jtype, description_format="markdown", verbose=0,
        )
        return task, df, None
    except Exception as e:
        return task, None, e


# An internship only if the TITLE says so. The job boards' internship filter is
# loose: 70% of rows it returned (12,729 of 18,297 by 2026-09) were ordinary jobs —
# "Program Manager", "Audit and Compliance Senior" — which then got filed, capped,
# and hidden as internships. New-grad roles count as full-time.
_INTERN_RE = re.compile(r"\b(internships?|interns?|co-?ops?)\b|\b(summer|fall|winter|spring)\s+20\d\d\b")


def classify_job_type(title):
    """'internship' or 'fulltime', from the title alone — never the search filter."""
    return "internship" if _INTERN_RE.search(str(title or "").lower()) else "fulltime"


def _row_to_job(row, jtype, search_term, category, region_tag):
    """Normalize one JobSpy row into a DB record, or None if it's unusable."""
    title_str = str(row.get("title", "")) if pd.notna(row.get("title")) else ""
    company_str = str(row.get("company", "")) if pd.notna(row.get("company")) else ""
    if not title_str or not company_str:
        return None

    # Word boundaries: plain substrings matched "Internal" (1,323 mis-tags) and
    # "Cooper"/"Cooperative".
    effective_type = classify_job_type(title_str)

    from .locations import region_of  # lazy: locations -> quick_scorer -> scraper
    location_str = str(row.get("location", "")) if pd.notna(row.get("location")) else ""
    return {
        "job_id": generate_job_id(row),
        "title": title_str,
        "company": company_str,
        "location": location_str,
        "job_url": str(row.get("job_url", "")) if pd.notna(row.get("job_url")) else "",
        "description": str(row.get("description", ""))[:10000] if pd.notna(row.get("description")) else "",
        "job_type": effective_type,
        # pd.notna guard: str(NaN) is the literal "nan", which sorted after every real
        # date and passed every "Last N days" filter (9,445 rows, 2026-09).
        "date_posted": str(row.get("date_posted"))[:10] if pd.notna(row.get("date_posted")) else None,
        "salary_min": float(row["min_amount"]) if pd.notna(row.get("min_amount")) else None,
        "salary_max": float(row["max_amount"]) if pd.notna(row.get("max_amount")) else None,
        "salary_currency": str(row.get("currency")) if pd.notna(row.get("currency")) else "USD",
        "source": str(row.get("site")) if pd.notna(row.get("site")) else "",
        "search_term": search_term,
        "search_category": category,
        "scraped_at": datetime.now().isoformat(),
        "is_remote": 1 if row.get("is_remote") else 0,
        # Where the job IS, not which search found it — a Seattle search also
        # returns Olympia, Spokane and out-of-state postings (241 misfiled at 70+).
        "region": region_of(location_str, row.get("is_remote"), title_str),
    }


def _progress(done, total, started, region, jtype, term, location, new, skipped=0):
    """One line per query, so a hung run is distinguishable from a slow one."""
    elapsed = (datetime.now() - started).total_seconds()
    eta = elapsed / done * (total - done) if done else 0
    blocks = f" | {_blocks} LinkedIn blocks" if _blocks else ""
    print(f"[{done}/{total}] {region}/{jtype} '{term}' @ {location}: +{new} new "
          f"({skipped:,} below 70 skipped so far) | {elapsed/60:.0f}m elapsed, ~{eta/60:.0f}m left{blocks}",
          flush=True)


def generate_job_id(row):
    """Stable ID from job URL (or title+company+location when there's no URL).

    hashlib, not hash(): Python salts str hashes per process, so hash(url) gave
    the same posting a new id every run and INSERT OR IGNORE never ignored
    anything — 5,566 duplicate rows by 2026-09.
    """
    if pd.notna(row.get("job_url")) and row["job_url"]:
        key = str(row["job_url"])
    else:
        key = f"{row.get('title', '')}-{row.get('company', '')}-{row.get('location', '')}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def scrape_all_jobs(sites=None, hours_old=None, region=None):
    """Scrape jobs from all configured queries and return new (unseen) jobs.

    region: 'seattle', 'national', or None (both).
    """
    profile, queries = load_config()
    prefs = profile["search_preferences"]

    if sites is None:
        sites = ["indeed", "linkedin"]
    if hours_old is None:
        hours_old = prefs["hours_old"]

    locations_seattle = prefs.get("locations_home") or prefs["locations_seattle"]  # home-area searches
    locations_national = prefs["locations_national"]

    if region == "seattle":
        location_groups = [("seattle", locations_seattle)]
    elif region == "national":
        location_groups = [("national", locations_national)]
    else:
        location_groups = [("seattle", locations_seattle), ("national", locations_national)]

    job_types_to_scrape = prefs["job_types"]

    tasks = [
        (region_tag, jtype, query["search_term"], query["category"], location)
        for region_tag, locations in location_groups
        for jtype in job_types_to_scrape
        for query in queries["queries"]
        for location in locations
        if location != "Remote"
    ]
    workers = int(prefs.get("scrape_workers", 3))
    fetch_args = dict(sites=sites, results=prefs["results_per_search"], hours_old=hours_old,
                      country=prefs["country"])

    # Score before storing and keep only postings that could reach her: the user's
    # rule (2026-09-27) is "skip <70 always". ~80% of what the boards return is
    # retail/restaurant/driving noise from loose keyword matching. Imported here
    # because quick_scorer imports this module.
    from .quick_scorer import score_row
    min_store = float(prefs.get("min_store_score", 70))
    skipped = 0

    conn = get_db()
    rejected = {r[0] for r in conn.execute("SELECT job_url FROM rejected_urls")}
    all_new_jobs = []
    done, total, started = 0, len(tasks), datetime.now()

    # Searches run in parallel; LinkedIn's per-request pauses (3-7s) are what make
    # each search take ~50s, not CPU, so 3 at once is ~3x faster. All DB writes
    # stay on this thread — concurrent writers have corrupted jobs.db before.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_fetch, task, **fetch_args) for task in tasks]
        for fut in as_completed(futures):
            (region_tag, jtype, search_term, category, location), jobs_df, err = fut.result()
            done += 1
            if err:
                print(f"  [!] Error scraping '{search_term}' ({jtype}) in {location}: {err}", flush=True)
                continue
            before = len(all_new_jobs)
            if jobs_df is not None and not jobs_df.empty:
                for _, row in jobs_df.iterrows():
                    job_data = _row_to_job(row, jtype, search_term, category, region_tag)
                    if job_data is None or job_data["job_url"] in rejected:
                        continue
                    score, breakdown = score_row(
                        job_data["title"], job_data["company"], job_data["location"],
                        job_data["description"], job_data["job_type"], job_data["date_posted"],
                        job_data["scraped_at"], job_data["is_remote"], job_data["salary_min"])
                    if score < min_store:
                        skipped += 1
                        continue
                    job_data.update(score=score, score_breakdown=breakdown,
                                    scored_at=job_data["scraped_at"])
                    cur = conn.execute("""
                        INSERT OR IGNORE INTO jobs
                        (job_id, title, company, location, job_url, description, job_type,
                         date_posted, salary_min, salary_max, salary_currency, source,
                         search_term, search_category, scraped_at, is_remote, region,
                         score, score_breakdown, scored_at)
                        VALUES (:job_id, :title, :company, :location, :job_url, :description,
                                :job_type, :date_posted, :salary_min, :salary_max,
                                :salary_currency, :source, :search_term, :search_category,
                                :scraped_at, :is_remote, :region,
                                :score, :score_breakdown, :scored_at)
                    """, job_data)
                    # rowcount is 0 when OR IGNORE skipped a posting we already have
                    if cur.rowcount == 1:
                        all_new_jobs.append(job_data)
                # Commit per search: one end-of-run commit held a write transaction
                # open for hours and lost the whole run if the scrape hung late.
                conn.commit()
            _progress(done, total, started, region_tag, jtype, search_term, location,
                      len(all_new_jobs) - before, skipped)

    conn.commit()
    conn.close()

    # ALWAYS quick-score new jobs immediately — never leave unscored
    if all_new_jobs:
        try:
            from .quick_scorer import score_all_unscored
            score_all_unscored()
        except Exception:
            pass

    return all_new_jobs


def get_unscored_jobs(limit=50):
    """Fetch jobs that haven't been scored yet."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT * FROM jobs
        WHERE score IS NULL
        ORDER BY scraped_at DESC
        LIMIT ?
    """, (limit,))
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


def update_job_score(job_id, score, breakdown):
    """Update a job's score in the database."""
    conn = get_db()
    conn.execute("""
        UPDATE jobs SET score = ?, score_breakdown = ?, scored_at = ?
        WHERE job_id = ?
    """, (score, json.dumps(breakdown), datetime.now().isoformat(), job_id))
    conn.commit()
    conn.close()


def get_top_jobs(min_score=60, limit=20):
    """Get top-scored jobs."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT * FROM jobs
        WHERE score >= ?
        ORDER BY score DESC
        LIMIT ?
    """, (min_score, limit))
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    conn.close()
    return [dict(zip(columns, row)) for row in rows]


if __name__ == "__main__":
    from rich.console import Console
    console = Console()
    console.print("[bold]Scraping jobs...[/bold]")
    new_jobs = scrape_all_jobs()
    console.print(f"[green]Found {len(new_jobs)} new jobs[/green]")
