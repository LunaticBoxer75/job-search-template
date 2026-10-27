"""Refresh H1B sponsor data from live USCIS H-1B Employer Data Hub.

Source: https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub
Downloads the latest fiscal-year CSV of actual H-1B petition approvals per employer,
aggregates by employer, and writes a verified sponsor lookup to
config/h1b_sponsors_verified.json.

This is REAL government data — every count is an actual USCIS petition approval.
Run periodically (data updates ~annually).
"""

import csv
import io
import json
import subprocess
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"
OUTPUT = CONFIG_DIR / "h1b_sponsors_verified.json"

# USCIS publishes one CSV per fiscal year. Try most-recent-first.
USCIS_BASE = "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-{year}.csv"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def download_latest(years=(2024, 2023, 2022)):
    """Download the most recent available USCIS H1B CSV via curl (urllib is UA-blocked)."""
    for year in years:
        url = USCIS_BASE.format(year=year)
        try:
            result = subprocess.run(
                ["curl", "-sL", "-A", UA, "--max-time", "120", url],
                capture_output=True, text=True, timeout=150,
            )
            data = result.stdout
            if data.startswith('"Fiscal Year"') or "Employer" in data[:200]:
                print(f"Downloaded FY{year} ({len(data)//1024}KB)")
                return year, data
            else:
                print(f"  FY{year}: unexpected content ({len(data)} bytes)")
        except Exception as e:
            print(f"  FY{year} failed: {e}")
    return None, None


def aggregate(csv_text):
    """Aggregate approvals by employer name."""
    reader = csv.DictReader(io.StringIO(csv_text))
    employers = {}
    for row in reader:
        name = (row.get("Employer") or "").strip()
        if not name:
            continue
        try:
            initial = int(row.get("Initial Approval", 0) or 0)
            cont = int(row.get("Continuing Approval", 0) or 0)
        except ValueError:
            initial, cont = 0, 0
        total = initial + cont
        if name not in employers:
            employers[name] = {"initial": 0, "continuing": 0, "total": 0,
                               "state": row.get("State", ""), "city": row.get("City", "")}
        employers[name]["initial"] += initial
        employers[name]["continuing"] += cont
        employers[name]["total"] += total
    return employers


def build_verified_lookup(employers, top_n=500):
    """Build a lookup keyed by normalized employer name → verified H1B data."""
    ranked = sorted(employers.items(), key=lambda kv: -kv[1]["total"])
    lookup = {}
    for name, data in ranked[:top_n]:
        norm = name.upper().strip()
        lookup[norm] = {
            "employer": name,
            "h1b_approvals": data["total"],
            "initial": data["initial"],
            "continuing": data["continuing"],
            "state": data["state"],
            "verified": True,
        }
    return lookup


def main():
    year, csv_text = download_latest()
    if not csv_text:
        print("Could not download USCIS data — aborting")
        return

    employers = aggregate(csv_text)
    print(f"Aggregated {len(employers)} unique employers")

    lookup = build_verified_lookup(employers, top_n=1000)

    output = {
        "source": "USCIS H-1B Employer Data Hub",
        "source_url": "https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub",
        "fiscal_year": year,
        "note": "Counts are actual USCIS H-1B petition approvals (initial + continuing).",
        "sponsors": lookup,
    }
    OUTPUT.write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(lookup)} verified sponsors to {OUTPUT.name}")

    # Show top 20 SCM/ops-relevant sponsors for a sanity check
    print("\nTop 20 sponsors overall (FY{}):".format(year))
    for i, (norm, d) in enumerate(list(lookup.items())[:20], 1):
        print(f"  {i:>2}. {d['employer'][:45]:<45} {d['h1b_approvals']:>6} approvals")


if __name__ == "__main__":
    main()
