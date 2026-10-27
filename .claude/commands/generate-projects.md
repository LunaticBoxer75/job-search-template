# Generate Portfolio Projects

Identify skill gaps from job data and build complete project packages with documentation and code.

## Instructions

1. Check what gaps need projects:
   ```
   python -c "
   from src.projects import identify_skill_gaps, get_gaps_needing_projects, get_existing_projects
   
   gaps = identify_skill_gaps()
   print('ALL SKILL GAPS (from top job listings):')
   for name, count in gaps:
       print(f'  {name}: {count} jobs request this')
   
   existing = get_existing_projects()
   print(f'\nEXISTING PROJECTS: {existing}')
   
   needs = get_gaps_needing_projects()
   print(f'\nGAPS NEEDING NEW PROJECTS: {len(needs)}')
   for name, count, pct in needs:
       print(f'  → {name} ({pct}% of top jobs, {count} mentions)')
   "
   ```

2. **For each gap that needs a project, BUILD THE FULL PROJECT yourself:**

   Each project MUST include:
   - `projects/<skill-name>/README.md` — Complete beginner-friendly guide (300+ lines)
   - `projects/<skill-name>/<script>.py` — Fully runnable code that produces output
   - `projects/<skill-name>/requirements.txt` — Python dependencies

   Quality standard (look at `projects/python-demand-forecasting/` as reference):
   - Assumes ZERO prior experience with the tool/skill
   - Every step explained: what it is, why it matters, how to do it
   - Produces a visible artifact (chart, dashboard, report)
   - Includes copy-paste resume bullet point
   - Includes interview talking points
   - Uses ONLY free tools
   - Completable in 1-2 weeks at ~2hr/day

3. **DO NOT generate a project if:**
   - The gap appears in <15% of top job listings (not worth the time)
   - An existing project already covers it
   - There's no free tool available to learn the skill (e.g., SAP requires paid access unless a school or employer provides it)

4. After building, regenerate the index:
   ```
   python -c "
   from src.projects import generate_project_index
   generate_project_index()
   print('Index updated')
   "
   ```

5. Present to the user: what was built, which gap it fills, and next steps.

## When NOT to Generate
- If all significant gaps are already covered → tell the user
- If gaps are below 15% threshold → mention them but don't force a project
- If the skill requires paid tools with no free alternative → note it as a "course recommendation" instead
