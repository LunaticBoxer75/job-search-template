"""Pass-2 scoring prompts. Claude Code subagents score the batch files this builds
(/find-jobs step 4); there is no API key. The rubric text is the profile's
`pass2_rubric` in config/profile.json."""

import json
from pathlib import Path

from .scraper import get_unscored_jobs, update_job_score

CONFIG_DIR = Path(__file__).parent.parent / "config"

def get_profile_summary():
    with open(CONFIG_DIR / "profile.json") as f:
        profile = json.load(f)

    candidate = profile["candidate"]
    exp = profile["experience_summary"]
    targets = profile["target_roles"]

    return f"""- Name: {candidate['name']}
- Current: {exp['current_role']} at {exp['current_company']} ({exp['years']} years)
- Education: {exp['education']['bachelors']}; {profile['identity']['current_study']}
- Target Location: {candidate['target_location']}
- Visa: {candidate['work_authorization']}
- Core Skills: {', '.join(exp['domain_expertise'][:6])}
- Tools: {', '.join(exp['tools'])}
- Certifications: {', '.join(exp['certifications'])}
- Target Roles (Primary): {', '.join(targets['primary'][:5])}
- Target Roles (Secondary): {', '.join(targets['secondary'][:4])}"""


def batch_score_prompt(jobs):
    """Build a batch scoring prompt for multiple jobs at once (more efficient)."""
    profile_summary = get_profile_summary()
    with open(CONFIG_DIR / "profile.json") as f:
        rubric = "\n".join(json.load(f)["pass2_rubric"])

    jobs_text = []
    for i, job in enumerate(jobs, 1):
        salary_str = "Not specified"
        if job.get("salary_min") and job.get("salary_max"):
            salary_str = f"${job['salary_min']:,.0f} - ${job['salary_max']:,.0f}"
        elif job.get("salary_min"):
            salary_str = f"${job['salary_min']:,.0f}+"

        description = job.get("description", "No description available")
        # The whole stored description (the scraper keeps 10,000 chars). At 2,000 the
        # requirements and legal sections — years, citizenship, sponsorship — were
        # usually cut off, so the hard rules couldn't fire.
        if len(description) > 10000:
            description = description[:10000] + "...[truncated]"

        jobs_text.append(f"""### Job {i} (ID: {job['job_id']})
**Title:** {job.get('title', 'Unknown')}
**Company:** {job.get('company', 'Unknown')}
**Location:** {job.get('location', 'Unknown')}
**Type:** {job.get('job_type', 'Unknown')}
**Salary:** {salary_str}
**Description:** {description}
""")

    return f"""You are a career advisor scoring job listings for a candidate.

## Candidate Profile
{profile_summary}

{rubric}

## Jobs to Score

{"".join(jobs_text)}

## Instructions
Score ALL jobs above. Return a JSON array with one entry per job:
[
  {{
    "job_id": "<from above>",
    "score": <0-100>,
    "reasoning": "<1-2 sentences>",
    "highlights": ["<key strengths>"],
    "red_flags": ["<concerns if any>"]
  }},
  ...
]

Return ONLY the JSON array, no other text."""


def parse_batch_scores(response_text, jobs):
    """Parse batch scoring response and update database."""
    try:
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0]
        scores = json.loads(response_text)
    except json.JSONDecodeError:
        return []

    results = []
    for score_entry in scores:
        job_id = score_entry.get("job_id")
        score = score_entry.get("score", 0)
        if job_id:
            update_job_score(job_id, score, score_entry)
            results.append(score_entry)
    return results


def score_all_pending(limit=50):
    """Get unscored jobs for manual/inline scoring. Returns jobs + prompts."""
    jobs = get_unscored_jobs(limit=limit)
    return jobs


if __name__ == "__main__":
    from rich.console import Console
    console = Console()
    jobs = score_all_pending()
    console.print(f"[bold]{len(jobs)} jobs awaiting scoring[/bold]")
    if jobs:
        console.print("\nTo score, run /find-jobs in Claude Code (uses inline Claude scoring)")
