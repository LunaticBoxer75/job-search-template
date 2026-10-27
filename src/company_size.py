"""Company size tiers from config/company_sizes.json.

JobSpy only reports employee counts for Indeed (~35% of rows), so sizes come from
a curated table of the ~500 most frequent employers — the long tail beyond that
is almost never a large company. Tiers, not exact counts: a rough number is fine,
a wrong tier isn't.

Matching is exact on normalized aliases — no prefix or substring guessing
("Target Logistics" must not become Target).
"""

import json
import re
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).parent.parent / "config" / "company_sizes.json"

TIER_RANK = {"100K+": 4, "10K-100K": 3, "1K-10K": 2, "<1K": 1, "unknown": 0}

_SUFFIXES = re.compile(
    r"\b(inc|llc|l l c|corp|corporation|co|ltd|limited|plc|services|company|group|holdings|usa|us|na)\b"
)


def normalize(name):
    """Same normalization used to build the table's aliases."""
    s = re.sub(r"\(.*?\)", " ", str(name or "").lower())
    s = re.sub(r"[.,]", " ", s)
    s = _SUFFIXES.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _plain(name):
    """Lowercase, punctuation removed, suffixes kept — the other alias form."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s&]", " ", str(name or "").lower())).strip()


@lru_cache(maxsize=1)
def _index():
    """(exact-name index, suffix-stripped index). Exact is checked first so a specific
    name wins over a generic one: "Compass Group USA" must not become "Compass", the
    real-estate firm, just because stripping "Group"/"USA" leaves "compass"."""
    try:
        data = json.loads(PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}, {}
    exact, stripped = {}, {}
    for entry in data.get("companies", {}).values():
        for alias in entry.get("aliases", []) + [entry.get("name", "")]:
            if _plain(alias):
                exact.setdefault(_plain(alias), entry)
            if normalize(alias):
                stripped.setdefault(normalize(alias), entry)
    return exact, stripped


@lru_cache(maxsize=20000)
def lookup(company):
    """{'tier', 'employees', 'staffing', 'name'} for a company, or an 'unknown' record."""
    exact, stripped = _index()
    entry = exact.get(_plain(company)) or stripped.get(normalize(company))
    if not entry:
        return {"tier": "unknown", "employees": None, "staffing": False, "name": None}
    return {"tier": entry.get("tier", "unknown"), "employees": entry.get("employees"),
            "staffing": bool(entry.get("staffing")), "name": entry.get("parent") or entry.get("name")}
