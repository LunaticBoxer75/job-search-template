"""STEM-OPT E-Verify lookup + Top SCM H1B sponsors.

For free public data sources:
- DOL Foreign Labor Disclosure: https://www.dol.gov/agencies/eta/foreign-labor/performance
- USCIS E-Verify employer DB: https://www.e-verify.gov/about-e-verify/e-verify-data
- myvisajobs.com sponsor rankings (public, scrape-friendly aggregation)

For now, we ship a curated lookup of known top SCM/Operations sponsors. The
data structure supports adding the full DOL CSV later via a script.
"""

# Known E-Verify enrolled companies that sponsor F1/H1B in SCM/Ops/PM roles
# Hand-curated from publicly reported H1B + E-Verify data
KNOWN_SPONSORS = {
    # Tier 1: Heavy sponsors with strong SCM presence
    "Amazon": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Amazon.com": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Amazon Web Services": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Microsoft": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Google": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Meta": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Apple": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Tesla": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    # Not in USCIS's FY2023 top 1,000; most roles require U.S.-person status (ITAR) —
    # 76 of 82 readable Boeing postings in the DB say so.
    "Boeing": {"e_verify": True, "h1b_volume": "low", "scm_focus": True, "us_only": True},
    "Intel": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "NVIDIA": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Qualcomm": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},

    # Tier 2: Strong sponsors, SCM-relevant
    "Walmart": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Target": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Costco": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Starbucks": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Nike": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Procter & Gamble": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Johnson & Johnson": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Pfizer": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "AbbVie": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Merck": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "General Electric": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Honeywell": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "3M": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Caterpillar": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "John Deere": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Lockheed Martin": {"e_verify": True, "h1b_volume": "high", "scm_focus": True, "us_only": True},
    "Raytheon": {"e_verify": True, "h1b_volume": "high", "scm_focus": True, "us_only": True},
    "Northrop Grumman": {"e_verify": True, "h1b_volume": "high", "scm_focus": True, "us_only": True},
    "FedEx": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "UPS": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Cummins": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Ford": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "General Motors": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Stellantis": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},

    # Tech with SCM/operations needs
    "Salesforce": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": False},
    "Oracle": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "SAP": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Cisco": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Dell": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "HPE": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "IBM": {"e_verify": True, "h1b_volume": "very_high", "scm_focus": True},
    "Adobe": {"e_verify": True, "h1b_volume": "high", "scm_focus": False},
    "Snowflake": {"e_verify": True, "h1b_volume": "high", "scm_focus": False},
    "Databricks": {"e_verify": True, "h1b_volume": "high", "scm_focus": False},
    "Stripe": {"e_verify": True, "h1b_volume": "high", "scm_focus": False},
    "Uber": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Lyft": {"e_verify": True, "h1b_volume": "medium", "scm_focus": False},
    "Airbnb": {"e_verify": True, "h1b_volume": "medium", "scm_focus": False},
    "DoorDash": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Instacart": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Datadog": {"e_verify": True, "h1b_volume": "medium", "scm_focus": False},
    "Anthropic": {"e_verify": True, "h1b_volume": "medium", "scm_focus": False},
    "OpenAI": {"e_verify": True, "h1b_volume": "medium", "scm_focus": False},

    # Consumer / Retail
    "PepsiCo": {"e_verify": True, "h1b_volume": "high", "scm_focus": True},
    "Coca-Cola": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Anheuser-Busch": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Estee Lauder": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},

    # SCM-specific
    "Sherwin-Williams": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Saint-Gobain": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "BASF": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Dow": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "DuPont": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "Cargill": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},
    "ADM": {"e_verify": True, "h1b_volume": "medium", "scm_focus": True},

    # Known NON-sponsors (red flag — F1 should skip)
    "Seattle Public Schools": {"e_verify": False, "h1b_volume": "none", "scm_focus": False, "non_sponsor": True},
    "United States Postal Service": {"e_verify": True, "h1b_volume": "none", "scm_focus": False, "us_only": True, "non_sponsor": True},
    "Department of Defense": {"e_verify": True, "h1b_volume": "none", "scm_focus": False, "us_only": True, "non_sponsor": True},
}


def lookup_sponsor(company_name):
    """Return sponsor info for a company. Fuzzy-match by normalized name."""
    if not company_name:
        return None

    normalized = company_name.strip()
    # Direct match
    if normalized in KNOWN_SPONSORS:
        return KNOWN_SPONSORS[normalized]

    # Fuzzy: lowercase, partial match
    norm_lower = normalized.lower()
    for known, data in KNOWN_SPONSORS.items():
        if known.lower() in norm_lower or norm_lower in known.lower():
            return data

    return None


import json as _json
from pathlib import Path as _Path

_VERIFIED_CACHE = None


def _load_verified():
    """Load USCIS-verified H1B sponsor data if available."""
    global _VERIFIED_CACHE
    if _VERIFIED_CACHE is not None:
        return _VERIFIED_CACHE
    path = _Path(__file__).parent.parent / "config" / "h1b_sponsors_verified.json"
    if path.exists():
        try:
            _VERIFIED_CACHE = _json.loads(path.read_text())
        except Exception:
            _VERIFIED_CACHE = {"sponsors": {}}
    else:
        _VERIFIED_CACHE = {"sponsors": {}}
    return _VERIFIED_CACHE


# Names USCIS files under a different spelling
_USCIS_ALIASES = {"WALMART": "WAL MART", "GE": "GENERAL ELECTRIC", "P&G": "PROCTER & GAMBLE",
                  "J&J": "JOHNSON & JOHNSON", "AWS": "AMAZON WEB SERVICES"}


def lookup_verified_sponsor(company_name):
    """Look up actual USCIS H1B approval count for a company (whole-word match)."""
    if not company_name:
        return None
    data = _load_verified()
    sponsors = data.get("sponsors", {})
    norm = company_name.upper().strip()
    for a, b in _USCIS_ALIASES.items():
        if norm == a or norm.startswith(a + " "):
            norm = b + norm[len(a):]

    # Direct match
    if norm in sponsors:
        return {**sponsors[norm], "fiscal_year": data.get("fiscal_year")}

    # Fuzzy: match on significant token overlap
    company_tokens = set(w for w in norm.replace(",", " ").replace(".", " ").split() if len(w) > 2)
    if not company_tokens:
        return None
    best = None
    for key, val in sponsors.items():
        key_tokens = set(w for w in key.replace(",", " ").replace(".", " ").split() if len(w) > 2)
        # The company's first significant word must be a whole WORD of the key
        # ("META" must not match "METALS")
        first_word = norm.split()[0] if norm.split() else ""
        if first_word and len(first_word) > 2 and first_word in key_tokens:
            if best is None or val["h1b_approvals"] > best["h1b_approvals"]:
                best = {**val, "fiscal_year": data.get("fiscal_year")}
    return best


def get_sponsor_badge(company_name):
    """Return (emoji, label, color) badge for the sponsor status."""
    # Prefer verified USCIS data
    verified = lookup_verified_sponsor(company_name)
    if verified and verified.get("h1b_approvals", 0) > 0:
        n = verified["h1b_approvals"]
        fy = verified.get("fiscal_year", "")
        if n >= 500:
            return ("🟢", f"Top H1B sponsor — {n:,} approvals FY{fy} (USCIS verified)", "green")
        elif n >= 100:
            return ("🟢", f"Strong H1B sponsor — {n:,} approvals FY{fy} (USCIS verified)", "green")
        elif n >= 20:
            return ("🟡", f"H1B sponsor — {n} approvals FY{fy} (USCIS verified)", "yellow")
        else:
            return ("🟡", f"Some H1B history — {n} approvals FY{fy} (USCIS verified)", "yellow")

    info = lookup_sponsor(company_name)

    if not info:
        return ("⚪", "Sponsorship: Unknown", "gray")

    if info.get("non_sponsor"):
        return ("🚫", "Does NOT sponsor F1/H1B", "red")

    if info.get("us_only"):
        return ("🇺🇸", "US citizens only (clearance required)", "red")

    volume = info.get("h1b_volume", "unknown")
    e_verify = info.get("e_verify", False)

    if volume == "very_high" and e_verify:
        return ("🟢", "Top H1B Sponsor + E-Verify (STEM OPT eligible)", "green")
    elif volume == "high" and e_verify:
        return ("🟢", "Strong H1B Sponsor + E-Verify (STEM OPT eligible)", "green")
    elif volume == "medium" and e_verify:
        return ("🟡", "Medium Sponsor + E-Verify (STEM OPT eligible)", "yellow")
    elif e_verify:
        return ("🟡", "E-Verify enrolled (STEM OPT eligible)", "yellow")
    else:
        return ("⚪", "Unknown sponsorship history", "gray")


def get_top_sponsors_for_scm():
    """Curated SCM employers, ranked by ACTUAL USCIS H-1B approvals (not hand labels).

    Each entry: company, approvals (None if not in USCIS's top 1,000), fiscal_year,
    us_only (roles mostly need U.S.-person status / clearance).
    """
    seen, out = set(), []
    for company, info in KNOWN_SPONSORS.items():
        if info.get("non_sponsor") or not info.get("scm_focus"):
            continue
        canon = (company.replace(".com", "").replace(" Web Services", "").replace(" Inc", "")
                 .replace("General Electric", "GE").strip())
        if canon in seen:
            continue
        seen.add(canon)
        v = lookup_verified_sponsor(company)
        out.append({"company": company, "approvals": (v or {}).get("h1b_approvals"),
                    "fiscal_year": _load_verified().get("fiscal_year"), "us_only": bool(info.get("us_only"))})
    # Sponsors she can actually use first, by approvals; U.S.-person-only employers last
    return sorted(out, key=lambda x: (x["us_only"], -(x["approvals"] or 0)))
