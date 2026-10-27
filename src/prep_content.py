"""Loader for the static interview-prep content in `content/`.

Content lives as JSON rather than Python so it's diffable, reviewable, and
editable without touching code. It ships in git, so it's present on every
deploy — nothing here is user data (see src/user_store.py for that).

Every loader degrades to empty on a missing or malformed file: a content bug
should blank one panel, never break the dashboard.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

CONTENT = Path(__file__).parent.parent / "content"

CASE_FILES = ["cases_scm.json", "cases_ops.json", "cases_pm.json", "cases_risk.json"]

DOMAINS = [
    "Supply Chain",
    "Operations",
    "Program & Product Management",
    "Risk & Compliance",
]


def _read(filename, default):
    path = CONTENT / filename
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


@lru_cache(maxsize=1)
def cases():
    """All case studies across domains, as a flat list."""
    out = []
    for fn in CASE_FILES:
        rows = _read(fn, [])
        if isinstance(rows, list):
            out.extend(r for r in rows if isinstance(r, dict) and r.get("prompt"))
    return out


@lru_cache(maxsize=1)
def frameworks():
    return _read("frameworks.json", [])


@lru_cache(maxsize=1)
def metrics():
    return _read("metrics.json", [])


@lru_cache(maxsize=1)
def companies():
    return _read("companies.json", [])


@lru_cache(maxsize=1)
def loop_map():
    rows = _read("loop_map.json", [])
    return sorted(rows, key=lambda r: r.get("order", 99)) if isinstance(rows, list) else []


@lru_cache(maxsize=1)
def resources():
    return _read("resources.json", [])


@lru_cache(maxsize=1)
def playbook():
    return _read("prep_playbook.json", {})


def by_domain(rows, domain):
    """Filter content rows by domain. `domain` of None or 'All' returns all."""
    if not domain or domain == "All":
        return rows
    out = []
    for r in rows:
        d = r.get("domains") or ([r["domain"]] if r.get("domain") else [])
        if domain in d or "All" in d:
            out.append(r)
    return out


def find_company(name):
    """Match a scraped company name to a curated entry, on whole words only.

    Plain substring matching sent "J.M. Huber Corporation" to the wrong company's entry. Same
    class of bug as "intern" matching "Internal" — word boundaries, always.
    """
    if not name:
        return None
    norm = name.strip().lower()
    entries = companies()
    for c in entries:
        if c.get("name", "").lower() == norm:
            return c
    for c in entries:
        # Match the curated name, or its first word ("Costco" for "Costco Wholesale")
        cn = c.get("name", "").lower().strip()
        for token in {cn, cn.split()[0] if cn else ""}:
            if token and re.search(rf"(?<![\w.]){re.escape(token)}(?![\w])", norm):
                return c
    return None


def status():
    """Per-file load diagnostics, for the dashboard's content health caption."""
    out = {}
    for fn in CASE_FILES + [
        "frameworks.json",
        "metrics.json",
        "companies.json",
        "loop_map.json",
        "resources.json",
        "prep_playbook.json",
    ]:
        path = CONTENT / fn
        if not path.exists():
            out[fn] = "missing"
            continue
        try:
            data = json.loads(path.read_text())
            out[fn] = len(data) if isinstance(data, (list, dict)) else "?"
        except json.JSONDecodeError as e:
            out[fn] = f"invalid JSON: {e}"
    return out
