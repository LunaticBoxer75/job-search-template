"""Application status tracker — track which jobs have been applied to, skipped, or are in progress.

Application records live in src/user_store.py (durable JSON). Job details still
come from SQLite, so the join that used to happen in SQL now happens here.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from . import user_store

DATA = Path(__file__).parent.parent / "data"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

STATUSES = ["new", "applied", "interviewing", "offer", "rejected", "skipped"]


def _jobs_db():
    """Whichever jobs DB is present: the full one locally, the deploy one on Cloud.

    Deliberately does not import src.scraper — that pulls in jobspy, which the
    dashboard has no reason to load just to read job titles.
    """
    for name in ("jobs.db", "jobs_deploy.db"):
        path = DATA / name
        if path.exists():
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return None


def update_status(job_id, status, notes=None, follow_up_date=None):
    """Upsert the application record for a job."""
    if status not in STATUSES:
        raise ValueError(f"Status must be one of: {STATUSES}")

    # None means "leave unchanged" — a status change must not erase notes or a
    # queued follow-up. Pass "" to clear a field deliberately.
    fields = {"status": status}
    if notes is not None:
        fields["notes"] = notes
    if follow_up_date is not None:
        fields["follow_up_date"] = follow_up_date
    if status == "applied":
        fields["applied_date"] = datetime.now().isoformat()

    existing = next(
        (a for a in user_store.get("applications") if a.get("job_id") == job_id), None
    )
    if existing:
        user_store.update("applications", existing["id"], fields,
                          message=f"Mark application {status}")
    else:
        user_store.add("applications", {"job_id": job_id, **fields},
                       message=f"Mark application {status}")


def get_application(job_id):
    return next(
        (a for a in user_store.get("applications") if a.get("job_id") == job_id), None
    )


def get_application_summary():
    """Counts by status."""
    summary = {}
    for a in user_store.get("applications"):
        s = a.get("status") or "new"
        summary[s] = summary.get(s, 0) + 1
    return summary


def _job_details(job_ids):
    """Look up title/company/etc for a set of job_ids. Returns {job_id: dict}."""
    job_ids = [j for j in job_ids if j]
    if not job_ids:
        return {}
    conn = _jobs_db()
    if conn is None:
        return {}
    placeholders = ",".join("?" * len(job_ids))
    cursor = conn.execute(
        f"""SELECT job_id, title, company, location, job_url, score
            FROM jobs WHERE job_id IN ({placeholders})""",
        list(job_ids),
    )
    cols = [d[0] for d in cursor.description]
    out = {r[0]: dict(zip(cols, r)) for r in cursor.fetchall()}
    conn.close()
    return out


def get_applications_by_status(status):
    """All applications with a given status, enriched with job details."""
    apps = [a for a in user_store.get("applications") if a.get("status") == status]
    details = _job_details([a.get("job_id") for a in apps if a.get("job_id")])
    rows = []
    for a in apps:
        d = details.get(a.get("job_id"), {})
        rows.append({**d, **{k: v for k, v in a.items() if k != "id"}})
    return sorted(rows, key=lambda r: r.get("score") or 0, reverse=True)


def get_follow_ups_due():
    """Applications whose follow-up date has passed and are still live."""
    today = datetime.now().strftime("%Y-%m-%d")
    due = [
        a for a in user_store.get("applications")
        if a.get("follow_up_date")
        and str(a["follow_up_date"])[:10] <= today
        and a.get("status") in ("applied", "interviewing")
    ]
    details = _job_details([a.get("job_id") for a in due if a.get("job_id")])
    rows = []
    for a in due:
        d = details.get(a.get("job_id"), {})
        rows.append({
            "title": d.get("title", "(job no longer in DB)"),
            "company": d.get("company", ""),
            "job_url": d.get("job_url", ""),
            "follow_up_date": a.get("follow_up_date"),
            "notes": a.get("notes"),
        })
    return sorted(rows, key=lambda r: r.get("follow_up_date") or "")


def generate_tracker_report():
    """Generate application tracking report."""
    summary = get_application_summary()
    follow_ups = get_follow_ups_due()

    lines = [
        "# Application Tracker",
        "",
        "## Status Summary",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status in STATUSES:
        lines.append(f"| {status.capitalize()} | {summary.get(status, 0)} |")

    if follow_ups:
        lines.extend(["", "## Follow-ups Due", ""])
        for f in follow_ups:
            lines.append(f"- **{f['title']}** @ {f['company']} (due: {f['follow_up_date']})")
            if f.get("notes"):
                lines.append(f"  Notes: {f['notes']}")

    applied = get_applications_by_status("applied")
    if applied:
        lines.extend(["", "## Applied (Awaiting Response)", ""])
        for a in applied:
            when = str(a.get("applied_date") or "")[:10]
            lines.append(f"- [{a.get('score')}] **{a.get('title')}** @ {a.get('company')} — applied {when}")

    interviewing = get_applications_by_status("interviewing")
    if interviewing:
        lines.extend(["", "## In Interview Process", ""])
        for a in interviewing:
            lines.append(f"- **{a.get('title')}** @ {a.get('company')} — {a.get('notes') or ''}")

    return "\n".join(lines)
