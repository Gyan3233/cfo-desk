"""
mailing_diagnose.py — why is the Notification Center empty?

Inspects invoices.db (the reminder store, separate from the dashboard
workbook) to see whether any invoice is eligible to become a reminder draft.

Run from E:\\cfo-copilot:  .\\venv\\Scripts\\python.exe mailing_diagnose.py
Read-only.
"""
import sys, os
sys.path.insert(0, ".")
try:
    from dotenv import load_dotenv; load_dotenv(".env")
except Exception:
    pass

from datetime import date, timedelta
from core.database import DB_PATH, get_db

NOTIFY_LEAD_DAYS = int(os.getenv("NOTIFY_LEAD_DAYS", "5"))
REMINDABLE = ("unpaid", "overdue", "partial", "promised")
horizon = (date.today() + timedelta(days=NOTIFY_LEAD_DAYS)).isoformat()

print("invoices.db:", os.path.abspath(DB_PATH))
print("Today:", date.today(), "| reminder horizon (today +", NOTIFY_LEAD_DAYS, "d):", horizon)

def one(conn, sql, args=()):
    r = conn.execute(sql, args).fetchone()
    return r[0] if r else 0

with get_db(DB_PATH) as c:
    print("\n--- CLIENTS ---")
    print("  total clients      :", one(c, "SELECT COUNT(*) FROM clients"))
    print("  clients with email :", one(c, "SELECT COUNT(*) FROM clients WHERE email IS NOT NULL AND email != ''"))

    print("\n--- INVOICES ---")
    print("  total invoices     :", one(c, "SELECT COUNT(*) FROM invoices"))
    print("  by status:")
    for row in c.execute("SELECT status, COUNT(*) n FROM invoices GROUP BY status"):
        print(f"      {str(row['status']):12s} {row['n']}")
    print("  with reminder_date set        :",
          one(c, "SELECT COUNT(*) FROM invoices WHERE reminder_date IS NOT NULL"))
    print("  reminder_date <= horizon      :",
          one(c, "SELECT COUNT(*) FROM invoices WHERE reminder_date IS NOT NULL AND reminder_date <= ?", (horizon,)))
    qs = ",".join("?" * len(REMINDABLE))
    print(f"  status in {REMINDABLE}:",
          one(c, f"SELECT COUNT(*) FROM invoices WHERE status IN ({qs})", REMINDABLE))

    print("\n--- ELIGIBLE FOR A DRAFT (the exact generate_upcoming_drafts filter) ---")
    eligible = one(c, f"""
        SELECT COUNT(*)
        FROM invoices i JOIN clients c ON c.id = i.client_id
        WHERE i.reminder_date IS NOT NULL AND i.reminder_date <= ?
          AND i.status IN ({qs})
          AND c.email IS NOT NULL AND c.email != ''
    """, (horizon, *REMINDABLE))
    print("  invoices that WOULD create a draft right now:", eligible)

    print("\n--- EMAIL DRAFTS ---")
    print("  total drafts       :", one(c, "SELECT COUNT(*) FROM email_drafts"))
    for row in c.execute("SELECT status, COUNT(*) n FROM email_drafts GROUP BY status"):
        print(f"      {str(row['status']):12s} {row['n']}")

print("\n--- READING ---")
print("  If 'total invoices' is 0 or 'eligible' is 0, the reminder store has")
print("  nothing to send. The fix is to populate invoices.db from your data")
print("  (workbook open invoices + Contact emails, or the CSV import path).")
print("  Paste this output back.")
