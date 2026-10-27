# Add Job Search Query

Add a new search term to the job search configuration.

## Instructions

The user will provide a role or search term they want to add. 

1. Read the current `config/search_queries.json`
2. Ask what category it belongs to (primary/secondary/stretch) if not obvious
3. Add the new query to the JSON file
4. Confirm the addition and show the updated query list

## Example usage
User: "add search for data analyst"
→ Add `{"search_term": "data analyst", "category": "secondary"}` to queries array
