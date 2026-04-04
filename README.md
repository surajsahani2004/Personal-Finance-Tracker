# Personal Finance Tracker

A complete mini-project built with Flask + SQLite + Chart.js.

## Features
- User registration and login
- Add income and expense transactions
- Expense categorization
- Monthly budget tracking
- Savings goals with progress updates
- Reports dashboard with Pie, Bar, and Line charts
- Google Calendar reminder notifications (OAuth)
- Gemini AI chat board for helper assistant

## Tech Stack
- Frontend: HTML, CSS, JavaScript
- Backend: Python (Flask)
- Database: SQLite
- Charts: Chart.js
- Notifications: Google Calendar API

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

## Google Calendar Setup (For Notifications)
1. Enable `Google Calendar API` in Google Cloud.
2. Create OAuth Client ID (`Web application`).
3. Add redirect URI:
   - `http://127.0.0.1:5000/oauth2callback`
4. Download OAuth JSON and rename it to:
   - `client_secret.json`
5. Place `client_secret.json` in project root (same folder as `app.py`).
6. Run app and open `Reminders` page, then click `Connect Google Calendar`.

## Gemini AI Setup (For Chat Board)
1. Create API key in Google AI Studio.
2. Set environment variable before running app:
   - PowerShell:
     - `$env:GEMINI_API_KEY="YOUR_GEMINI_API_KEY"`
3. Optional model override:
   - `$env:GEMINI_MODEL="gemini-2.0-flash"`
4. Open app and go to `AI Chat`.

## Notes
- Database tables are auto-created on first run.
- Default secret key is for development only. Set `SECRET_KEY` in production.
- Never commit `client_secret.json` to GitHub.

## Deploy On Render
1. Push latest code to GitHub (already done for this project).
2. In Render dashboard:
   - New + -> Blueprint
   - Select this repository
   - Render will read `render.yaml` and create web service
3. After first deploy, copy your app URL:
   - `https://YOUR_RENDER_APP.onrender.com`
4. Set/update these env vars in Render service:
   - `GOOGLE_REDIRECT_URI=https://YOUR_RENDER_APP.onrender.com/oauth2callback`
   - `GOOGLE_OAUTH_CLIENT_JSON=<contents of client_secret.json as single-line JSON>`
   - `GEMINI_API_KEY=<your Gemini API key>`
   - `GEMINI_MODEL=gemini-2.0-flash` (optional)
   - One-line JSON helper:
     - `python -c "import json;print(json.dumps(json.load(open('client_secret.json', encoding='utf-8'))))"`
5. In Google Cloud OAuth Client, add:
   - Authorized JavaScript origin: `https://YOUR_RENDER_APP.onrender.com`
   - Authorized redirect URI: `https://YOUR_RENDER_APP.onrender.com/oauth2callback`
6. Redeploy service from Render dashboard after env var update.

### Render Notes
- This setup uses SQLite at `/tmp/finance.db` for easy deployment.
- `/tmp` storage is ephemeral, so data may reset on restart/redeploy.
- For persistent production data, migrate to Render PostgreSQL or another managed DB.

## Deploy On Google Cloud Run
1. Install and login to Google Cloud CLI:
   - `gcloud auth login`
   - `gcloud config set project calendra-api-492109`
2. Enable required services:
   - `gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com`
3. Store OAuth JSON in Secret Manager:
   - `gcloud secrets create google-oauth-client-json --replication-policy=automatic`
   - `gcloud secrets versions add google-oauth-client-json --data-file=client_secret.json`
4. Build and deploy:
   - `gcloud builds submit --tag gcr.io/calendra-api-492109/personal-finance-tracker`
   - `gcloud run deploy personal-finance-tracker --image gcr.io/calendra-api-492109/personal-finance-tracker --platform managed --region asia-south1 --allow-unauthenticated --set-env-vars SECRET_KEY=CHANGE_ME,APP_TIMEZONE=Asia/Kolkata,SESSION_COOKIE_SECURE=1 --set-secrets GOOGLE_OAUTH_CLIENT_JSON=google-oauth-client-json:latest`
5. Grant secret access to Cloud Run service account:
   - `gcloud run services describe personal-finance-tracker --region asia-south1 --format="value(spec.template.spec.serviceAccountName)"`
   - `gcloud secrets add-iam-policy-binding google-oauth-client-json --member="serviceAccount:SERVICE_ACCOUNT_EMAIL" --role="roles/secretmanager.secretAccessor"`
6. After deploy, copy service URL and update Google OAuth client:
   - Authorized JavaScript origins: `https://YOUR_CLOUD_RUN_URL`
   - Authorized redirect URI: `https://YOUR_CLOUD_RUN_URL/oauth2callback`
7. Then redeploy with production redirect env:
   - `gcloud run services update personal-finance-tracker --region asia-south1 --set-env-vars GOOGLE_REDIRECT_URI=https://YOUR_CLOUD_RUN_URL/oauth2callback`

### Important
- `finance.db` is local SQLite and not durable on Cloud Run. For real multi-user production, migrate to Cloud SQL.
- While OAuth app is in Testing mode, every user email must be added in `Audience -> Test users`.
