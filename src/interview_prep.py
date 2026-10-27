"""Interview prep — recommendations from mentors, STAR stories, mock question bank.

Her data (recs, stories, mock sessions) is backed by src/user_store.py — durable
JSON, not SQLite. The question bank and themes below are static content.
"""

import random
from datetime import datetime

from . import profile, user_store


# ============ RECOMMENDATIONS ============
def _rec(recommendation, category, recommended_by, person_role=""):
    return {"recommendation": recommendation, "category": category,
            "recommended_by": recommended_by, "person_role": person_role}


def add_recommendation(recommendation, category, recommended_by, person_role=""):
    return user_store.add_unique("interview_recs",
                                 _rec(recommendation, category, recommended_by, person_role),
                                 unique_on="recommendation", message="Add recommendation")


def get_recommendations():
    """Grouped by category; newest first within a category."""
    rows = sorted(user_store.get("interview_recs"),
                  key=lambda r: r.get("created_at") or "", reverse=True)
    return sorted(rows, key=lambda r: r.get("category") or "")  # stable: keeps newest-first


def seed_recommendations():
    """Seed mentor recommendations listed in config/profile.json (seed.interview_recs)."""
    recs = profile.load().get("seed", {}).get("interview_recs", [])
    existing = {r.get("recommendation") for r in user_store.get("interview_recs")}
    new = [_rec(*r) for r in recs if r[0] not in existing]
    if new:  # runs on every render: zero commits once seeded
        user_store.add_many("interview_recs", new, "Seed mentor recommendations",
                            unique_on="recommendation")


# ============ STAR STORIES ============
STORY_THEMES = [
    "Leadership", "Conflict Management", "Prioritization", "Dealing with Ambiguity",
    "Failure / Learning", "Data-Driven Decision", "Cross-Functional Collaboration",
    "Process Improvement", "Customer Obsession", "Ownership", "Stakeholder Management",
    "Handling Pressure / Tight Deadline", "Influencing Without Authority", "Innovation",
]


def add_story(title, theme, situation, task, action, result, tags=""):
    return user_store.add("star_stories", {
        "title": title, "theme": theme, "situation": situation,
        "task": task, "action": action, "result": result, "tags": tags,
    }, message=f"Add STAR story: {title}")


def get_stories(theme=None):
    rows = user_store.get("star_stories")
    if theme:
        rows = [r for r in rows if r.get("theme") == theme]
    # Grouped by theme; most recently edited first within a theme
    rows = sorted(rows, key=lambda r: r.get("updated_at") or r.get("created_at") or "",
                  reverse=True)
    return sorted(rows, key=lambda r: r.get("theme") or "")


def update_story(story_id, fields):
    return user_store.update("star_stories", story_id, fields)


def delete_story(story_id):
    return user_store.delete("star_stories", story_id)


def story_coverage():
    """Which behavioral themes still have no story. Drives the coverage gauge.

    A story bank with gaps is the single most common cause of a blown behavioral
    round, so this is surfaced prominently rather than buried.
    """
    have = {s.get("theme") for s in get_stories()}
    return {
        "covered": [t for t in STORY_THEMES if t in have],
        "missing": [t for t in STORY_THEMES if t not in have],
    }


# ============ MOCK INTERVIEW QUESTION BANK ============
# Behavioral + role-specific questions for SCM/Ops/PM/Risk roles
MOCK_QUESTION_BANK = {
    "Behavioral (Leadership Principles style)": [
        "Tell me about a time you had to make a decision with incomplete data.",
        "Describe a situation where you disagreed with a stakeholder. How did you handle it?",
        "Tell me about a time you failed. What did you learn?",
        "Give an example of when you had to prioritize competing deadlines.",
        "Describe a time you influenced a decision without having formal authority.",
        "Tell me about a time you improved a process. What was the impact?",
        "Describe the most complex problem you've solved. Walk me through your approach.",
        "Tell me about a time you had to deliver bad news to a stakeholder.",
        "Give an example of when you went above and beyond your role.",
        "Tell me about a time you had to manage a conflict within a team.",
        "Describe a time you had to learn something quickly to complete a task.",
        "Tell me about a time you used data to change someone's mind.",
    ],
    "Supply Chain / Operations": [
        "How would you reduce lead time in a supply chain that's consistently late?",
        "Walk me through how you'd analyze why on-time delivery dropped 10% last quarter.",
        "How do you balance inventory costs against stockout risk?",
        "A key supplier just failed an audit. What are your next steps?",
        "How would you set up KPIs for a new distribution center?",
        "Explain a time you optimized a process end-to-end. What levers did you pull?",
        "How do you approach demand forecasting for a product with no history?",
        "Describe how you'd run a supplier negotiation for a critical component.",
    ],
    "Program / Product Management": [
        "How do you prioritize features when engineering capacity is limited?",
        "Walk me through how you'd launch a new program from scratch.",
        "How do you measure the success of a program you own?",
        "A project is behind schedule. How do you get it back on track?",
        "How do you align multiple stakeholders with conflicting goals?",
        "Tell me how you'd write a one-pager for a new initiative.",
        "How do you decide what NOT to build?",
    ],
    "Risk / Compliance": [
        "How would you build a risk framework for a new business line?",
        "Describe how you'd detect fraud in a high-volume transaction system.",
        "How do you balance compliance requirements against operational speed?",
        "Tell me about a time you identified a risk others missed.",
        "How would you design an audit process for a new vendor?",
    ],
}


def generate_mock_interview(n=5):
    """Generate a mixed mock interview of n questions across categories."""
    all_q = []
    for category, questions in MOCK_QUESTION_BANK.items():
        for q in questions:
            all_q.append({"category": category, "question": q})
    # Weighted: ensure at least 2 behavioral, then mix
    behavioral = [q for q in all_q if "Behavioral" in q["category"]]
    others = [q for q in all_q if "Behavioral" not in q["category"]]
    # Deterministic-ish shuffle using current second so repeated clicks vary
    seed = int(datetime.now().timestamp())
    rng = random.Random(seed)
    rng.shuffle(behavioral)
    rng.shuffle(others)
    n_behavioral = min(2, len(behavioral))
    picked = behavioral[:n_behavioral] + others[:(n - n_behavioral)]
    rng.shuffle(picked)
    return picked[:n]


# ============ MOCK SESSION HISTORY ============
# Answers used to vanish on every rerun. They persist now, because a written
# mock answer is usually the first draft of a STAR story.
def save_mock_session(questions, answers, company="", notes=""):
    """Persist one mock attempt. `answers` maps question index -> text."""
    return user_store.add("mock_sessions", {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "company": company,
        "notes": notes,
        "items": [
            {
                "category": q.get("category", ""),
                "question": q.get("question", ""),
                "answer": answers.get(i, ""),
            }
            for i, q in enumerate(questions, 1)
        ],
    }, message="Save mock interview session")


def get_mock_sessions():
    return sorted(
        user_store.get("mock_sessions"),
        key=lambda s: s.get("created_at") or "",
        reverse=True,
    )


def delete_mock_session(session_id):
    return user_store.delete("mock_sessions", session_id)


def practice_cadence():
    """Days since the last mock. Mentor advice is one every 2 weeks."""
    sessions = get_mock_sessions()
    if not sessions:
        return {"last": None, "days_since": None, "overdue": True}
    last = sessions[0].get("date") or sessions[0].get("created_at", "")[:10]
    try:
        days = (datetime.now() - datetime.strptime(last[:10], "%Y-%m-%d")).days
    except ValueError:
        return {"last": last, "days_since": None, "overdue": False}
    return {"last": last, "days_since": days, "overdue": days > 14}


# ============ INTERVIEW EXPERIENCE SOURCES ============
# Concrete, real sources for interview experiences (management/SCM/PM/ops roles)
# These are curated resource sites, NOT scraped content (ToS-safe).
def get_interview_experience_sources(company="", role=""):
    """Return concrete resource links for finding interview experiences."""
    import urllib.parse
    c = urllib.parse.quote(company) if company else ""
    r = urllib.parse.quote(role) if role else "supply chain"

    sources = [
        {
            "name": "Glassdoor Interviews",
            "desc": "Real interview questions & experiences by company + role. Best coverage for non-technical roles.",
            "url": f"https://www.glassdoor.com/Interview/{c.replace('%20','-')}-interview-questions-SRCH_KE0,{len(company)}.htm" if company else "https://www.glassdoor.com/Interview/index.htm",
            "search_url": f"https://www.glassdoor.com/Interview/index.htm?keyword={c}%20{r}",
        },
        {
            "name": "LeetCode Discuss (Interview Experiences)",
            "desc": "Has PM, Ops, and some management interview experiences — not just software. Search by company.",
            "url": f"https://leetcode.com/discuss/interview-experience?currentPage=1&query={c}%20{r}",
        },
        {
            "name": "Blind (teamblind.com)",
            "desc": "Anonymous professional network — search interview threads by company. Strong for tech company non-eng roles.",
            "url": f"https://www.teamblind.com/search/{c}%20interview" if company else "https://www.teamblind.com/",
        },
        {
            "name": "Reddit (r/supplychain, r/prodmgmt, r/ITCareerQuestions)",
            "desc": "Community interview debriefs. Search 'interview' + company.",
            "url": f"https://www.reddit.com/search/?q={c}%20{r}%20interview",
        },
        {
            "name": "Prepfully",
            "desc": "Interview questions + guides for PM, Ops, and management roles at top companies.",
            "url": f"https://prepfully.com/interview-questions?q={c}",
        },
        {
            "name": "Exponent (tryexponent.com)",
            "desc": "PM, TPM, and management interview prep with real question sets and mock interview practice.",
            "url": "https://www.tryexponent.com/questions",
        },
    ]
    return sources
