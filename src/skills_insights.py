"""Skills & certification insights — analyze what's demanded across all scored job descriptions.

Categorizes into technical skills, management/soft skills, and certifications,
returning frequency counts. Used by the dashboard's Skills Insights and Skills Gap pages.

Computed once per /find-jobs run from the FULL local DB (scripts/export_deploy_db.py
stores the result in the deploy DB's `insights` table). Computing it on the dashboard
was wrong twice over: the deploy DB truncates descriptions to 800 chars, and plain
substring matching counted "excel" inside "excellent", "erp" inside "enterprise",
"tms" inside "items" — Excel showed 73% (full text) or 11% (on the site); the real
figure is ~41%.
"""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import profile

# Prefer the deploy DB (bundled on Streamlit Cloud), fall back to full local DB
_DATA_DIR = Path(__file__).parent.parent / "data"


def get_db():
    deploy = _DATA_DIR / "jobs_deploy.db"
    full = _DATA_DIR / "jobs.db"
    return sqlite3.connect(deploy if deploy.exists() else full)

TECHNICAL_SKILLS = {
    "Excel / Spreadsheets": ["excel", "spreadsheet", "pivot table", "vlookup"],
    "ERP systems (SAP/Oracle)": ["erp", "oracle", "netsuite", "sap"],
    "Data analysis / analytics": ["data analysis", "data analytics", "analytics"],
    "Data visualization": ["data visualization", "dashboard", "visualization"],
    "SQL": ["sql", "mysql", "postgresql"],
    "Forecasting / Demand planning": ["forecasting", "demand planning"],
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "Statistical analysis": ["statistical analysis", "statistics", "regression"],
    "Python": ["python", "pandas"],
    "Salesforce": ["salesforce"],
    "WMS / TMS": ["wms", "tms", "warehouse management system", "transportation management"],
    "VBA / Macros": ["vba", "macros"],
    "Machine Learning": ["machine learning", "predictive model"],
    "MRP": ["mrp ", "material requirements planning"],
}

SOFT_SKILLS = {
    "Communication": ["communication skills", "verbal communication", "written communication", "communicate"],
    "Stakeholder management": ["stakeholder"],
    "Leadership": ["leadership", "lead a team", "team lead"],
    "Analytical / critical thinking": ["analytical", "critical thinking"],
    "Process improvement": ["process improvement", "continuous improvement", "process optimization"],
    "Strategic thinking / planning": ["strategic thinking", "strategic planning", "strategy"],
    "Collaboration / teamwork": ["teamwork", "team player", "collaborative", "collaboration"],
    "Time management / prioritization": ["time management", "prioritization", "prioritize"],
    "Project management": ["project management", "project manage"],
    "Attention to detail": ["attention to detail", "detail-oriented", "detail oriented"],
    "Risk management": ["risk management", "risk assessment", "risk mitigation"],
    "Negotiation": ["negotiation", "negotiate"],
    "Problem solving": ["problem solving", "problem-solving"],
    "Vendor / supplier management": ["vendor management", "supplier management", "supplier relationship"],
    "Change management": ["change management"],
}

CERTIFICATIONS = {
    "Lean Six Sigma": ["six sigma", "lean six sigma", "green belt", "black belt", "yellow belt"],
    "PMP": ["pmp", "project management professional"],
    "APICS / CSCP": ["apics", "cscp"],
    "CPIM": ["cpim"],
    "CLTD": ["cltd"],
    "CPSM (Supply Mgmt)": ["cpsm"],
    "CSCMP / SCPro": ["cscmp", "scpro"],
    "Lean / Kaizen": ["lean manufacturing", "kaizen"],
    "ISO 9001": ["iso 9001", "iso certification"],
    "Scrum / Agile (CSM)": ["scrum master", "pmi-acp", "safe agile"],
    "CPA": ["cpa"],
    "CFA": ["cfa"],
}

# Skills and certifications the candidate already has (to flag gaps) — config/profile.json
HAS_SKILLS = set(profile.load()["skills_have"]["insights"])
HAS_CERTS = set(profile.load()["skills_have"]["certs"])


def _pattern(variants):
    """Whole words, allowing a plural ending ("dashboards", "negotiations") — never inside
    another word ("excel" must not match "excellent")."""
    return re.compile("|".join(rf"(?<![\w/]){re.escape(v.strip())}(?:e?s)?(?![\w])" for v in variants))


def analyze_skills(min_score=70, db_path=None):
    """Skill frequency across descriptions of jobs scoring `min_score`+.

    Pass `db_path` = the full data/jobs.db; the deploy copy's descriptions are
    truncated. Rows without a real description (all LinkedIn rows) are excluded.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) if db_path else get_db()
    rows = conn.execute("""
        SELECT description FROM jobs
        WHERE score >= ? AND LENGTH(description) > 200
    """, (min_score,)).fetchall()
    conn.close()

    # Undo markdown escapes first: descriptions are stored as markdown, so every
    # hyphenated term is "cross\\-functional" and would never match.
    descs = [re.sub(r"\\([^\w\s])", r"\1", r[0].lower()) for r in rows]
    n = len(descs)
    if n == 0:
        return {"total": 0, "technical": [], "soft": [], "certs": []}

    def count_category(category):
        results = []
        for name, variants in category.items():
            rx = _pattern(variants)
            cnt = sum(1 for d in descs if rx.search(d))
            results.append({
                "skill": name,
                "count": cnt,
                "pct": round(cnt / n * 100, 1),
            })
        results.sort(key=lambda x: -x["count"])
        return results

    tech = count_category(TECHNICAL_SKILLS)
    soft = count_category(SOFT_SKILLS)
    certs = count_category(CERTIFICATIONS)

    # Tag has/gap
    for r in tech + soft:
        r["has"] = r["skill"] in HAS_SKILLS
    for r in certs:
        r["has"] = r["skill"] in HAS_CERTS

    return {"total": n, "technical": tech, "soft": soft, "certs": certs,
            "min_score": min_score, "computed_at": datetime.now().isoformat(timespec="minutes")}


def store(conn, insights):
    """Save into a DB's `insights` table (the deploy DB, at export time)."""
    conn.execute("CREATE TABLE IF NOT EXISTS insights (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO insights VALUES ('skills', ?)", (json.dumps(insights),))


def load_precomputed(db_path):
    """The stored analysis, or None if this DB predates it."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = conn.execute("SELECT value FROM insights WHERE key='skills'").fetchone()
        conn.close()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, ValueError):
        return None
