from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "finance.db"))
GOOGLE_CLIENT_SECRETS_FILE = os.path.join(BASE_DIR, "client_secret.json")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:5000/oauth2callback")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

CATEGORIES = [
    "Food",
    "Transport",
    "Rent",
    "Bills",
    "Health",
    "Education",
    "Shopping",
    "Entertainment",
    "Salary",
    "Business",
    "Other",
]


_oauth_file_cache: dict | None = None
_oauth_file_cache_mtime: float | None = None
_oauth_env_cache: dict | None = None
_oauth_env_cache_raw: str | None = None


def new_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_db() -> sqlite3.Connection:
    if "db_conn" not in g:
        g.db_conn = new_db_connection()
    return g.db_conn


@app.teardown_appcontext
def close_db(_: BaseException | None) -> None:
    conn = g.pop("db_conn", None)
    if conn is not None:
        conn.close()


def init_db() -> None:
    conn = new_db_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                monthly_budget REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                notes TEXT,
                tx_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                target_amount REAL NOT NULL,
                current_amount REAL NOT NULL DEFAULT 0,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS google_tokens (
                user_id INTEGER PRIMARY KEY,
                credentials_json TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_user_type_date
            ON transactions(user_id, type, tx_date);

            CREATE INDEX IF NOT EXISTS idx_transactions_user_date
            ON transactions(user_id, tx_date);

            CREATE INDEX IF NOT EXISTS idx_transactions_user_type_category
            ON transactions(user_id, type, category);

            CREATE INDEX IF NOT EXISTS idx_goals_user_status_due
            ON goals(user_id, status, due_date);
            """
        )
        conn.commit()
    finally:
        conn.close()


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "error")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped


def current_user_id() -> int:
    return int(session["user_id"])


def get_month_bounds(target_date: date | None = None) -> tuple[str, str]:
    base = target_date or date.today()
    start = base.replace(day=1)
    if start.month == 12:
        next_month = start.replace(year=start.year + 1, month=1)
    else:
        next_month = start.replace(month=start.month + 1)
    return start.isoformat(), next_month.isoformat()


def shift_month_start(month_start: date, delta_months: int) -> date:
    month_zero = month_start.month - 1 + delta_months
    year = month_start.year + (month_zero // 12)
    month = (month_zero % 12) + 1
    return month_start.replace(year=year, month=month)


def get_google_oauth_config() -> dict | None:
    global _oauth_env_cache, _oauth_env_cache_raw, _oauth_file_cache, _oauth_file_cache_mtime

    json_env = os.getenv("GOOGLE_OAUTH_CLIENT_JSON", "").strip()
    if json_env:
        if json_env == _oauth_env_cache_raw:
            return _oauth_env_cache
        try:
            _oauth_env_cache = json.loads(json_env)
            _oauth_env_cache_raw = json_env
            return _oauth_env_cache
        except json.JSONDecodeError:
            _oauth_env_cache = None
            _oauth_env_cache_raw = json_env
            return None

    if os.path.exists(GOOGLE_CLIENT_SECRETS_FILE):
        try:
            mtime = os.path.getmtime(GOOGLE_CLIENT_SECRETS_FILE)
            if _oauth_file_cache is not None and _oauth_file_cache_mtime == mtime:
                return _oauth_file_cache

            with open(GOOGLE_CLIENT_SECRETS_FILE, "r", encoding="utf-8") as f:
                _oauth_file_cache = json.load(f)
                _oauth_file_cache_mtime = mtime
                return _oauth_file_cache
        except (OSError, json.JSONDecodeError):
            _oauth_file_cache = None
            _oauth_file_cache_mtime = None
            return None

    return None


def has_google_oauth_config() -> bool:
    return get_google_oauth_config() is not None


def save_google_credentials(user_id: int, credentials_json: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO google_tokens(user_id, credentials_json, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                credentials_json = excluded.credentials_json,
                updated_at = datetime('now')
            """,
            (user_id, credentials_json),
        )


def load_google_credentials(user_id: int) -> Credentials | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT credentials_json FROM google_tokens WHERE user_id = ?",
            (user_id,),
        ).fetchone()

    if not row:
        return None

    try:
        token_info = json.loads(row["credentials_json"])
        credentials = Credentials.from_authorized_user_info(token_info, GOOGLE_SCOPES)
    except Exception:
        clear_google_credentials(user_id)
        return None

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(GoogleAuthRequest())
            save_google_credentials(user_id, credentials.to_json())
        except Exception:
            clear_google_credentials(user_id)
            return None

    return credentials


def clear_google_credentials(user_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM google_tokens WHERE user_id = ?", (user_id,))


def build_calendar_service(user_id: int):
    credentials = load_google_credentials(user_id)
    if not credentials:
        return None
    return build("calendar", "v3", credentials=credentials)


def get_gemini_api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def build_gemini_contents(message: str, history: list[dict]) -> list[dict]:
    contents: list[dict] = []
    for item in history[-12:]:
        role = "model" if item.get("role") == "assistant" else "user"
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        contents.append({"role": role, "parts": [{"text": text}]})

    contents.append({"role": "user", "parts": [{"text": message}]})
    return contents


def generate_gemini_reply(message: str, history: list[dict]) -> str:
    api_key = get_gemini_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing on server.")

    model_name = os.getenv("GEMINI_MODEL", GEMINI_MODEL).strip() or GEMINI_MODEL
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model_name, safe='')}:generateContent"
        f"?key={urllib.parse.quote(api_key, safe='')}"
    )

    payload = {
        "contents": build_gemini_contents(message, history),
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 800,
        },
    }

    request_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Gemini API HTTP {exc.code}: {detail[:180]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("Network error while calling Gemini API.") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Invalid JSON response from Gemini API.") from exc

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("No response candidates returned by Gemini API.")

    parts = candidates[0].get("content", {}).get("parts", [])
    text_chunks = [part.get("text", "").strip() for part in parts if part.get("text")]
    reply = "\n".join(chunk for chunk in text_chunks if chunk)

    if not reply:
        raise RuntimeError("Gemini response was empty.")

    return reply


@app.get("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not full_name or not email or not password:
            flash("All fields are required.", "error")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("register.html")

        password_hash = generate_password_hash(password)
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO users(full_name, email, password_hash) VALUES (?, ?, ?)",
                    (full_name, email, password_hash),
                )
            flash("Account created successfully. Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already exists. Use a different email.", "error")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        with get_db() as conn:
            user = conn.execute(
                "SELECT id, full_name, password_hash FROM users WHERE email = ?",
                (email,),
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = int(user["id"])
            session["user_name"] = user["full_name"]
            flash("Welcome back.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.get("/logout")
@login_required
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


@app.get("/dashboard")
@login_required
def dashboard():
    user_id = current_user_id()
    month_start, next_month_start = get_month_bounds()
    month_key = month_start[:7]

    with get_db() as conn:
        user = conn.execute(
            "SELECT full_name, monthly_budget FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        monthly_totals = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END), 0) AS expense_total,
                COALESCE(SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END), 0) AS income_total
            FROM transactions
            WHERE user_id = ?
              AND tx_date >= ?
              AND tx_date < ?
            """,
            (user_id, month_start, next_month_start),
        ).fetchone()

        recent_transactions = conn.execute(
            """
            SELECT type, category, amount, notes, tx_date
            FROM transactions
            WHERE user_id = ?
            ORDER BY tx_date DESC, id DESC
            LIMIT 8
            """,
            (user_id,),
        ).fetchall()

        goals_rows = conn.execute(
            """
            SELECT id, title, target_amount, current_amount, due_date, status
            FROM goals
            WHERE user_id = ?
            ORDER BY status ASC, due_date ASC, id DESC
            """,
            (user_id,),
        ).fetchall()

    goals = []
    for row in goals_rows:
        target = float(row["target_amount"])
        current = float(row["current_amount"])
        progress = 0.0 if target <= 0 else min(100.0, (current / target) * 100.0)
        goals.append(
            {
                "id": row["id"],
                "title": row["title"],
                "target_amount": target,
                "current_amount": current,
                "due_date": row["due_date"],
                "status": row["status"],
                "progress": progress,
            }
        )

    budget = float(user["monthly_budget"]) if user else 0.0
    expense_total = float(monthly_totals["expense_total"]) if monthly_totals else 0.0
    income_total = float(monthly_totals["income_total"]) if monthly_totals else 0.0

    return render_template(
        "dashboard.html",
        user_name=user["full_name"] if user else "User",
        month_key=month_key,
        budget=budget,
        expense_total=expense_total,
        income_total=income_total,
        savings=income_total - expense_total,
        remaining=budget - expense_total,
        recent_transactions=recent_transactions,
        goals=goals,
    )


@app.post("/budget")
@login_required
def update_budget():
    budget_raw = request.form.get("monthly_budget", "0").strip()
    try:
        monthly_budget = max(0.0, float(budget_raw))
    except ValueError:
        flash("Invalid budget value.", "error")
        return redirect(url_for("dashboard"))

    with get_db() as conn:
        conn.execute(
            "UPDATE users SET monthly_budget = ? WHERE id = ?",
            (monthly_budget, current_user_id()),
        )

    flash("Monthly budget updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/transactions/add", methods=["GET", "POST"])
@login_required
def add_transaction():
    if request.method == "POST":
        tx_type = request.form.get("type", "expense").strip().lower()
        category = request.form.get("category", "Other").strip() or "Other"
        amount_raw = request.form.get("amount", "").strip()
        notes = request.form.get("notes", "").strip()
        tx_date = request.form.get("tx_date", date.today().isoformat()).strip()

        if tx_type not in {"income", "expense"}:
            flash("Transaction type must be income or expense.", "error")
            return render_template("add_transaction.html", categories=CATEGORIES, today=date.today().isoformat())

        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash("Amount must be a positive number.", "error")
            return render_template("add_transaction.html", categories=CATEGORIES, today=date.today().isoformat())

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO transactions(user_id, type, category, amount, notes, tx_date)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (current_user_id(), tx_type, category, amount, notes, tx_date),
            )

        flash("Transaction saved.", "success")
        return redirect(url_for("dashboard"))

    return render_template("add_transaction.html", categories=CATEGORIES, today=date.today().isoformat())


@app.route("/goals", methods=["GET", "POST"])
@login_required
def goals():
    user_id = current_user_id()

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        target_raw = request.form.get("target_amount", "").strip()
        due_date = request.form.get("due_date", "").strip() or None

        if not title:
            flash("Goal title is required.", "error")
            return redirect(url_for("goals"))

        try:
            target_amount = float(target_raw)
            if target_amount <= 0:
                raise ValueError
        except ValueError:
            flash("Target amount must be positive.", "error")
            return redirect(url_for("goals"))

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO goals(user_id, title, target_amount, due_date)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, title, target_amount, due_date),
            )

        flash("Goal added.", "success")
        return redirect(url_for("goals"))

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, title, target_amount, current_amount, due_date, status
            FROM goals
            WHERE user_id = ?
            ORDER BY status ASC, due_date ASC, id DESC
            """,
            (user_id,),
        ).fetchall()

    goal_list = []
    for row in rows:
        target = float(row["target_amount"])
        current = float(row["current_amount"])
        progress = 0.0 if target <= 0 else min(100.0, (current / target) * 100.0)
        goal_list.append(
            {
                "id": row["id"],
                "title": row["title"],
                "target_amount": target,
                "current_amount": current,
                "due_date": row["due_date"],
                "status": row["status"],
                "progress": progress,
            }
        )

    return render_template("goals.html", goals=goal_list)


@app.post("/goals/<int:goal_id>/update")
@login_required
def update_goal(goal_id: int):
    current_raw = request.form.get("current_amount", "0").strip()

    try:
        current_amount = max(0.0, float(current_raw))
    except ValueError:
        flash("Invalid goal progress amount.", "error")
        return redirect(url_for("goals"))

    user_id = current_user_id()
    with get_db() as conn:
        goal = conn.execute(
            "SELECT target_amount FROM goals WHERE id = ? AND user_id = ?",
            (goal_id, user_id),
        ).fetchone()

        if not goal:
            flash("Goal not found.", "error")
            return redirect(url_for("goals"))

        status = "completed" if current_amount >= float(goal["target_amount"]) else "active"
        conn.execute(
            """
            UPDATE goals
            SET current_amount = ?, status = ?
            WHERE id = ? AND user_id = ?
            """,
            (current_amount, status, goal_id, user_id),
        )

    flash("Goal progress updated.", "success")
    return redirect(url_for("goals"))


@app.get("/reports")
@login_required
def reports():
    return render_template("reports.html")


@app.get("/ai-chat")
@login_required
def ai_chat():
    return render_template("ai_chat.html", gemini_ready=bool(get_gemini_api_key()))


@app.post("/api/ai-chat")
@login_required
def api_ai_chat():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    history = payload.get("history") or []

    if not message:
        return jsonify({"error": "Message is required."}), 400

    if not isinstance(history, list):
        history = []

    try:
        reply = generate_gemini_reply(message, history)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"reply": reply})


@app.get("/calendar/connect")
@login_required
def calendar_connect():
    oauth_config = get_google_oauth_config()
    if not oauth_config:
        flash("Google OAuth config missing. Add client_secret.json or set GOOGLE_OAUTH_CLIENT_JSON.", "error")
        return redirect(url_for("reminders"))

    flow = Flow.from_client_config(
        oauth_config,
        scopes=GOOGLE_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["google_oauth_state"] = state
    return redirect(auth_url)


@app.get("/oauth2callback")
@login_required
def oauth2callback():
    state = session.get("google_oauth_state")
    if not state:
        flash("Missing OAuth state. Please connect calendar again.", "error")
        return redirect(url_for("reminders"))

    try:
        oauth_config = get_google_oauth_config()
        if not oauth_config:
            flash("Google OAuth config missing. Add client_secret.json or set GOOGLE_OAUTH_CLIENT_JSON.", "error")
            return redirect(url_for("reminders"))

        flow = Flow.from_client_config(
            oauth_config,
            scopes=GOOGLE_SCOPES,
            state=state,
            redirect_uri=GOOGLE_REDIRECT_URI,
        )
        flow.fetch_token(authorization_response=request.url)
        save_google_credentials(current_user_id(), flow.credentials.to_json())
        flash("Google Calendar connected successfully.", "success")
    except Exception:
        flash("Google Calendar connect failed. Check redirect URI and OAuth settings.", "error")

    return redirect(url_for("reminders"))


@app.post("/calendar/disconnect")
@login_required
def calendar_disconnect():
    clear_google_credentials(current_user_id())
    flash("Google Calendar disconnected.", "success")
    return redirect(url_for("reminders"))


@app.route("/reminders", methods=["GET", "POST"])
@login_required
def reminders():
    user_id = current_user_id()
    calendar_connected = load_google_credentials(user_id) is not None

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        reminder_date = request.form.get("reminder_date", "").strip()
        reminder_time = request.form.get("reminder_time", "").strip()
        notify_before_raw = request.form.get("notify_before", "10").strip()

        if not calendar_connected:
            flash("Please connect Google Calendar first.", "error")
            return redirect(url_for("reminders"))

        if not title or not reminder_date or not reminder_time:
            flash("Title, date, and time are required.", "error")
            return redirect(url_for("reminders"))

        try:
            notify_before = int(notify_before_raw)
            notify_before = max(0, min(notify_before, 1440))
            start_dt = datetime.strptime(f"{reminder_date} {reminder_time}", "%Y-%m-%d %H:%M")
            end_dt = start_dt + timedelta(minutes=30)
        except ValueError:
            flash("Invalid date/time or reminder minutes.", "error")
            return redirect(url_for("reminders"))

        service = build_calendar_service(user_id)
        if not service:
            flash("Google Calendar connection expired. Please connect again.", "error")
            return redirect(url_for("reminders"))

        event_body = {
            "summary": title,
            "description": description,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": APP_TIMEZONE,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": APP_TIMEZONE,
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": notify_before},
                    {"method": "email", "minutes": notify_before},
                ],
            },
        }

        try:
            service.events().insert(calendarId="primary", body=event_body).execute()
            flash("Reminder created in Google Calendar.", "success")
            return redirect(url_for("reminders"))
        except Exception:
            flash("Failed to create reminder. Verify Calendar API and OAuth scopes.", "error")
            return redirect(url_for("reminders"))

    return render_template(
        "reminders.html",
        oauth_ready=has_google_oauth_config(),
        calendar_connected=calendar_connected,
        today=date.today().isoformat(),
        current_time=datetime.now().strftime("%H:%M"),
    )


@app.get("/api/reports")
@login_required
def api_reports():
    user_id = current_user_id()
    month_start_iso, next_month_iso = get_month_bounds()
    six_month_start_iso = shift_month_start(date.fromisoformat(month_start_iso), -5).isoformat()

    with get_db() as conn:
        category_rows = conn.execute(
            """
            SELECT category, ROUND(SUM(amount), 2) AS total
            FROM transactions
            WHERE user_id = ? AND type = 'expense'
            GROUP BY category
            ORDER BY total DESC
            """,
            (user_id,),
        ).fetchall()

        monthly_expense_rows = conn.execute(
            """
            SELECT strftime('%Y-%m', tx_date) AS month, ROUND(SUM(amount), 2) AS total
            FROM transactions
            WHERE user_id = ?
              AND type = 'expense'
              AND tx_date >= ?
              AND tx_date < ?
            GROUP BY month
            ORDER BY month DESC
            LIMIT 6
            """,
            (user_id, six_month_start_iso, next_month_iso),
        ).fetchall()

        monthly_savings_rows = conn.execute(
            """
            SELECT
                strftime('%Y-%m', tx_date) AS month,
                ROUND(SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END), 2) AS savings
            FROM transactions
            WHERE user_id = ?
              AND tx_date >= ?
              AND tx_date < ?
            GROUP BY month
            ORDER BY month DESC
            LIMIT 6
            """,
            (user_id, six_month_start_iso, next_month_iso),
        ).fetchall()

    category_totals = [dict(row) for row in category_rows]
    monthly_expenses = [dict(row) for row in reversed(monthly_expense_rows)]
    monthly_savings = [dict(row) for row in reversed(monthly_savings_rows)]

    return jsonify(
        {
            "categoryTotals": category_totals,
            "monthlyExpenses": monthly_expenses,
            "monthlySavings": monthly_savings,
        }
    )


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
