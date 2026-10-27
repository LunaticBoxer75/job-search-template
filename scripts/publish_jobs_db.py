"""Publish data/jobs_deploy.db to the history-free `jobs-data` branch.

Why: the deploy DB used to be committed to `main` on every /find-jobs run —
13 copies, ~258MB of history that Streamlit Cloud downloads on every cold
start. Now it lives on its own branch holding exactly ONE commit, replaced each
run with a force-push, so nothing accumulates. The dashboard downloads it at
startup (src/jobs_data.py).

The force-push only ever targets refs/heads/jobs-data. It never touches main.

Usage: python scripts/publish_jobs_db.py [--dry-run]
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB = ROOT / "data" / "jobs_deploy.db"
REMOTE = "github"
BRANCH = "jobs-data"
MAX_MB = 95  # GitHub hard-rejects files over 100MB


def git(*args, stdin=None):
    return subprocess.run(
        ["git", *args], cwd=ROOT, input=stdin, capture_output=True, text=True, check=True
    ).stdout.strip()


def main():
    dry = "--dry-run" in sys.argv
    if not DB.exists():
        sys.exit(f"{DB} not found — run scripts/export_deploy_db.py first.")
    size_mb = DB.stat().st_size / 1024 / 1024
    if size_mb > MAX_MB:
        sys.exit(f"{DB.name} is {size_mb:.0f}MB; GitHub rejects files over 100MB.")

    # Build a parentless commit containing only data/jobs_deploy.db, without
    # touching the working tree or index.
    blob = git("hash-object", "-w", str(DB))
    sub = git("mktree", stdin=f"100644 blob {blob}\tjobs_deploy.db\n")
    root = git("mktree", stdin=f"040000 tree {sub}\tdata\n")
    commit = git("commit-tree", root, "-m", f"jobs_deploy.db ({size_mb:.1f}MB)")

    if dry:
        print(f"[dry-run] built {commit[:8]} ({size_mb:.1f}MB); not pushed.")
        return
    git("push", "--force", REMOTE, f"{commit}:refs/heads/{BRANCH}")
    print(f"Published {DB.name} ({size_mb:.1f}MB) to {REMOTE}/{BRANCH} @ {commit[:8]}")


if __name__ == "__main__":
    main()
