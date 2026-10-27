"""Resume rephrase engine — given a job description, suggest how to rephrase
each of the candidate's resume bullets to match the JD's keywords/verbs.

Two modes:
1. Show side-by-side: original bullet → suggested rephrase
2. Generate full DOCX with all rephrases applied
"""

import json
import re
from pathlib import Path

CONFIG_DIR = Path(__file__).parent.parent / "config"


def get_base_bullets():
    """Get the candidate's base resume bullets from profile."""
    with open(CONFIG_DIR / "profile.json") as f:
        profile = json.load(f)
    return profile.get("key_achievements", [])


def extract_jd_keywords(description, top_n=20):
    """Extract likely ATS keywords from a job description.

    Rule-based: pull capitalized noun phrases, action verbs, and skill terms.
    """
    if not description:
        return []

    # Common SCM/ops/PM keywords to look for
    skill_terms = [
        # Tools
        "SAP", "S/4HANA", "Oracle", "SQL", "Python", "Tableau", "Power BI",
        "Excel", "VBA", "JIRA", "Confluence", "Smartsheet",
        # Methodologies
        "Six Sigma", "Lean", "Kaizen", "DMAIC", "Agile", "Scrum", "PMP",
        "APICS", "CSCP", "CPIM", "S&OP", "MRP", "ERP",
        # Concepts
        "supply chain", "procurement", "sourcing", "logistics", "inventory",
        "demand planning", "forecasting", "vendor management", "RFP", "RFQ",
        "category management", "TCO", "OTIF", "fill rate", "lead time",
        "stakeholder management", "cross-functional", "process improvement",
        "risk analysis", "compliance", "audit", "SOP", "KPI",
        "data analysis", "data visualization", "dashboard", "reporting",
        "negotiation", "cost reduction", "quality assurance",
    ]

    desc_lower = description.lower()
    found = []
    for term in skill_terms:
        if term.lower() in desc_lower:
            found.append(term)

    return found[:top_n]


def get_resume_keywords():
    """Keywords currently in the candidate's resume (from profile + achievements)."""
    with open(CONFIG_DIR / "profile.json") as f:
        profile = json.load(f)

    text = " ".join([
        " ".join(profile.get("experience_summary", {}).get("domain_expertise", [])),
        " ".join(profile.get("experience_summary", {}).get("tools", [])),
        " ".join(profile.get("experience_summary", {}).get("certifications", [])),
        " ".join(profile.get("key_achievements", [])),
    ]).lower()

    return text


def keyword_match_analysis(description):
    """Return (matched_keywords, missing_keywords) for a JD vs resume."""
    jd_kws = extract_jd_keywords(description, top_n=30)
    resume_text = get_resume_keywords()

    matched = []
    missing = []
    for kw in jd_kws:
        if kw.lower() in resume_text:
            matched.append(kw)
        else:
            missing.append(kw)

    return matched, missing


def suggest_rephrase(bullet, jd_keywords):
    """Suggest a rephrase of a bullet to incorporate JD keywords.

    Rule-based mapping: substitute terminology where applicable.
    For more nuanced rephrasing, this would call Claude — but rules cover 80%.
    """
    rephrase = bullet
    suggestions = []

    # Common SCM/Ops translation patterns
    replacements = {
        "fraud detection": ["risk analysis", "supply chain risk", "compliance"],
        "appeals": ["case management", "issue resolution"],
        "JIRA": ["JIRA / project tracking", "ticket management"],
        "SME": ["subject matter expert", "process owner"],
        "manual dependency": ["manual processes", "non-automated workflows"],
        "automation": ["process automation", "workflow optimization"],
        "intervention time": ["resolution time", "cycle time"],
        "escalation": ["issue escalation", "exception handling"],
        "quality frameworks": ["quality management", "Six Sigma frameworks", "process quality"],
    }

    bullet_lower = bullet.lower()
    for original, options in replacements.items():
        if original in bullet_lower:
            # Pick option matching JD keywords if possible
            matched_option = None
            for opt in options:
                if any(kw.lower() in opt.lower() or opt.lower() in kw.lower() for kw in jd_keywords):
                    matched_option = opt
                    break
            if matched_option:
                # Case-preserving replace
                rephrase = re.sub(re.escape(original), matched_option, rephrase, flags=re.IGNORECASE)
                suggestions.append(f"'{original}' → '{matched_option}'")

    # Suggest adding missing JD keywords contextually
    bullet_words = set(bullet.lower().split())
    for kw in jd_keywords[:5]:
        if kw.lower() not in bullet_words and len(kw) > 3:
            # Don't actually add, just suggest
            pass

    return rephrase, suggestions


def rephrase_all_bullets(description):
    """Return list of (original, rephrased, change_notes) for all bullets."""
    bullets = get_base_bullets()
    jd_keywords = extract_jd_keywords(description)

    results = []
    for bullet in bullets:
        rephrased, notes = suggest_rephrase(bullet, jd_keywords)
        changed = rephrased != bullet
        results.append({
            "original": bullet,
            "rephrased": rephrased,
            "changes": notes,
            "changed": changed,
        })
    return results


def generate_tailored_docx(description, output_path=None):
    """Generate a DOCX with rephrased bullets. Requires python-docx."""
    try:
        from docx import Document
    except ImportError:
        return None

    rephrases = rephrase_all_bullets(description)
    matched, missing = keyword_match_analysis(description)

    doc = Document()
    doc.add_heading("Tailored Resume Bullets", 0)
    doc.add_paragraph(f"Customized for this job description")
    doc.add_paragraph()

    doc.add_heading("Rephrased Achievements", level=1)
    for r in rephrases:
        para = doc.add_paragraph()
        para.add_run("• " + r["rephrased"])

    doc.add_heading("ATS Keywords", level=1)
    doc.add_paragraph(f"Matched in your resume: {', '.join(matched) if matched else 'None'}")
    doc.add_paragraph(f"Missing (consider adding): {', '.join(missing) if missing else 'None'}")

    if output_path:
        doc.save(output_path)
        return output_path

    import io
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf
