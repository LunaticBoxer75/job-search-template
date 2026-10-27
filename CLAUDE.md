# Job Search Pipeline

Daily automated job search for one candidate: scrape → score (two passes) → report → Streamlit
dashboard. README.md has setup and hosting.

**Everything person-specific is in `config/profile.json`** — identity and intro text, the Pass-2
rubric (`pass2_rubric`), and the Pass-1 rules (`scoring`: role families, home area, keywords,
excluded fields, visa rules, internship mode, technical-PM cap). Retarget by editing that file, never
by hard-coding a person, school, employer or city in code. Searches are in `config/search_queries.json`.

## Quick start

```bash
pip install -r requirements.txt           # Python 3.12 venv
python -m src.pipeline scrape             # just scrape
python scripts/export_deploy_db.py        # build data/jobs_deploy.db for the dashboard
streamlit run app.py
```

No API key: Pass-2 scoring is Claude Code subagents reading batch files (`/find-jobs` step 4).

## Slash commands

- `/find-jobs` — full daily flow: scrape → Pass 1 check → LinkedIn descriptions → Pass 2 → report →
  export → publish → health checks. Keep it current when the pipeline changes.
- `/backfill` — one-time 4-month scrape · `/job-stats` · `/add-search` · `/track` ·
  `/generate-projects` · `/dashboard`

## Architecture

```
config/profile.json          ← The candidate: identity, rubric, scoring rules, search prefs
config/search_queries.json   ← Search terms by category (primary/secondary/stretch)
config/company_sizes.json    ← Curated size tiers for the most frequent employers
content/*.json               ← Static interview-prep content (cases, frameworks, metrics,
                               companies, loop map, resources, playbook)
src/profile.py              ← Loads config/profile.json
src/scraper.py              ← JobSpy scraper (home area + national, FT + intern), score-at-insert
src/quick_scorer.py         ← Pass 1: rule-based scoring of ALL jobs
src/scorer.py               ← Pass 2 batch prompts (rubric from the profile)
src/locations.py            ← Home area vs rest of USA; big-metro map (city + state, exact)
src/user_store.py           ← The candidate's data: durable JSON + GitHub write-back
src/jobs_data.py            ← Downloads the deploy DB from the jobs-data branch
src/reporter.py, tracker.py, todos.py, networking.py, interview_prep.py, prep_content.py,
    sponsor_data.py, company_size.py, projects.py, referrals.py, skills_insights.py
app.py                      ← Streamlit dashboard, 14 pages via st.navigation
scripts/export_deploy_db.py ← Prune + trim → data/jobs_deploy.db
scripts/publish_jobs_db.py  ← Publishes the deploy DB to jobs-data (one commit, force-replaced)
scripts/backfill_linkedin_descriptions.py ← Fetches LinkedIn descriptions for top jobs
scripts/rescore_all.py      ← Re-run Pass 1 on every row after a rule change (never Pass-2 rows)
scripts/smoke_test_pages.py ← Renders all pages headlessly; exit 1 on any exception
scripts/export_template.py  ← Exports a history-free copy with no personal data, and checks it
```

## Data stores — three branches, deliberately separate

| Where | Holds | Written by |
|---|---|---|
| `data/jobs.db` (local only, gitignored) | Full corpus, one row per posting URL | Scraper |
| `jobs-data` branch → `data/jobs_deploy.db` | Jobs scoring 70+ — **one commit, force-replaced each run** | `scripts/publish_jobs_db.py` |
| `user-data` branch → `data/user_data.json` | The candidate's applications, coffee chats, to-dos, recs, STAR stories, mock sessions, dismissals | Dashboard + local scripts, via GitHub API (`src/user_store.py`) |
| `main` | Code + `content/` only | Normal reviewed commits |

**Why:** Streamlit Cloud's filesystem is ephemeral, so the candidate's data cannot live in a file the
pipeline replaces; saving to `main` would restart the app on every click (Cloud reloads on commits to
its branch); and a jobs DB committed to `main` grows history that every cold start downloads.

**Never collapse these.** Nothing may write the candidate's data into a jobs DB, commit a jobs DB to
`main`, or force-push anything except `jobs-data`. Never rewrite `main` history.

**Token:** `MSCM_GITHUB_TOKEN` (fine-grained, this repo only, Contents read/write, with expiry) in
Streamlit Cloud secrets and local `.streamlit/secrets.toml`. Not `GITHUB_TOKEN`: gh/CI set that name
and it would make local scripts commit. Without the token, `user_store` uses the local
`data/user_data.json` (a snapshot, not authoritative). `MSCM_STORE_MODE=file` forces local mode even
with a token — tests and the smoke test must never touch GitHub.

**Store behaviour:** every save is read-latest → apply → write-with-SHA; on a conflict it re-applies
the change on the newer copy, so no writer loses data. An unreadable store raises instead of being
treated as empty. If GitHub is unreachable the dashboard shows last-good data read-only with a banner
and refuses writes rather than dropping them.

## Scoring (two passes)

1. **Pass 1 — `src/quick_scorer.py`**, rules over every job, applied at insert. Score = role fit
   (the best-matching `scoring.role_families` entry, ≤50) + role-scaled seniority + location (home
   core > home area > remote > big metro > rest of home state > elsewhere) + employer H-1B volume
   (log scale) + freshness + description signals, then hard caps: `excluded_fields`, years required
   (read from the requirements section, largest stated minimum), visa caps when
   `needs_visa_sponsorship` is true (clearance / U.S. person / citizen-only / federal / defense),
   `technical_pm_cap`, and senior titles filed as internships when `internships` is `fallback`.
   Scores carry one decimal so the top band ranks.
   - Nothing under 70 is stored (`min_store_score`); the export deletes every <70 row except tracked
     or dismissed jobs, including ones Claude rated under 70 (remembered in `rejected_urls` for 30
     days so the scraper can't re-add them). Closed LinkedIn postings (`expired_postings`) are deleted
     the same way.
   - After changing rules or the profile's `scoring` block: `python scripts/rescore_all.py --apply`
     (one transaction; never touches Pass-2 rows) so old and new rows share one scale. Measure with
     `scripts/eval_quick_scorer.py` (read-only, hand-labelled sample — relabel it for a new profile).
2. **Pass 2 — Claude inline.** Claude Code subagents read descriptions and rescore with the profile's
   `pass2_rubric`, stamping `score_breakdown.scorer = "ai"`. `/find-jobs` reads every unread home-area
   FT job at 70+ and every national FT job at 85+, plus 5 + 5 internships. LinkedIn search returns no
   description, so `scripts/backfill_linkedin_descriptions.py` fetches them first.

**Internship vs full-time comes from the TITLE** (`scraper.classify_job_type`): intern / internship /
co-op / "<season> 20xx". The boards' internship filter is loose; new-grad roles are full-time.
**Region comes from the job's location**, not the search that found it. The home area's internal
region id is `seattle` (stored rows use it; do not rename it); everything else is `national`.

**Known limits — do not paper over them:** rows without a description can't be read by Pass 2 and
Pass 1's description rules can't fire on them — mark them `(quick score only)`. Defense-employer caps
are by company name when there's no description. Company size exists only for employers in
`config/company_sizes.json`. The same role posted in several offices appears several times (dedup is
by URL only).

## Keyword precision

- Every keyword rule uses **word boundaries** (`_words()` in `src/quick_scorer.py`), never plain
  substrings: "intern" must not match "Internal", "excel" must not match "excellent".
- Write **plurals** explicitly in patterns (`analysts?`, `buyers?`).
- **Un-escape Markdown** (`\-`, `\&`, `\+` from scraped descriptions) before matching, as
  `src/skills_insights.py` does. Count skill demand from full descriptions only.

## Dashboard

- Same `app.py` locally and on Streamlit Community Cloud. Jobs come from `data/jobs_deploy.db`
  (downloaded from `jobs-data` when hosted), the candidate's data from `user_store`.
- **Pages, not `st.tabs`:** the 14 pages use `st.navigation`, so only the open page runs. Tabs execute
  every tab's code on every click.
- **Theme:** `.streamlit/config.toml` defines BOTH light and dark variants, so the app follows the
  device. Never set `base` (it forces one mode). Page styles are in `app.py`'s `CSS` block, colored
  via `PALETTES` for the active mode (`st.context.theme`).
- The CSS targets action buttons by key prefix (`st-key-ok_`, `st-key-x_`, `st-key-sel_`) — keep those
  prefixes if you rename keys.
- Job rows: 🤖 = Claude read it; ties rank Claude-read first, then newest. "Open ↗" never tracks; ✓
  marks applied and queues a 7-day follow-up; ✕ dismisses everywhere. Scraped text is HTML-escaped and
  only http(s) links render (rows use `unsafe_allow_html`).
- The ATS bookmarklet fills only name, email, phone, LinkedIn, city, school and degree. It never
  touches sponsorship, work authorization, EEO, salary or start date; those are shown as copy-ready
  answers from `manual_answers`.
- To-dos are soft-deleted (`deleted`), because coffee-chat action items are re-imported by text.
- Check visual changes with real screenshots, not only AppTest. Run
  `python scripts/smoke_test_pages.py` after any dashboard change (must end `problems: 0`).

## Content accuracy rules

- **"SIMDC" is a FAKE framework** that circulates in AI-generated advice. Never use it. The real
  supply-chain diagnostic framework is **SCOR** (ASCM): Plan → Source → Transform → Order → Fulfill →
  Return → Orchestrate.
- **Amazon has 16 Leadership Principles, not 14.**
- Entries marked `verified: false` in `content/companies.json` were not confirmed from an official
  source — don't present them as fact.
- Immigration notes in `content/` are preparation guidance, not legal advice; rules differ by school.

## Sharing

Never commit `data/`, secrets, or a real person's profile to a public repo. Anyone who can read a repo
can read every branch and its history. To share, run `python scripts/export_template.py <empty dir>`
and push that copy as a new repository; `--check <dir>` must print `clean: nothing identifying found`.
