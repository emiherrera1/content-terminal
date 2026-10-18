# Call log

`calls.csv` holds every call the fund makes from the terminal, written before the outcome is known; `resolutions.csv` holds the outcomes. Both are append-only: each row carries the SHA-256 of the row before it, so editing or deleting an earlier row is detectable (`python -m terminal calls`). Commit both files after every call so git records when it was made.

Add a call: `python -m terminal call --topic woodworking --direction up --horizon 13 --outcome "Google Trends: woodworking, US" --p 0.6 --by Emily --note "DSS above 2 for a week; newcomers taking share"`

Resolve it: `python -m terminal resolve --id 1 --outcome hit --note "Search interest up 18% over 13 weeks, link to the data"`
