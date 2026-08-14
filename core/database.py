"""
FILE: backend/database.py
PURPOSE: Everything related to the SQLite database.
         Creates tables, stores data, fetches data.
         Think of this as the "memory" of the entire system.

WHY SQLite?
  - It's just a single file on your laptop (invoices.db)
  - No need to install any database server
  - Works everywhere, zero configuration
"""

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "./invoices.db")


# ─────────────────────────────────────────────────────────────
# DATABASE SCHEMA  (the structure of all tables)
# ─────────────────────────────────────────────────────────────

SCHEMA = """

-- TABLE 1: clients
-- One row for every unique client (company or person)
-- We identify clients by their email address
CREATE TABLE IF NOT EXISTS clients (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    email      TEXT    UNIQUE,           -- must be unique per client
    created_at TEXT    DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- TABLE 2: invoices
-- One row per invoice found in emails
-- Links to clients table via client_id
CREATE TABLE IF NOT EXISTS invoices (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id        INTEGER NOT NULL REFERENCES clients(id),
    invoice_number   TEXT,
    invoice_date     TEXT,                -- format: YYYY-MM-DD
    due_date         TEXT,                -- format: YYYY-MM-DD
    amount           REAL,                -- base amount before tax
    gst_amount       REAL,                -- GST / tax portion
    total_amount     REAL,                -- final amount after tax
    currency         TEXT    DEFAULT 'INR',
    status           TEXT    DEFAULT 'unpaid'
                         CHECK(status IN ('unpaid','paid','overdue','partial')),
    description      TEXT,                -- what the invoice is for
    confidence       REAL,                -- how confident AI was (0.0 to 1.0)
    gmail_message_id TEXT    UNIQUE,      -- prevents scanning same email twice
    gmail_thread_id  TEXT,
    email_subject    TEXT,                -- original email subject line
    created_at       TEXT    DEFAULT (datetime('now', 'localtime')),
    updated_at       TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- TABLE 3: email_drafts
-- Stores AI-generated reminder emails BEFORE the user approves them
-- This is the "review queue" — user sees these in the dashboard
-- Status can be: pending (waiting for review), approved (user said yes),
--                rejected (user said no), sent (actually sent)
CREATE TABLE IF NOT EXISTS email_drafts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL REFERENCES invoices(id),
    client_id    INTEGER NOT NULL REFERENCES clients(id),
    to_email     TEXT    NOT NULL,
    cc_email     TEXT,
    subject      TEXT    NOT NULL,
    body         TEXT    NOT NULL,
    status       TEXT    DEFAULT 'pending'
                     CHECK(status IN ('pending','approved','rejected','sent','failed')),
    created_at   TEXT    DEFAULT (datetime('now', 'localtime')),
    reviewed_at  TEXT,                   -- when user clicked approve/reject
    sent_at      TEXT                    -- when email was actually sent
);

-- TABLE 4: push_subscriptions
-- Stores browser push notification tokens
-- When you open the dashboard and allow notifications,
-- the browser gives us a token we save here
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint   TEXT    NOT NULL UNIQUE,  -- browser's push endpoint URL
    p256dh     TEXT    NOT NULL,         -- encryption key
    auth       TEXT    NOT NULL,         -- auth secret
    created_at TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- TABLE 5: agent_runs
-- Audit log: every time the pipeline runs, we record what happened
CREATE TABLE IF NOT EXISTS agent_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at             TEXT    DEFAULT (datetime('now', 'localtime')),
    emails_scanned     INTEGER DEFAULT 0,
    invoices_found     INTEGER DEFAULT 0,
    invoices_stored    INTEGER DEFAULT 0,
    drafts_created     INTEGER DEFAULT 0,
    status             TEXT    DEFAULT 'success',
    error_message      TEXT,
    duration_seconds   REAL
);

-- INDEXES: make common queries fast
CREATE INDEX IF NOT EXISTS idx_invoices_due_date  ON invoices(due_date);
CREATE INDEX IF NOT EXISTS idx_invoices_status    ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_client    ON invoices(client_id);
CREATE INDEX IF NOT EXISTS idx_drafts_status      ON email_drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_invoice     ON email_drafts(invoice_id);
"""


# ─────────────────────────────────────────────────────────────
# CONNECTION HELPER
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# CONNECTION HELPER  (delegates to the unified SQLite/Postgres layer)
# ─────────────────────────────────────────────────────────────

from core.db import get_db, IS_PG   # noqa: E402  (unified backend)


def init_db(path: str = None):
    """Create all tables. On Postgres/Supabase the schema is created once via
    schema_supabase.sql, so here we only run the SQLite DDL for local dev."""
    if IS_PG:
        return
    with get_db(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    print(f"✅ Database ready at: {path or DB_PATH}")



# ─────────────────────────────────────────────────────────────
# CLIENT OPERATIONS
# ─────────────────────────────────────────────────────────────

def upsert_client(conn, name: str, email: Optional[str]) -> int:
    """
    Insert a new client OR update an existing one.
    'Upsert' = Update if exists, Insert if not.
    Returns the client's ID number.
    """
    if email:
        # Try to find by email first (most reliable identifier)
        row = conn.execute("SELECT id FROM clients WHERE email=?", (email,)).fetchone()
        if row:
            conn.execute(
                "UPDATE clients SET name=?, updated_at=datetime('now','localtime') WHERE id=?",
                (name, row["id"])
            )
            return row["id"]
    else:
        # No email — try to find by name
        row = conn.execute("SELECT id FROM clients WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]

    # Client not found — create new
    conn.execute("INSERT INTO clients (name, email) VALUES (?, ?)", (name, email))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ─────────────────────────────────────────────────────────────
# INVOICE OPERATIONS
# ─────────────────────────────────────────────────────────────

VALID_STATUSES = {"unpaid", "paid", "overdue", "partial"}


def normalize_status(raw_status) -> str:
    """
    Llama/Groq sometimes return status values outside our 4 allowed values
    (e.g. "due", "pending", "N/A", "Paid in full", None, etc.)
    This function maps anything the AI returns into one of our 4 valid values.
    If nothing matches, defaults to "unpaid" (safe default — better to remind
    than to silently skip a real invoice).
    """
    if not raw_status:
        return "unpaid"

    s = str(raw_status).strip().lower()

    if s in VALID_STATUSES:
        return s

    # Map common AI variations to our valid set
    if any(word in s for word in ["paid", "settled", "cleared", "complete"]):
        if "partial" in s or "part" in s:
            return "partial"
        if "not" in s or "un" in s:
            return "unpaid"
        return "paid"

    if any(word in s for word in ["overdue", "past due", "late"]):
        return "overdue"

    if any(word in s for word in ["partial", "part payment", "partly"]):
        return "partial"

    if any(word in s for word in ["due", "pending", "unpaid", "outstanding",
                                    "n/a", "none", "null", "unknown"]):
        return "unpaid"

    # Anything else unrecognised → safe default
    return "unpaid"


def store_invoice(conn, inv: dict) -> tuple:
    """
    Save an extracted invoice to the database.
    Returns (invoice_id, action) where action tells you what happened:
      - 'inserted'  → brand new invoice saved
      - 'updated'   → existing invoice status was refreshed
      - 'skipped'   → duplicate or too uncertain, ignored
    """
    # Always normalise status FIRST — fixes AI returning unexpected values
    inv["status"] = normalize_status(inv.get("status"))

    # Skip if AI wasn't confident enough
    if inv.get("confidence") is not None and inv["confidence"] < 0.40:
        return None, "skipped_low_confidence"

    # Skip if we already processed this exact email
    if inv.get("gmail_message_id"):
        existing = conn.execute(
            "SELECT id FROM invoices WHERE gmail_message_id=?",
            (inv["gmail_message_id"],)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE invoices SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                (inv["status"], existing["id"])
            )
            return existing["id"], "updated"

    # Get or create client
    client_id = upsert_client(conn, inv["client_name"], inv.get("client_email"))

    # Skip if same invoice number from same client already exists
    if inv.get("invoice_number"):
        existing = conn.execute(
            "SELECT id FROM invoices WHERE invoice_number=? AND client_id=?",
            (inv["invoice_number"], client_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE invoices SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                (inv["status"], existing["id"])
            )
            return existing["id"], "updated"

    # Insert new invoice
    conn.execute("""
        INSERT INTO invoices (
            client_id, invoice_number, invoice_date, due_date,
            amount, gst_amount, total_amount, currency,
            status, description, confidence,
            gmail_message_id, gmail_thread_id, email_subject
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        client_id,
        inv.get("invoice_number"),
        inv.get("invoice_date"),
        inv.get("due_date"),
        inv.get("amount"),
        inv.get("gst_amount"),
        inv.get("total_amount"),
        inv.get("currency", "INR"),
        inv["status"],
        inv.get("description"),
        inv.get("confidence"),
        inv.get("gmail_message_id"),
        inv.get("gmail_thread_id"),
        inv.get("email_subject"),
    ))
    invoice_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return invoice_id, "inserted"


def get_invoices_needing_reminder(conn, days: int = 7) -> list:
    """
    Find unpaid/partial/overdue invoices that need a reminder email.

    INCLUDES:
      - Invoices due within the next N days (upcoming)
      - Invoices already overdue (past due date) — these need reminders too!

    EXCLUDES:
      - Paid invoices
      - Invoices that already have a pending or approved draft
      - Invoices with no client email (can't send to them)

    Also handles Llama sometimes returning dates in wrong format.
    """
    future = (datetime.now().date() + timedelta(days=days)).isoformat()

    # Fetch all unpaid/partial/overdue invoices that have a due date
    # We fetch a broad set and filter in Python to handle date format issues
    rows = conn.execute("""
        SELECT
            i.*,
            c.name  AS client_name,
            c.email AS client_email,
            CAST(julianday(i.due_date) - julianday('now') AS INTEGER) AS days_until_due
        FROM invoices i
        JOIN clients c ON c.id = i.client_id
        WHERE i.status IN ('unpaid', 'partial', 'overdue')
          AND i.due_date IS NOT NULL
          AND c.email IS NOT NULL
          AND c.email != ''
          AND i.id NOT IN (
              SELECT invoice_id FROM email_drafts
              WHERE status IN ('pending', 'approved')
          )
        ORDER BY i.due_date ASC
    """).fetchall()

    today     = datetime.now().date()
    future_dt = today + timedelta(days=days)
    result    = []

    for row in rows:
        inv = dict(row)
        raw_due = inv.get("due_date", "")

        # Try to parse the due date — handle multiple formats Llama might return
        due_date = None
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%B %d, %Y", "%d %B %Y"):
            try:
                due_date = datetime.strptime(str(raw_due)[:20].strip(), fmt).date()
                break
            except ValueError:
                continue

        if due_date is None:
            # Can't parse date — include it anyway with a note (better to remind than miss)
            print(f"    ⚠️  Could not parse due_date '{raw_due}' for invoice #{inv.get('invoice_number')} — including in reminders")
            inv["days_until_due"] = 0
            result.append(inv)
            continue

        # Include if: overdue (past) OR due within the window (future)
        if due_date <= future_dt:
            inv["days_until_due"] = (due_date - today).days
            result.append(inv)

    return result


def get_all_invoices(conn) -> list:
    """Fetch all invoices for the dashboard table."""
    rows = conn.execute("""
        SELECT
            i.*,
            c.name  AS client_name,
            c.email AS client_email,
            CAST(julianday(i.due_date) - julianday('now') AS INTEGER) AS days_until_due
        FROM invoices i
        JOIN clients c ON c.id = i.client_id
        ORDER BY
            CASE i.status
                WHEN 'overdue'  THEN 1
                WHEN 'partial'  THEN 2
                WHEN 'unpaid'   THEN 3
                WHEN 'paid'     THEN 4
            END,
            i.due_date ASC NULLS LAST
    """).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────
# EMAIL DRAFT OPERATIONS
# ─────────────────────────────────────────────────────────────

def save_draft(conn, draft: dict) -> int:
    """Save a generated email draft for user review."""
    conn.execute("""
        INSERT INTO email_drafts
            (invoice_id, client_id, to_email, cc_email, subject, body, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
    """, (
        draft["invoice_id"],
        draft["client_id"],
        draft["to_email"],
        draft.get("cc_email"),
        draft["subject"],
        draft["body"],
    ))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_pending_drafts(conn) -> list:
    """Get all drafts waiting for user approval."""
    rows = conn.execute("""
        SELECT
            d.*,
            c.name  AS client_name,
            i.invoice_number,
            i.due_date,
            i.total_amount,
            i.amount,
            i.currency,
            CAST(julianday(i.due_date) - julianday('now') AS INTEGER) AS days_until_due
        FROM email_drafts d
        JOIN invoices i ON i.id = d.invoice_id
        JOIN clients  c ON c.id = d.client_id
        WHERE d.status = 'pending'
        ORDER BY i.due_date ASC
    """).fetchall()
    return [dict(r) for r in rows]


def approve_draft(conn, draft_id: int):
    """Mark a draft as approved (user said YES)."""
    conn.execute("""
        UPDATE email_drafts
        SET status='approved', reviewed_at=datetime('now','localtime')
        WHERE id=?
    """, (draft_id,))
    conn.commit()


def reject_draft(conn, draft_id: int):
    """Mark a draft as rejected (user said NO)."""
    conn.execute("""
        UPDATE email_drafts
        SET status='rejected', reviewed_at=datetime('now','localtime')
        WHERE id=?
    """, (draft_id,))
    conn.commit()


def mark_draft_sent(conn, draft_id: int):
    """Mark a draft as sent after the email is dispatched."""
    conn.execute("""
        UPDATE email_drafts
        SET status='sent', sent_at=datetime('now','localtime')
        WHERE id=?
    """, (draft_id,))
    conn.commit()


def mark_draft_failed(conn, draft_id: int):
    """Mark a draft as failed if sending broke."""
    conn.execute("""
        UPDATE email_drafts SET status='failed' WHERE id=?
    """, (draft_id,))
    conn.commit()


# ─────────────────────────────────────────────────────────────
# PUSH SUBSCRIPTION OPERATIONS
# ─────────────────────────────────────────────────────────────

def save_push_subscription(conn, endpoint: str, p256dh: str, auth: str):
    """Save browser push notification credentials."""
    conn.execute("""
        INSERT INTO push_subscriptions (endpoint, p256dh, auth)
        VALUES (?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth
    """, (endpoint, p256dh, auth))
    conn.commit()


def get_all_push_subscriptions(conn) -> list:
    """Get all registered push subscribers."""
    rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()
    return [dict(r) for r in rows]


def delete_push_subscription(conn, endpoint: str):
    """Remove a push subscription (when browser unsubscribes)."""
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
    conn.commit()


# ─────────────────────────────────────────────────────────────
# SUMMARY & STATS
# ─────────────────────────────────────────────────────────────

def get_summary(conn) -> dict:
    """Get counts and totals for the dashboard header cards."""
    today = datetime.now().date()
    soon  = (today + timedelta(days=7)).isoformat()
    today = today.isoformat()

    def q(sql, *args):
        return conn.execute(sql, args).fetchone()[0]

    return {
        "total":              q("SELECT COUNT(*) FROM invoices"),
        "unpaid":             q("SELECT COUNT(*) FROM invoices WHERE status IN ('unpaid','partial')"),
        "paid":               q("SELECT COUNT(*) FROM invoices WHERE status='paid'"),
        "overdue":            q("SELECT COUNT(*) FROM invoices WHERE status IN ('unpaid','partial','overdue') AND date(due_date) < date('now')"),
        "due_soon":           q("SELECT COUNT(*) FROM invoices WHERE status IN ('unpaid','partial') AND date(due_date) BETWEEN date(?) AND date(?)", today, soon),
        "pending_drafts":     q("SELECT COUNT(*) FROM email_drafts WHERE status='pending'"),
        "total_outstanding":  q("SELECT COALESCE(SUM(COALESCE(total_amount,amount)),0) FROM invoices WHERE status IN ('unpaid','partial','overdue')"),
        "clients_count":      q("SELECT COUNT(*) FROM clients"),
        "reminders_sent":     q("SELECT COUNT(*) FROM email_drafts WHERE status='sent'"),
    }


# ─────────────────────────────────────────────────────────────
# AGENT RUN LOGGING
# ─────────────────────────────────────────────────────────────

def log_run(conn, data: dict):
    """Save a record of this pipeline run to the audit log."""
    conn.execute("""
        INSERT INTO agent_runs
            (emails_scanned, invoices_found, invoices_stored,
             drafts_created, status, error_message, duration_seconds)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("emails_scanned", 0),
        data.get("invoices_found", 0),
        data.get("invoices_stored", 0),
        data.get("drafts_created", 0),
        data.get("status", "success"),
        data.get("error_message"),
        data.get("duration_seconds"),
    ))
    conn.commit()
