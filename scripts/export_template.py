"""Build a shareable copy of this repo that holds nobody's personal data.

    python scripts/export_template.py <empty target dir>      # copy, then check
    python scripts/export_template.py --check <dir>           # check only

Copies the tracked files except the personal ones, puts config/profile.example.json
in place of config/profile.json, drops the per-candidate notes in content/
("candidate_angle"), and exits 1 listing every line that still identifies the candidate.
The copy has no git history: push it as a new repository, never as a branch of this one
(anyone who can read a repo can read all of its branches and history).
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Personal to this candidate, or stale; never copied.
EXCLUDE_PREFIXES = ("career-plan/", "research/", "docs/", "data/")
EXCLUDE_FILES = {"CLAUDE.md", "config/profile.json", ".claude/scheduled_tasks.json", "run.sh", ".env.example"}

IDENTIFYING = re.compile(
    r"bhumika|choudhary|bhummika|bhumichoudhary|9666597222|hyderabad|st\.? francis college|foster school"
    r"|uw foster|university of washington|\buber\b|lunaticboxer75|aniketsw|mscm-bhumika|microbiology"
    r"|global sme|\buw\b|schoolfilter=%5b%222584", re.I)
# Reference data that lists employers by name (Uber, University of Washington) — not about her.
# The eval sample's job rows name real employers too.
CHECK_SKIP = {"config/company_sizes.json", "config/h1b_sponsors_verified.json", "src/sponsor_data.py",
              "scripts/export_template.py", "scripts/eval_quick_scorer.py"}


def _strip_angles(obj):
    if isinstance(obj, dict):
        return {k: _strip_angles(v) for k, v in obj.items() if k != "candidate_angle"}
    if isinstance(obj, list):
        return [_strip_angles(v) for v in obj]
    return obj


def export(dst):
    if dst.exists() and any(dst.iterdir()):
        sys.exit(f"{dst} is not empty")
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    n = 0
    for f in files:
        if f.startswith(EXCLUDE_PREFIXES) or f in EXCLUDE_FILES or not (ROOT / f).is_file():
            continue
        (dst / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst / f)
        n += 1
    shutil.copy2(ROOT / "config" / "profile.example.json", dst / "config" / "profile.json")
    for p in (dst / "content").glob("*.json"):
        p.write_text(json.dumps(_strip_angles(json.loads(p.read_text())), indent=2, ensure_ascii=False) + "\n")
    print(f"copied {n} files to {dst}")


def check(dst):
    hits = []
    for p in sorted(dst.rglob("*")):
        rel = p.relative_to(dst).as_posix()
        if not p.is_file() or rel.startswith(".git/") or rel in CHECK_SKIP:
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            hits.append(f"{rel}: binary file — review by hand")
            continue
        hits += [f"{rel}:{i}: {line.strip()[:140]}" for i, line in enumerate(text.splitlines(), 1)
                 if IDENTIFYING.search(line)]
    print("\n".join(hits) if hits else "clean: nothing identifying found")
    return not hits


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--check"] and len(args) == 2:
        sys.exit(0 if check(Path(args[1])) else 1)
    if len(args) != 1:
        sys.exit(__doc__)
    target = Path(args[0])
    target.mkdir(parents=True, exist_ok=True)
    export(target)
    sys.exit(0 if check(target) else 1)
