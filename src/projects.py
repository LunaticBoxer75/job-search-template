"""Portfolio project generator — identifies skill gaps and builds complete project packages.

The workflow:
1. Analyze top-scoring job descriptions to find commonly requested skills
2. Compare against the candidate's existing skills to identify gaps
3. Only generate a project when a gap is genuinely significant (15%+ of top jobs)
4. Each project is a COMPLETE package: README + runnable code + requirements

Projects are generated via /generate-projects which uses Claude inline to build
detailed, beginner-friendly project guides with working code.
"""

import json
from datetime import datetime
from pathlib import Path

from . import profile
from .scraper import get_db

OUTPUT_DIR = Path(__file__).parent.parent / "output"
PROJECTS_DIR = Path(__file__).parent.parent / "projects"

# Skills the candidate already has — config/profile.json
EXISTING_SKILLS = set(profile.load()["skills_have"]["projects"])

# Skill detection keywords
SKILL_KEYWORDS = {
    "python": ["python", "pandas", "numpy", "scikit", "jupyter"],
    "tableau": ["tableau"],
    "power_bi": ["power bi", "powerbi", "power-bi"],
    "sap": ["sap", "s/4hana", "s4hana", "sap erp"],
    "r_programming": ["r programming", "rstudio", "tidyverse"],
    "excel_advanced": ["vba", "macros", "power query", "power pivot"],
    "forecasting": ["forecasting", "demand planning", "time series", "s&op"],
    "procurement": ["procurement", "sourcing", "rfp", "rfq", "vendor management"],
    "logistics": ["logistics", "freight", "3pl", "wms", "warehouse management"],
    "erp_systems": ["erp", "oracle scm", "netsuite", "blue yonder", "kinaxis"],
    "data_visualization": ["data visualization", "dashboard", "reporting tools"],
    "supply_chain_analytics": ["supply chain analytics", "inventory optimization", "network design"],
}


def identify_skill_gaps(min_score=55, min_jobs=50):
    """Analyze job listings to find most-requested skills the candidate doesn't have."""
    conn = get_db()
    cursor = conn.execute("""
        SELECT description FROM jobs
        WHERE score IS NOT NULL AND score >= ?
        ORDER BY score DESC LIMIT ?
    """, (min_score, min_jobs))
    descriptions = [row[0] for row in cursor.fetchall() if row[0]]
    conn.close()

    if not descriptions:
        return []

    gaps = {}
    for skill, keywords in SKILL_KEYWORDS.items():
        count = sum(1 for d in descriptions if any(kw.lower() in d.lower() for kw in keywords))
        if count > 0 and skill not in EXISTING_SKILLS:
            gaps[skill] = count

    return sorted(gaps.items(), key=lambda x: x[1], reverse=True)


def get_existing_projects():
    """List projects that already exist."""
    if not PROJECTS_DIR.exists():
        return []
    return [d.name for d in PROJECTS_DIR.iterdir() if d.is_dir() and (d / "README.md").exists()]


def should_generate_project(gap_name, gap_count, total_jobs):
    """Decide if a gap warrants a new project."""
    existing = get_existing_projects()

    # Check if already covered
    if gap_name in existing or gap_name.replace("_", "-") in existing:
        return False

    # Map gap names to existing project dirs
    coverage_map = {
        "python": "python-demand-forecasting",
        "tableau": "tableau-supply-chain-kpis",
        "power_bi": "tableau-supply-chain-kpis",  # covered by same viz project
        "forecasting": "python-demand-forecasting",  # covered by forecasting project
    }
    if coverage_map.get(gap_name) in existing:
        return False

    # Only generate if significant (15%+ of top jobs mention it)
    percentage = (gap_count / total_jobs * 100) if total_jobs > 0 else 0
    return percentage >= 15


def get_gaps_needing_projects():
    """Get skill gaps that need a project built."""
    gaps = identify_skill_gaps()
    if not gaps:
        return []

    conn = get_db()
    total_jobs = conn.execute("SELECT COUNT(*) FROM jobs WHERE score >= 55").fetchone()[0]
    conn.close()

    return [
        (name, count, round(count / total_jobs * 100, 1))
        for name, count in gaps
        if should_generate_project(name, count, total_jobs)
    ]


def generate_project_index():
    """Regenerate the projects INDEX.md."""
    PROJECTS_DIR.mkdir(exist_ok=True)
    existing = get_existing_projects()

    if not existing:
        return

    lines = [
        "# Portfolio Projects for the Job Search",
        "",
        "Projects generated based on skill gaps found in target job listings.",
        "",
        "| # | Project | Duration | Status |",
        "|---|---------|----------|--------|",
    ]

    for i, pname in enumerate(sorted(existing), 1):
        readme = PROJECTS_DIR / pname / "README.md"
        title = pname.replace("-", " ").title()
        if readme.exists():
            first_line = readme.read_text().split("\n")[0].replace("# ", "")
            if first_line:
                title = first_line

        has_code = any((PROJECTS_DIR / pname).glob("*.py"))
        status = "✅ Complete" if has_code else "📝 Guide only"
        lines.append(f"| {i} | [{title}]({pname}/README.md) | 1-2 weeks | {status} |")

    lines.extend([
        "",
        "## How to Use",
        "",
        "1. Pick a project that addresses your biggest skill gap",
        "2. Follow the README step-by-step (designed for beginners)",
        "3. Complete it and add the resume line to your resume",
        "4. Put the code on GitHub for portfolio visibility",
    ])

    (PROJECTS_DIR / "INDEX.md").write_text("\n".join(lines))


if __name__ == "__main__":
    from rich.console import Console
    console = Console()

    gaps = identify_skill_gaps()
    if gaps:
        console.print("[bold]Skill Gaps Found:[/bold]")
        for name, count in gaps:
            console.print(f"  {name}: requested in {count} jobs")

        needs_project = get_gaps_needing_projects()
        if needs_project:
            console.print(f"\n[bold yellow]{len(needs_project)} gaps need projects:[/bold yellow]")
            for name, count, pct in needs_project:
                console.print(f"  [yellow]• {name}[/yellow] ({pct}% of top jobs)")
            console.print("\nRun [bold]/generate-projects[/bold] in Claude Code to build them.")
        else:
            console.print("\n[green]All significant gaps are covered by existing projects.[/green]")
    else:
        console.print("[yellow]No scored jobs yet. Run /find-jobs first.[/yellow]")

    generate_project_index()
