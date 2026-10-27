# Job Search Statistics

Show current statistics from the job search database.

## Instructions

1. Run the stats command:
   ```
   python -c "
   from src.reporter import get_stats
   import json
   stats = get_stats()
   print(json.dumps(stats, indent=2))
   "
   ```

2. Present the stats in a clean table showing:
   - Total jobs scraped
   - Scored vs unscored
   - Score distribution (90+, 75-89, 60-74, <60)
   - Top companies by average score
   - Most common job titles

3. Also show the latest report date and any pending unscored jobs.
