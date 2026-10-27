# Application Tracker

Track job application status, set follow-up reminders, and see pipeline progress.

## Instructions

### Show tracker dashboard:
```
python -c "
from src.tracker import generate_tracker_report
print(generate_tracker_report())
"
```

### Update a job's status:
The user will say something like "mark the Amazon Supply Chain Analyst as applied" or "I got an interview at Boeing".

1. Find the job in the database:
```
python -c "
from src.scraper import get_db
conn = get_db()
results = conn.execute('''
    SELECT job_id, title, company, score FROM jobs
    WHERE company LIKE '%COMPANY%' AND title LIKE '%TITLE%'
    ORDER BY score DESC LIMIT 5
''').fetchall()
for r in results:
    print(f'{r[0]} | {r[1]} @ {r[2]} (score: {r[3]})')
conn.close()
"
```

2. Update the status:
```
python -c "
from src.tracker import update_status
update_status('JOB_ID', 'STATUS', notes='NOTES', follow_up_date='YYYY-MM-DD')
print('Updated!')
"
```

Valid statuses: new, applied, interviewing, offer, rejected, skipped

### Check follow-ups due:
```
python -c "
from src.tracker import get_follow_ups_due
for f in get_follow_ups_due():
    print(f'{f[\"title\"]} @ {f[\"company\"]} - due {f[\"follow_up_date\"]}')
"
```
