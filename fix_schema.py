import sqlite3
c = sqlite3.connect("invoices.db")
stmts = [
    "ALTER TABLE invoices ADD COLUMN original_due_date TEXT",
    "ALTER TABLE invoices ADD COLUMN latest_due_date TEXT",
    "ALTER TABLE invoices ADD COLUMN extension_count INTEGER DEFAULT 0",
    "ALTER TABLE invoices ADD COLUMN expected_payment_date TEXT",
    "ALTER TABLE invoices ADD COLUMN reminder_date TEXT",
    "ALTER TABLE email_drafts ADD COLUMN template_used TEXT",
    "ALTER TABLE email_drafts ADD COLUMN scheduled_send_date TEXT",
    "ALTER TABLE email_drafts ADD COLUMN reviewed_by TEXT",
    "ALTER TABLE email_drafts ADD COLUMN reviewed_at TEXT",
]
for s in stmts:
    try:
        c.execute(s); print("added:", s.split("ADD COLUMN")[1].strip())
    except sqlite3.OperationalError as e:
        print("skip :", e)
c.commit()
print("\ninvoices columns now:", [r[1] for r in c.execute("PRAGMA table_info(invoices)")])
