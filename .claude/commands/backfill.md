# Backfill Jobs (4 months)

Run a deep scrape to catch all jobs from the last 4 months. Use this once to populate the database, then rely on daily /find-jobs for new listings. Run every command from the repo root.

## Instructions

1. Run the backfill scrape (this takes hours — run it in the background and watch the `[done/total]` lines):
   ```
   python -c "
   from src.scraper import scrape_all_jobs
   # 4 months = ~2880 hours
   jobs = scrape_all_jobs(hours_old=2880)
   print(f'Backfill complete: {len(jobs)} new jobs found')
   "
   ```

2. Quick-score anything left unscored (rows are scored at insert, so this is a safety net):
   ```
   python -c "
   from src.quick_scorer import score_all_unscored
   count = score_all_unscored()
   print(f'Quick-scored {count} jobs')
   "
   ```

3. Fetch LinkedIn descriptions and AI-score the top jobs with Claude inline: follow /find-jobs steps 3b and 4.

4. Export the deploy DB, and publish it when the dashboard is hosted (/find-jobs steps 7 and 8):
   ```
   python scripts/export_deploy_db.py
   python scripts/publish_jobs_db.py
   ```
