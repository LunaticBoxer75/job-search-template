"""Generate daily job reports with separate dashboards by region and job type."""

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import profile
from .scraper import get_db

OUTPUT_DIR = Path(__file__).parent.parent / "output"
_BENCH = profile.identity()["salary_benchmark"]


def _format_job_entry(job):
    """Format a single job for markdown output."""
    score = job.get("score", 0)
    title = job.get("title", "Unknown")
    company = job.get("company", "Unknown")
    location = job.get("location", "Unknown")
    url = job.get("job_url", "")
    salary_str = ""
    if job.get("salary_min") and job.get("salary_max"):
        salary_str = f" | ${job['salary_min']:,.0f}-${job['salary_max']:,.0f}"
    elif job.get("salary_min"):
        salary_str = f" | ${job['salary_min']:,.0f}+"

    breakdown = {}
    if job.get("score_breakdown"):
        try:
            breakdown = json.loads(job["score_breakdown"]) if isinstance(job["score_breakdown"], str) else job["score_breakdown"]
        except (json.JSONDecodeError, TypeError):
            pass

    reasoning = breakdown.get("reasoning", "")
    highlights = breakdown.get("highlights", [])

    lines = []
    lines.append(f"### [{score}] {title} @ {company}")
    lines.append(f"**Location:** {location}{salary_str}")
    if url:
        lines.append(f"**Apply:** {url}")
    if reasoning:
        lines.append(f"**Why:** {reasoning}")
    if highlights:
        lines.append(f"**Highlights:** {', '.join(highlights)}")
    lines.append("")
    return "\n".join(lines)


def _section_jobs(jobs, section_title):
    """Generate a section with tiered jobs."""
    if not jobs:
        return f"## {section_title}\n\n_No matches found._\n\n"

    lines = [f"## {section_title}", f"_{len(jobs)} matches_\n"]

    tier_labels = {
        (90, 101): "Excellent (90+)",
        (75, 90): "Strong (75-89)",
        (60, 75): "Good (60-74)",
        (50, 60): "Consider (50-59)",
    }

    for (low, high), label in tier_labels.items():
        tier_jobs = [j for j in jobs if low <= (j.get("score") or 0) < high]
        if not tier_jobs:
            continue
        lines.append(f"#### {label}")
        lines.append("")
        for job in tier_jobs:
            lines.append(_format_job_entry(job))

    return "\n".join(lines)


def generate_daily_report(min_score=50, limit=50):
    """Generate a markdown report split by region and job type."""
    OUTPUT_DIR.mkdir(exist_ok=True)

    conn = get_db()
    cursor = conn.execute("""
        SELECT * FROM jobs
        WHERE score IS NOT NULL AND score >= ?
        ORDER BY score DESC
        LIMIT ?
    """, (min_score, limit * 4))
    columns = [desc[0] for desc in cursor.description]
    all_jobs = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()

    if not all_jobs:
        return "No scored jobs found above threshold."

    today = datetime.now().strftime("%Y-%m-%d")

    # Split into 4 panels
    seattle_ft = [j for j in all_jobs if j.get("region") == "seattle" and j.get("job_type") != "internship"]
    seattle_intern = [j for j in all_jobs if j.get("region") == "seattle" and j.get("job_type") == "internship"]
    national_ft = [j for j in all_jobs if j.get("region") == "national" and j.get("job_type") != "internship"]
    national_intern = [j for j in all_jobs if j.get("region") == "national" and j.get("job_type") == "internship"]
    # Jobs without region tag go to seattle (legacy data)
    untagged = [j for j in all_jobs if not j.get("region")]
    seattle_ft.extend([j for j in untagged if j.get("job_type") != "internship"])
    seattle_intern.extend([j for j in untagged if j.get("job_type") == "internship"])

    # Salary benchmark
    salaries = [j["salary_min"] for j in all_jobs if j.get("salary_min")]
    avg_salary_str = f"${sum(salaries)/len(salaries):,.0f}" if salaries else "N/A"
    above_mscm = len([s for s in salaries if s >= _BENCH["amount"]])

    report_lines = [
        f"# Daily Job Report - {today}",
        f"",
        f"**Total matches:** {len(all_jobs)} jobs scoring {min_score}+",
        f"**Salary benchmark:** Avg found: {avg_salary_str} | {_BENCH['label']}: ${_BENCH['amount']:,} | {above_mscm} jobs above avg",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"---",
        f"",
    ]

    report_lines.append(_section_jobs(seattle_ft, f"{profile.home_label()} — Full-Time"))
    report_lines.append(_section_jobs(seattle_intern, f"{profile.home_label()} — Internships"))
    report_lines.append(_section_jobs(national_ft, "Rest of USA — Full-Time"))
    report_lines.append(_section_jobs(national_intern, "Rest of USA — Internships"))

    report_content = "\n".join(report_lines)

    report_path = OUTPUT_DIR / f"report-{today}.md"
    report_path.write_text(report_content)

    return report_content


def generate_csv_export(min_score=0):
    """Export all scored jobs to CSV."""
    import csv

    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    conn = get_db()
    cursor = conn.execute("""
        SELECT job_id, title, company, location, job_url, job_type,
               date_posted, salary_min, salary_max, source, search_term,
               search_category, score, scored_at, is_remote, region
        FROM jobs
        WHERE score IS NOT NULL AND score >= ?
        ORDER BY score DESC
    """, (min_score,))
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    conn.close()

    csv_path = OUTPUT_DIR / f"jobs-{today}.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    return str(csv_path), len(rows)


def get_stats():
    """Get summary statistics."""
    conn = get_db()
    stats = {}
    stats["total_jobs"] = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    stats["scored_jobs"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE score IS NOT NULL").fetchone()[0]
    stats["unscored_jobs"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE score IS NULL").fetchone()[0]
    stats["high_matches"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE score >= 75").fetchone()[0]
    stats["good_matches"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE score >= 60 AND score < 75").fetchone()[0]

    # By region
    stats["seattle_jobs"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE region = 'seattle'").fetchone()[0]
    stats["national_jobs"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE region = 'national'").fetchone()[0]

    # By type
    stats["fulltime_jobs"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE job_type = 'fulltime'").fetchone()[0]
    stats["internship_jobs"] = conn.execute("SELECT COUNT(*) FROM jobs WHERE job_type = 'internship'").fetchone()[0]

    top_companies = conn.execute("""
        SELECT company, COUNT(*) as cnt, AVG(score) as avg_score
        FROM jobs WHERE score IS NOT NULL
        GROUP BY company ORDER BY avg_score DESC LIMIT 10
    """).fetchall()
    stats["top_companies"] = [{"company": r[0], "count": r[1], "avg_score": round(r[2], 1)} for r in top_companies]
    conn.close()
    return stats


def generate_skills_gap_report():
    """Analyze commonly requested skills across top jobs that the candidate may not have."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT description FROM jobs
        WHERE score IS NOT NULL AND score >= 60
        ORDER BY score DESC LIMIT 50
    """)
    descriptions = [row[0] for row in cursor.fetchall() if row[0]]
    conn.close()

    if not descriptions:
        return "Not enough scored jobs to analyze skills gaps."

    # Common supply chain skills/tools to check for
    skills_to_check = {
        "SAP": ["sap", "sap erp", "s/4hana"],
        "Tableau": ["tableau"],
        "Power BI": ["power bi", "powerbi"],
        "Python": ["python"],
        "R": ["r programming", " r,", " r "],
        "Excel Advanced": ["vlookup", "pivot table", "macros", "vba"],
        "SQL": ["sql", "mysql", "postgresql"],
        "Supply Chain Software": ["oracle scm", "blue yonder", "kinaxis", "llamasoft", "o9"],
        "ERP Systems": ["erp", "oracle", "netsuite"],
        "Lean/Six Sigma": ["lean", "six sigma", "kaizen", "dmaic"],
        "APICS/CSCP": ["apics", "cscp", "cpim", "cltd"],
        "Forecasting": ["forecasting", "demand planning", "s&op"],
        "Procurement": ["procurement", "sourcing", "rfp", "rfq"],
        "Logistics": ["logistics", "freight", "3pl", "warehouse management", "wms"],
        "Data Visualization": ["data visualization", "dashboard"],
        "Project Management": ["pmp", "agile", "scrum", "project management"],
        "Communication": ["stakeholder", "cross-functional", "presentation"],
    }

    has_skills = set(profile.load()["skills_have"]["report"])

    skill_frequency = {}
    total_jobs = len(descriptions)

    for skill_name, keywords in skills_to_check.items():
        count = 0
        for desc in descriptions:
            desc_lower = desc.lower()
            if any(kw.lower() in desc_lower for kw in keywords):
                count += 1
        if count > 0:
            skill_frequency[skill_name] = {
                "count": count,
                "percentage": round(count / total_jobs * 100, 1),
                "has_skill": skill_name in has_skills,
            }

    # Sort by frequency
    sorted_skills = sorted(skill_frequency.items(), key=lambda x: x[1]["count"], reverse=True)

    lines = [
        "# Skills Gap Analysis",
        "",
        f"Based on {total_jobs} top-scoring job listings (score 60+).",
        "",
        "## Most Requested Skills",
        "",
        "| Skill | Requested In | Has It? | Priority |",
        "|-------|-------------|---------|----------|",
    ]

    for skill, data in sorted_skills:
        has = "Yes" if data["has_skill"] else "**GAP**"
        priority = "—" if data["has_skill"] else ("HIGH" if data["percentage"] > 40 else "Medium" if data["percentage"] > 20 else "Low")
        lines.append(f"| {skill} | {data['percentage']}% ({data['count']}/{total_jobs}) | {has} | {priority} |")

    lines.extend([
        "",
        "## Recommended Focus Areas",
        "",
        f"Skills most commonly requested that {profile.first_name()} should prioritize:",
        "",
    ])

    gaps = [(s, d) for s, d in sorted_skills if not d["has_skill"] and d["percentage"] > 15]
    for i, (skill, data) in enumerate(gaps[:7], 1):
        lines.append(f"{i}. **{skill}** — requested in {data['percentage']}% of top matches")

    OUTPUT_DIR.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    report_content = "\n".join(lines)
    (OUTPUT_DIR / f"skills-gap-{today}.md").write_text(report_content)
    return report_content


def generate_weekly_trends():
    """Show what's trending up/down over the past week."""
    conn = get_db()

    # Jobs from last 7 days vs prior 7 days
    now = datetime.now()
    week_ago = (now - timedelta(days=7)).isoformat()
    two_weeks_ago = (now - timedelta(days=14)).isoformat()

    this_week = conn.execute("""
        SELECT search_term, COUNT(*) as cnt FROM jobs
        WHERE scraped_at >= ? GROUP BY search_term
    """, (week_ago,)).fetchall()

    last_week = conn.execute("""
        SELECT search_term, COUNT(*) as cnt FROM jobs
        WHERE scraped_at >= ? AND scraped_at < ? GROUP BY search_term
    """, (two_weeks_ago, week_ago)).fetchall()

    conn.close()

    this_week_map = {row[0]: row[1] for row in this_week}
    last_week_map = {row[0]: row[1] for row in last_week}

    all_terms = set(this_week_map.keys()) | set(last_week_map.keys())
    trends = []
    for term in all_terms:
        current = this_week_map.get(term, 0)
        previous = last_week_map.get(term, 0)
        if previous > 0:
            change = ((current - previous) / previous) * 100
        elif current > 0:
            change = 100
        else:
            change = 0
        trends.append({"term": term, "this_week": current, "last_week": previous, "change": change})

    trends.sort(key=lambda x: x["change"], reverse=True)

    lines = ["# Weekly Trends", "", "| Search Term | This Week | Last Week | Trend |", "|-------------|-----------|-----------|-------|"]
    for t in trends:
        arrow = "+" if t["change"] > 0 else ("-" if t["change"] < 0 else "=")
        lines.append(f"| {t['term']} | {t['this_week']} | {t['last_week']} | {arrow}{abs(t['change']):.0f}% |")

    return "\n".join(lines)


if __name__ == "__main__":
    from rich.console import Console
    console = Console()
    report = generate_daily_report()
    console.print(report)
