"""LinkedIn referral finder for top-scoring jobs.

Generates LinkedIn search URLs to find employees at target companies
for networking and referral introductions.

No API needed — uses LinkedIn's public search URL format.
"""

import json
import urllib.parse
from pathlib import Path

from . import profile
from .scraper import get_db

# General people search at a company
COMPANY_PEOPLE_SEARCH = "https://www.linkedin.com/search/results/people/?keywords={keywords}&company={company}"


def _linkedin_search_url(company, role_keywords=None, location=None):
    """Build a LinkedIn people search URL."""
    params = {}

    keywords_parts = []
    if role_keywords:
        keywords_parts.append(role_keywords)

    keywords = " ".join(keywords_parts) if keywords_parts else ""

    base = "https://www.linkedin.com/search/results/people/?"
    query_parts = []

    if keywords:
        query_parts.append(f"keywords={urllib.parse.quote(keywords)}")
    if company:
        query_parts.append(f"company={urllib.parse.quote(company)}")
    if location:
        query_parts.append(f"geoUrn={urllib.parse.quote(location)}")

    return base + "&".join(query_parts)


def _linkedin_company_url(company):
    """Build a URL to search for a company's LinkedIn page."""
    return f"https://www.linkedin.com/search/results/companies/?keywords={urllib.parse.quote(company)}"


def generate_referral_links(job):
    """Generate referral/networking links for a specific job."""
    company = job.get("company", "")
    title = job.get("title", "")
    location = job.get("location", "")

    # Extract role keywords from title
    role_words = title.lower().replace("sr.", "").replace("senior", "").replace("jr.", "").replace("junior", "").strip()

    links = []

    # 1. People in same role at that company
    links.append({
        "label": f"People in similar roles at {company}",
        "url": _linkedin_search_url(company, role_words),
        "purpose": "Find potential referrers doing the same job",
    })

    # 2. Recruiters/HR at that company
    links.append({
        "label": f"Recruiters at {company}",
        "url": _linkedin_search_url(company, "recruiter talent acquisition"),
        "purpose": "Find the recruiter handling this role",
    })

    # 3. Supply chain / procurement people at that company
    links.append({
        "label": f"Supply Chain team at {company}",
        "url": _linkedin_search_url(company, "supply chain procurement operations"),
        "purpose": "Find the hiring manager or team members",
    })

    # 4. Alumni of her school at that company
    ident = profile.identity()
    foster_url = (f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(company)}"
                  f"&schoolFilter=%5B%22{ident['linkedin_school_id']}%22%5D")
    links.append({
        "label": f"{ident['school_short']} alumni at {company}",
        "url": foster_url,
        "purpose": "Warm intro via shared school connection",
    })

    # 5. Company LinkedIn page
    links.append({
        "label": f"{company} LinkedIn page",
        "url": _linkedin_company_url(company),
        "purpose": "Follow for updates, see who's posting",
    })

    return links


def get_referral_report(min_score=75, limit=10):
    """Generate referral links for all top-scoring jobs."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT job_id, title, company, location, score, job_url
        FROM jobs
        WHERE score IS NOT NULL AND score >= ?
        ORDER BY score DESC
        LIMIT ?
    """, (min_score, limit))
    columns = [desc[0] for desc in cursor.description]
    jobs = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()

    report = []
    seen_companies = set()

    for job in jobs:
        company = job["company"]
        if company in seen_companies:
            continue
        seen_companies.add(company)

        links = generate_referral_links(job)
        report.append({
            "job": job,
            "links": links,
        })

    return report


def format_referral_report_markdown(report):
    """Format referral report as markdown."""
    lines = [
        "# Referral & Networking Links",
        "",
        f"Generated for top {len(report)} companies from highest-scoring jobs.",
        "",
        "---",
        "",
    ]

    for entry in report:
        job = entry["job"]
        lines.append(f"## {job['company']} — [{job['score']}] {job['title']}")
        lines.append(f"**Location:** {job['location']} | [Job Posting]({job['job_url']})")
        lines.append("")
        lines.append("| Who to Find | Link | Purpose |")
        lines.append("|-------------|------|---------|")
        for link in entry["links"]:
            lines.append(f"| {link['label']} | [Search →]({link['url']}) | {link['purpose']} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.extend([
        "## Outreach Template",
        "",
        "```",
        "Hi [Name],",
        "",
        f"I'm {profile.intro('long_intro')}.",
        "I noticed [Company] has a [Role] opening",
        "and I'd love to learn more about the team and culture.",
        "",
        "Would you be open to a 15-minute chat? Happy to work around your schedule.",
        "",
        "Best,",
        profile.load()["candidate"]["name"],
        "```",
    ])

    return "\n".join(lines)


if __name__ == "__main__":
    from rich.console import Console
    console = Console()

    report = get_referral_report(min_score=75, limit=10)
    if report:
        md = format_referral_report_markdown(report)
        console.print(md)

        output_dir = Path(__file__).parent.parent / "output"
        output_dir.mkdir(exist_ok=True)
        (output_dir / "referral-links.md").write_text(md)
        console.print(f"\n[green]Saved to output/referral-links.md[/green]")
    else:
        console.print("[yellow]No jobs scoring 75+. Run /find-jobs first.[/yellow]")

