"""Main pipeline orchestrator - runs the full daily job search flow."""

import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .scraper import scrape_all_jobs
from .scorer import score_all_pending
from .reporter import generate_daily_report, generate_csv_export, get_stats

console = Console()


def run_full_pipeline(hours_old=24, score_limit=50, skip_scrape=False, skip_score=False):
    """Run the complete daily pipeline: scrape -> score -> report."""
    console.print(f"\n[bold blue]{'='*60}[/bold blue]")
    console.print(f"[bold blue]  Job Search Pipeline - {datetime.now().strftime('%Y-%m-%d %H:%M')}[/bold blue]")
    console.print(f"[bold blue]{'='*60}[/bold blue]\n")

    # Step 1: Scrape
    if not skip_scrape:
        console.print("[bold]Step 1/3: Scraping job boards...[/bold]")
        try:
            new_jobs = scrape_all_jobs(hours_old=hours_old)
            console.print(f"  [green]Found {len(new_jobs)} new jobs[/green]")
        except Exception as e:
            console.print(f"  [red]Scraping error: {e}[/red]")
            new_jobs = []
    else:
        console.print("[dim]Step 1/3: Scraping skipped[/dim]")
        new_jobs = []

    # Step 2: Score
    if not skip_score:
        console.print("\n[bold]Step 2/3: Scoring jobs with AI...[/bold]")
        try:
            scored = score_all_pending(limit=50)
            console.print(f"  [green]Scored {len(scored)} jobs[/green]")
            if scored:
                top = max(scored, key=lambda x: x.get("score", 0))
                console.print(f"  [cyan]Best match: {top['title']} @ {top['company']} (score: {top['score']})[/cyan]")
        except Exception as e:
            console.print(f"  [red]Scoring error: {e}[/red]")
            scored = []
    else:
        console.print("[dim]Step 2/3: Scoring skipped[/dim]")
        scored = []

    # Step 3: Report
    console.print("\n[bold]Step 3/3: Generating report...[/bold]")
    report = generate_daily_report(min_score=score_limit)
    csv_path, csv_count = generate_csv_export(min_score=0)
    console.print(f"  [green]Report generated with jobs scoring {score_limit}+[/green]")
    console.print(f"  [green]CSV exported: {csv_path} ({csv_count} jobs)[/green]")

    # Summary
    stats = get_stats()
    console.print(f"\n[bold]Summary:[/bold]")

    table = Table()
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total jobs in DB", str(stats["total_jobs"]))
    table.add_row("Scored", str(stats["scored_jobs"]))
    table.add_row("Awaiting scoring", str(stats["unscored_jobs"]))
    table.add_row("High matches (75+)", str(stats["high_matches"]))
    table.add_row("Good matches (60-74)", str(stats["good_matches"]))
    console.print(table)

    if stats.get("top_companies"):
        console.print("\n[bold]Top Companies by Avg Score:[/bold]")
        for c in stats["top_companies"][:5]:
            console.print(f"  {c['company']}: {c['avg_score']} avg ({c['count']} jobs)")

    console.print(f"\n[bold green]Pipeline complete![/bold green]")
    return report


def run_scrape_only(hours_old=72):
    """Just scrape, don't score."""
    console.print("[bold]Scraping job boards (last {hours_old}h)...[/bold]")
    new_jobs = scrape_all_jobs(hours_old=hours_old)
    console.print(f"[green]Found {len(new_jobs)} new jobs[/green]")
    return new_jobs


def run_score_only(limit=50):
    """Just score pending jobs."""
    console.print("[bold]Scoring pending jobs...[/bold]")
    scored = score_all_pending(limit=limit)
    console.print(f"[green]Scored {len(scored)} jobs[/green]")
    return scored


if __name__ == "__main__":
    args = sys.argv[1:]
    if "scrape" in args:
        run_scrape_only()
    elif "score" in args:
        run_score_only()
    elif "report" in args:
        report = generate_daily_report()
        console.print(report)
    else:
        run_full_pipeline()
