"""
set_reminders.py — stamp reminder dates and generate reminder drafts.

Runs the two steps that were never applied on this invoices.db:
  1. ensure_reminder_dates()      -> due_date - REMINDER_BEFORE_DUE_DAYS
  2. generate_upcoming_drafts()   -> creates drafts you review before sending

It does NOT send anything. Drafts appear in the Notification Center for review;
they auto-send only when their scheduled date arrives.

Run from E:\\cfo-copilot:  .\\venv\\Scripts\\python.exe set_reminders.py
"""
import sys
sys.path.insert(0, ".")
try:
    from dotenv import load_dotenv; load_dotenv(".env")
except Exception:
    pass

from app.tabs.notification_center import ensure_reminder_dates, generate_upcoming_drafts
from core.database import DB_PATH, get_db

n = ensure_reminder_dates()
d = generate_upcoming_drafts()
print(f"Reminder dates stamped : {n}")
print(f"Drafts generated       : {d}   (awaiting review — nothing sent)")

with get_db(DB_PATH) as c:
    print("\nDrafts are addressed to these inboxes — CONFIRM they are test/")
    print("controlled addresses before the send date, or the auto-send will")
    print("email real clients tomorrow:")
    rows = c.execute(
        "SELECT to_email, COUNT(*) n FROM email_drafts "
        "WHERE status IN ('pending','approved') "
        "GROUP BY to_email ORDER BY n DESC"
    ).fetchall()
    for r in rows:
        print(f"    {str(r['to_email']):42s} {r['n']} draft(s)")
    total = c.execute("SELECT COUNT(*) FROM email_drafts").fetchone()[0]
    earliest = c.execute(
        "SELECT MIN(scheduled_send_date) FROM email_drafts "
        "WHERE status IN ('pending','approved')"
    ).fetchone()[0]
    print(f"\nTotal drafts now: {total} | earliest scheduled send date: {earliest}")

print("\nNext: open the Notification Center to review them. Before the send date,")
print("send ONE to yourself as a test to confirm SMTP delivery.")
