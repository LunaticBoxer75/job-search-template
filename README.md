# Job Search Pipeline + Dashboard

A daily job-search pipeline for one candidate, plus a Streamlit dashboard to work the results.

1. **Scrape** Indeed and LinkedIn with [JobSpy](https://github.com/speedyapply/JobSpy) for every
   search term × job type (full-time, internship) × location you configure.
2. **Pass 1** — a rule-based scorer (`src/quick_scorer.py`) scores *every* posting 0-100 before it is
   stored: role family from the title, seniority, location, employer sponsorship history, freshness,
   description keywords, then hard caps. Postings under 70 are not kept.
3. **Pass 2** — Claude Code subagents read the full description of the top postings and rescore them
   with a rubric you write (`/find-jobs` step 4). There is no API key; Claude Code itself is the scorer.
4. **Dashboard** (`app.py`) — 14 pages: a Today shortlist, the job panels, referrals, an H-1B sponsor
   lookup, an application tracker, to-dos, networking, interview prep, skills gap, portfolio projects,
   fellowships, tools and export.

The example configuration targets supply chain / operations / risk & compliance roles. Everything
person-specific is in `config/profile.json`; you retarget the whole system by editing that file, not
the code.

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

You also need `sqlite3` on the PATH (the `/find-jobs` checks use it) and
[Claude Code](https://docs.claude.com/en/docs/claude-code) for Pass 2 and the slash commands in
`.claude/commands/`.

## Retarget it to yourself

Copy `config/profile.example.json` to `config/profile.json` (the copy in this repo is already the
example) and edit it field by field:

| Field | What it does |
|---|---|
| `candidate` | Name, email, phone, LinkedIn handle, location, school, work authorization. Used by the ATS bookmarklet, referral messages and the Pass-2 prompt. |
| `experience_summary` | `years` (drives the "years required" caps), current role and company, domain expertise, tools, certifications, education. |
| `key_achievements`, `target_roles` | Resume bullets for the resume rephraser; target titles shown to the Pass-2 scorer. |
| `identity` | `short_intro` / `long_intro` (sentences used in referral and outreach messages; `{years}` is filled in), `school_short` and `linkedin_school_id` (the alumni-search links), `home_city`, `app_title`, `brand_subtitle`, `salary_benchmark`, and `github_repo` (`owner/name` of the repo that holds the data branches). |
| `pass2_rubric` | The rubric Claude scores with in Pass 2: weighted dimensions, hard rules that override them, score anchors. Write it in plain sentences. Keep it consistent with the `scoring` block. |
| `scoring.role_families` | The core of Pass 1. Each family has `points` (role fit, max 50), a `label`, and `title_patterns`: regex fragments matched against the job title with word boundaries at both ends (write plurals explicitly, e.g. `analysts?`). The highest-scoring matching family wins. |
| `scoring.top_families` | Labels of your target families; a title that matches two of them gets a small bonus. |
| `scoring.core_target_terms` | Title terms that mark a core target role (they protect it from some field caps). |
| `scoring.relevance_words` / `relevance_phrases` | Description keywords from your background; each match adds a little. Words are whole-word matches; phrases are regex fragments. |
| `scoring.home_area` | Your home area: `label`, `core_label`, `state`, `state_name`, `core_cities`, `near_cities` (lowercase). Jobs here get the highest location points and their own tab. |
| `scoring.excluded_fields` | Fields to cap hard (`clinical`, `healthcare`, `food`, `retail`, `driving`, `warehouse`, `trades`, `unrelated`, `engineering`, `it_security`). Remove the ones that are your field. |
| `scoring.needs_visa_sponsorship` | `true` turns on the clearance / citizen-only / U.S.-person / defense-employer caps and the sponsorship rules. |
| `scoring.internships` | `fallback` lists internships after full-time and caps senior titles filed as internships; `normal` treats them alike. |
| `scoring.technical_pm_cap` | A number caps technical program/project/product manager titles at that score; `null` means no cap. |
| `skills_have` | Skills and certifications you already have, so the skills-gap views and the project generator skip them. |
| `seed` | Optional `interview_recs` and `todos` added to the store on first load. |
| `manual_answers` | Answers shown on the Tools page for questions the bookmarklet deliberately never fills (sponsorship, work authorization, start date). |
| `search_preferences` | `locations_home` and `locations_national` (one search per location), plus `hours_old`, `results_per_search`, and optional `scrape_workers` (default 3) and `min_store_score` (default 70). |

Then edit `config/search_queries.json`: one entry per search term, with a `category`
(`primary` / `secondary` / `stretch`).

Notes:

- The database's internal region id for the home area is `seattle`, whatever your home area is.
  Stored rows use it; do not rename it. Everything else is `national`.
- The big-metro list used for location points and the 📍 pill is in `src/locations.py`.
- After you change the `scoring` block on an existing database, run `python scripts/rescore_all.py`
  (dry run) and then `--apply`, so old and new rows share one scale.
- `content/*.json` (cases, frameworks, metrics, company notes, loop map, resources, prep playbook) and
  `projects/` are example interview-prep material for a supply-chain / ops / risk candidate. Edit or
  replace them for another field. `scripts/eval_quick_scorer.py` has a hand-labelled sample for the
  example profile; relabel it before you trust it for yours.

## First run (local only)

```bash
export MSCM_STORE_MODE=file        # keep your data in data/user_data.json, never on GitHub
mkdir -p data
python -c "from src.scraper import get_db; get_db().close(); from src import user_store; user_store.save(user_store._empty(), 'Initialize')"
```

Then either run `/find-jobs` in Claude Code (the full daily flow, including Pass 2), or scrape only:

```bash
python -m src.pipeline scrape      # Indeed + LinkedIn; LinkedIn is slow, so this takes hours
python scripts/export_deploy_db.py # builds data/jobs_deploy.db for the dashboard (prunes jobs.db)
streamlit run app.py
```

`python scripts/smoke_test_pages.py` renders every page headlessly and ends with `problems: 0`.

## Hosting on Streamlit Community Cloud

The hosted app's filesystem is ephemeral, so data lives on GitHub, on three branches of one repo:

| Branch | Holds | Written by |
|---|---|---|
| `main` | Code and `content/` | Your normal commits. Streamlit Cloud deploys from it. |
| `user-data` | `data/user_data.json` — the candidate's applications, chats, to-dos, stories, mock sessions, dismissals | The dashboard and local scripts, through the GitHub contents API (`src/user_store.py`) |
| `jobs-data` | `data/jobs_deploy.db` — one commit, force-replaced on each publish | `scripts/publish_jobs_db.py` |

Why separate: saving to `main` would restart the app on every click, and a jobs-DB commit on `main`
would add megabytes of history on every run and overwrite whatever the candidate typed.

Steps:

1. Push the code to a **private** GitHub repo. Add it as a remote named `github`
   (`git remote add github git@github.com:OWNER/REPO.git`); `publish_jobs_db.py` pushes to that name.
   Set `identity.github_repo` in the profile to `OWNER/REPO`.
2. Create a fine-grained token: this repo only, permission **Contents: Read and write**, with an expiry
   date. Put it in `.streamlit/secrets.toml` as `MSCM_GITHUB_TOKEN` (see
   `.streamlit/secrets.toml.example`) and in the app's Secrets on Streamlit Cloud. It is named
   `MSCM_GITHUB_TOKEN`, not `GITHUB_TOKEN`, so tools that set `GITHUB_TOKEN` cannot make local scripts
   write to GitHub.
3. Create a `user-data` branch on GitHub (for example from `main` in the GitHub branch menu; the store
   only reads and writes `data/user_data.json` on it). Unset `MSCM_STORE_MODE` locally so scripts use
   that branch. The first save creates `data/user_data.json` there.
4. After each `/find-jobs` run: `python scripts/export_deploy_db.py`, then
   `python scripts/publish_jobs_db.py` (`--dry-run` to check). The app notices the new `jobs-data`
   commit within about 10 minutes; no redeploy.
5. Deploy `app.py` from `main` on Streamlit Community Cloud and share it invite-only.

If GitHub is unreachable or the token has expired, the dashboard shows the last-good data read-only
with a banner and refuses writes rather than dropping them.

## Privacy

- Never commit `data/`, `.streamlit/secrets.toml`, or the profile of a real person to a public repo.
  `.gitignore` covers `data/` and the secrets file; the profile is your call.
- Anyone who can read a repo can read **every branch and its full history** — including `user-data`
  and `jobs-data`. Keep the repo private, and to share the code, export a fresh copy with no history
  (`python scripts/export_template.py <empty dir>`, then check it with `--check`) and push that as a
  new repository.
- The scraper never scrapes LinkedIn people search, Glassdoor, Blind or Levels.fyi; the dashboard only
  links to them.
