-- CFO Desk — PostgreSQL schema for Supabase
-- Run this ONCE in the Supabase SQL editor (SQL Editor → New query → paste → Run).
-- It is idempotent (IF NOT EXISTS), so re-running is safe.
-- Dates are stored as TEXT (ISO strings) to match the app's SQLite behaviour.

-- ── Operational: clients / invoices / drafts ─────────────────────────────
CREATE TABLE IF NOT EXISTS clients (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    email      TEXT UNIQUE,
    created_at TEXT DEFAULT (now()::text),
    updated_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS invoices (
    id                    BIGSERIAL PRIMARY KEY,
    client_id             BIGINT NOT NULL REFERENCES clients(id),
    invoice_number        TEXT,
    invoice_date          TEXT,
    due_date              TEXT,
    amount                REAL,
    gst_amount            REAL,
    total_amount          REAL,
    currency              TEXT DEFAULT 'INR',
    status                TEXT DEFAULT 'unpaid'
                              CHECK (status IN ('unpaid','paid','overdue','partial','promised')),
    description           TEXT,
    confidence            REAL,
    gmail_message_id      TEXT UNIQUE,
    gmail_thread_id       TEXT,
    email_subject         TEXT,
    reminder_date         TEXT,
    original_due_date     TEXT,
    latest_due_date       TEXT,
    expected_payment_date TEXT,
    extension_count       INTEGER DEFAULT 0,
    created_at            TEXT DEFAULT (now()::text),
    updated_at            TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS email_drafts (
    id                  BIGSERIAL PRIMARY KEY,
    invoice_id          BIGINT NOT NULL REFERENCES invoices(id),
    client_id           BIGINT NOT NULL REFERENCES clients(id),
    to_email            TEXT NOT NULL,
    cc_email            TEXT,
    subject             TEXT NOT NULL,
    body                TEXT NOT NULL,
    status              TEXT DEFAULT 'pending'
                            CHECK (status IN ('pending','approved','rejected','sent','failed')),
    scheduled_send_date TEXT,
    template_used       TEXT,
    reviewed_at         TEXT,
    reviewed_by         TEXT,
    sent_at             TEXT,
    created_at          TEXT DEFAULT (now()::text)
);

-- ── Reply / PTP intelligence ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS client_replies (
    id               BIGSERIAL PRIMARY KEY,
    gmail_message_id TEXT UNIQUE,
    client_id        BIGINT REFERENCES clients(id),
    invoice_id       BIGINT REFERENCES invoices(id),
    thread_id        TEXT,
    subject          TEXT,
    body             TEXT,
    received_at      TEXT,
    ai_category      TEXT,
    ai_summary       TEXT,
    ai_promised_date TEXT,
    ai_confidence    REAL,
    is_ptp           INTEGER DEFAULT 0,
    created_at       TEXT DEFAULT (now()::text)
);
CREATE INDEX IF NOT EXISTS idx_replies_client  ON client_replies (client_id, received_at);
CREATE INDEX IF NOT EXISTS idx_replies_invoice ON client_replies (invoice_id, received_at);

CREATE TABLE IF NOT EXISTS ptp_events (
    id            BIGSERIAL PRIMARY KEY,
    invoice_id    BIGINT NOT NULL REFERENCES invoices(id),
    client_id     BIGINT NOT NULL REFERENCES clients(id),
    reply_id      BIGINT REFERENCES client_replies(id),
    promised_date TEXT NOT NULL,
    previous_due  TEXT NOT NULL,
    days_extended INTEGER NOT NULL,
    created_at    TEXT DEFAULT (now()::text)
);
CREATE INDEX IF NOT EXISTS idx_ptp_invoice ON ptp_events (invoice_id);

CREATE TABLE IF NOT EXISTS scan_history (
    id        BIGSERIAL PRIMARY KEY,
    ran_at    TEXT DEFAULT (now()::text),
    fetched   INTEGER,
    processed INTEGER,
    ptps      INTEGER,
    targeted  INTEGER,
    days_back INTEGER,
    error     TEXT
);

-- ── Misc operational ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         BIGSERIAL PRIMARY KEY,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id               BIGSERIAL PRIMARY KEY,
    run_at           TEXT DEFAULT (now()::text),
    emails_scanned   INTEGER DEFAULT 0,
    invoices_found   INTEGER DEFAULT 0,
    invoices_stored  INTEGER DEFAULT 0,
    drafts_created   INTEGER DEFAULT 0,
    status           TEXT DEFAULT 'success',
    error_message    TEXT,
    duration_seconds REAL
);

CREATE INDEX IF NOT EXISTS idx_invoices_due_date ON invoices(due_date);
CREATE INDEX IF NOT EXISTS idx_invoices_status   ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_client   ON invoices(client_id);
CREATE INDEX IF NOT EXISTS idx_drafts_status     ON email_drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_invoice    ON email_drafts(invoice_id);

-- ── Authentication (auth.db equivalent) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS allowlist (
    email    TEXT PRIMARY KEY,
    role     TEXT NOT NULL DEFAULT 'member',
    added_by TEXT,
    added_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    email         TEXT PRIMARY KEY,
    full_name     TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member',
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT,
    last_login    TEXT
);

CREATE TABLE IF NOT EXISTS login_attempts (
    email   TEXT,
    ts      DOUBLE PRECISION,
    success INTEGER
);

CREATE TABLE IF NOT EXISTS password_resets (
    email      TEXT PRIMARY KEY,
    code_hash  TEXT NOT NULL,
    salt       TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    issued_by  TEXT
);
