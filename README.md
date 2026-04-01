# Personal Finance Tracker

A complete mini-project built with Flask + SQLite + Chart.js.

## Features
- User registration and login
- Add income and expense transactions
- Expense categorization
- Monthly budget tracking
- Savings goals with progress updates
- Reports dashboard with Pie, Bar, and Line charts

## Tech Stack
- Frontend: HTML, CSS, JavaScript
- Backend: Python (Flask)
- Database: SQLite
- Charts: Chart.js

## Project Structure
```
Anup pal/
|- app.py
|- requirements.txt
|- finance.db (auto-created)
|- templates/
|- static/
```

## Run Locally
1. Create virtual environment
   - Windows PowerShell: `python -m venv .venv`
2. Activate it
   - `.venv\Scripts\Activate.ps1`
3. Install dependencies
   - `pip install -r requirements.txt`
4. Start app
   - `python app.py`
5. Open browser
   - `http://127.0.0.1:5000`

## Notes
- Database tables are auto-created on first run.
- Default secret key is for development only. Set `SECRET_KEY` in production.
