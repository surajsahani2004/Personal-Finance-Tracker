from __future__ import annotations

import os
import sqlite3
from datetime import date
from functools import wraps

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "finance.db")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-me")

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


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
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
            """
        )


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
    month_key = date.today().strftime("%Y-%m")

    with get_db() as conn:
        user = conn.execute(
            "SELECT full_name, monthly_budget FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

        expense_total = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE user_id = ?
              AND type = 'expense'
              AND strftime('%Y-%m', tx_date) = ?
            """,
            (user_id, month_key),
        ).fetchone()[0]

        income_total = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0)
            FROM transactions
            WHERE user_id = ?
              AND type = 'income'
              AND strftime('%Y-%m', tx_date) = ?
            """,
            (user_id, month_key),
        ).fetchone()[0]

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
    expense_total = float(expense_total)
    income_total = float(income_total)

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


@app.get("/api/reports")
@login_required
def api_reports():
    user_id = current_user_id()

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
            WHERE user_id = ? AND type = 'expense'
            GROUP BY month
            ORDER BY month DESC
            LIMIT 6
            """,
            (user_id,),
        ).fetchall()

        monthly_savings_rows = conn.execute(
            """
            SELECT
                strftime('%Y-%m', tx_date) AS month,
                ROUND(SUM(CASE WHEN type = 'income' THEN amount ELSE -amount END), 2) AS savings
            FROM transactions
            WHERE user_id = ?
            GROUP BY month
            ORDER BY month DESC
            LIMIT 6
            """,
            (user_id,),
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
    app.run(debug=True)
