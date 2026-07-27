"""
FILE: backend/pipeline.py
PURPOSE: The main brain of the Invoice Agent.
         Orchestrates everything in sequence:
           1. Load emails (from Gmail or sample files)
           2. Extract invoice data using Claude AI
           3. Save invoices to SQLite database
           4. Find invoices that need reminders
           5. Generate email drafts using Claude AI
           6. Save drafts (NOT send them yet)
           7. Send push notification to alert you
           8. Log everything to audit trail

MODES:
  test  → reads from emails/sample_emails.json (safe, no Gmail needed)
  live  → reads from your real Gmail inbox

USAGE:
  python pipeline.py --mode test        # test with sample emails
  python pipeline.py --mode live        # real Gmail scan
  python pipeline.py --mode test --verbose  # show more detail
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
# --- ADD THIS LINE TO FIX THE WINDOWS ENCODING ERROR ---
sys.stdout.reconfigure(encoding='utf-8')

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, env vars must be set manually

# Import our own modules
sys.path.insert(0, str(Path(__file__).parent))
from core.database import (
    init_db, get_db, store_invoice, get_invoices_needing_reminder,
    save_draft, get_summary, log_run, DB_PATH
)
from agent import extract_invoices_from_email, generate_reminder_email

# v3: push notifications are OPTIONAL. notifications.py has known broken
# imports (handbook issue #9) and needs VAPID keys in .env — if either is
# missing, the pipeline must still run, so we degrade to a no-op.
try:
    from notifications import notify_all_subscribers
    NOTIFICATIONS_OK = True
except Exception as _notif_err:
    NOTIFICATIONS_OK = False
    def notify_all_subscribers(conn, title, body, url, _err=str(_notif_err)):
        print(f"  (push notifications disabled: {_err})")

CC_EMAIL      = os.getenv("CC_EMAIL", "")   # comma-separated, e.g. "a@b.com, c@d.com"
BCC_EMAIL     = os.getenv("BCC_EMAIL", "")  # comma-separated, hidden recipients
DUE_SOON_DAYS = int(os.getenv("DUE_SOON_DAYS", "7"))

# v3: how reminder drafts are worded.
#   template (default) -> per-client templates from templates/*.json
#                         (template_manager.render_for_client)
#   llm                -> original behaviour: agent.generate_reminder_email
DRAFT_ENGINE = os.getenv("DRAFT_ENGINE", "template").lower()
from core.template_manager import render_for_client



def divider(title: str, char="─"):
    width = 60
    print(f"\n{char * width}")
    print(f"  {title}")
    print(f"{char * width}")


def run_pipeline(mode: str = "test", verbose: bool = False, auto_send: bool = False):
    """
    Run the full invoice scanning pipeline.
    This is the function called by the scheduler every day at 9AM.
    """
    start_time = time.time()
    run_stats = {
        "emails_scanned": 0, "invoices_found": 0,
        "invoices_stored": 0, "drafts_created": 0,
        "status": "success"
    }

    divider(f"Invoice Agent Pipeline — {mode.upper()} mode", "═")
    print(f"  Started: {datetime.now().strftime('%d %b %Y at %I:%M %p')}")

    # ── STEP 0: Initialize database ──────────────────────────────────────────
    init_db(DB_PATH)

    # ── STEP 1: Load emails ──────────────────────────────────────────────────
    divider("STEP 1 — Loading Emails")

    if mode == "test":
        emails = _load_sample_emails()
        gmail_service = None
    else:
        emails, gmail_service = _load_gmail_emails()

    run_stats["emails_scanned"] = len(emails)
    print(f"\n  📬 {len(emails)} emails loaded for scanning")

    if not emails:
        print("  Nothing to do. Exiting.")
        return

    # ── STEP 2: Extract invoice data with Claude AI ──────────────────────────
    divider("STEP 2 — Extracting Invoice Data with Claude AI")

    all_invoices = []

    for i, email in enumerate(emails, 1):
        print(f"\n  [{i:02d}/{len(emails):02d}] {email['subject'][:65]}")
        if verbose:
            print(f"          From: {email.get('from_name','')} <{email.get('from','')}>")

        extracted = extract_invoices_from_email(email)

        if extracted:
            print(f"         ✅ Found {len(extracted)} invoice(s):")
            for inv in extracted:
                amt = inv.get("total_amount") or inv.get("amount") or "?"
                print(f"            • #{inv.get('invoice_number','N/A')} | "
                      f"{inv.get('client_name','?')} | "
                      f"{inv.get('currency','INR')} {amt} | "
                      f"Due: {inv.get('due_date','?')} | "
                      f"Status: {inv.get('status','?')} | "
                      f"Confidence: {inv.get('confidence',0):.0%}")
            all_invoices.extend(extracted)
        else:
            print(f"         ℹ️  No invoice data found in this email")

    run_stats["invoices_found"] = len(all_invoices)
    print(f"\n  Total invoices extracted: {len(all_invoices)}")

    if not all_invoices:
        print("\n  No invoices to store. Pipeline complete.")
        return

    # ── STEP 3: Save to SQLite database ─────────────────────────────────────
    divider("STEP 3 — Saving to Database")

    inserted = updated = skipped = 0

    with get_db(DB_PATH) as conn:
        for inv in all_invoices:
            inv_id, action = store_invoice(conn, inv)
            if action == "inserted":
                inserted += 1
                print(f"  ✅ NEW:  {inv.get('invoice_number','N/A')} — {inv.get('client_name')}")
            elif action == "updated":
                updated += 1
                print(f"  🔄 UPD:  {inv.get('invoice_number','N/A')} — {inv.get('client_name')} (status updated)")
            else:
                skipped += 1
                print(f"  ⏭️  SKIP: {inv.get('invoice_number','N/A')} — {action}")
        conn.commit()

    run_stats["invoices_stored"] = inserted
    print(f"\n  Inserted: {inserted} | Updated: {updated} | Skipped: {skipped}")

    # ── STEP 4: Find invoices needing reminders ──────────────────────────────
    divider(f"STEP 4 — Finding Invoices Due in {DUE_SOON_DAYS} Days")

    with get_db(DB_PATH) as conn:
        needs_reminder = get_invoices_needing_reminder(conn, DUE_SOON_DAYS)

    if not needs_reminder:
        print(f"\n  ✅ No invoices due in the next {DUE_SOON_DAYS} days (or all have drafts already)")
    else:
        print(f"\n  ⚠️  {len(needs_reminder)} invoice(s) need reminder drafts:\n")
        for inv in needs_reminder:
            days = inv.get("days_until_due", 0)
            flag = f"🔴 OVERDUE {abs(days)}d" if days < 0 else f"⏰ Due in {days}d"
            amt  = inv.get("total_amount") or inv.get("amount") or 0
            print(f"     {flag:20s} | {inv['client_name']:30s} | {inv.get('currency','INR')} {amt:>10,.0f} | #{inv.get('invoice_number','N/A')}")

    # ── STEP 5: Generate reminder email drafts ───────────────────────────────
    divider("STEP 5 — Generating Email Drafts (Not Sending Yet)")

    drafts_created = 0

    with get_db(DB_PATH) as conn:
        for inv in needs_reminder:
            client_email = inv.get("client_email")
            print(f"\n  Generating draft for: {inv['client_name']}")

            if not client_email:
                print(f"  ⚠️  No email address on file — skipping draft")
                continue

            if DRAFT_ENGINE == "llm":
                draft_content = generate_reminder_email(inv)
            else:
                # v3 default: per-client template (deterministic, auditable)
                draft_content = render_for_client({
                    "client_name":    inv.get("client_name", ""),
                    "client_email":   client_email,
                    "invoice_number": inv.get("invoice_number", ""),
                    "amount":         inv.get("total_amount") or inv.get("amount") or 0,
                    "currency":       inv.get("currency", "INR"),
                    "issue_date":     inv.get("invoice_date", ""),
                    "due_date":       inv.get("due_date", ""),
                })
                print(f"  ✉️   Template used: {draft_content['template_name']}")

            if not draft_content:
                print(f"  ❌  Could not generate draft ({DRAFT_ENGINE} engine)")
                continue

            # Save draft to database — NOT sending yet
            draft_id = save_draft(conn, {
                "invoice_id": inv["id"],
                "client_id":  inv["client_id"],
                "to_email":   client_email,
                "cc_email":   CC_EMAIL,   # all CC addresses from .env
                "subject":    draft_content["subject"],
                "body":       draft_content["body"],
            })

            drafts_created += 1
            
            if auto_send:
                print(f"  🚀  Auto-Sending Email #{draft_id} to {client_email}...")
                from services.gmail_client import get_gmail_service, send_email
                from core.database import mark_draft_sent
                
                gmail_svc = get_gmail_service()
                success = send_email(
                    gmail_svc,
                    to=client_email,
                    subject=draft_content["subject"],
                    body=draft_content["body"],
                    cc=CC_EMAIL,
                    bcc=BCC_EMAIL
                )
                
                if success:
                    mark_draft_sent(conn, draft_id)
                    print(f"  ✅  Email Sent Successfully!")
                else:
                    print(f"  ❌  Failed to send email.")
            else:
                print(f"  📋  Draft #{draft_id} saved → Waiting for your approval")
                print(f"      To      : {client_email}")
                print(f"      CC      : {CC_EMAIL or 'none'}")
                print(f"      BCC     : {BCC_EMAIL or 'none'}")

        conn.commit()

    run_stats["drafts_created"] = drafts_created

    # ── STEP 6: Send push notification ──────────────────────────────────────
    if drafts_created > 0:
        divider("STEP 6 — Sending Push Notification")
        if not (NOTIFICATIONS_OK and os.getenv("VAPID_PRIVATE_KEY")):
            print("  ⏭  Skipped — push not configured (see .env PUSH NOTIFICATIONS section).")
        else:
            pass  # fall through to notify below

        if NOTIFICATIONS_OK and os.getenv("VAPID_PRIVATE_KEY"):
            with get_db(DB_PATH) as conn:
                notif_title = f"📋 {drafts_created} Payment Reminder Draft(s) Ready"
                notif_body  = "Open your Invoice Agent dashboard to review and approve."
                notify_all_subscribers(conn, notif_title, notif_body, "/dashboard")

    # ── STEP 7: Mark Gmail emails as processed ───────────────────────────────
    if mode == "live" and gmail_service:
        divider("STEP 7 — Marking Emails as Processed")
        from services.gmail_client import mark_email_processed
        for email in emails:
            mark_email_processed(gmail_service, email["id"])
        print(f"  ✅ {len(emails)} emails labeled 'InvoiceAgent/Processed'")

    # ── STEP 8: Final summary ────────────────────────────────────────────────
    duration = round(time.time() - start_time, 1)
    run_stats["duration_seconds"] = duration

    with get_db(DB_PATH) as conn:
        log_run(conn, run_stats)
        summary = get_summary(conn)

    divider("Pipeline Complete", "═")
    print(f"""
  ⏱️  Duration:           {duration} seconds
  📧  Emails scanned:    {run_stats['emails_scanned']}
  📄  Invoices found:    {run_stats['invoices_found']}
  💾  New in database:   {run_stats['invoices_stored']}
  📋  Drafts created:    {run_stats['drafts_created']}

  DATABASE OVERVIEW:
  {'─' * 40}
  Total invoices:        {summary['total']}
  Unpaid:                {summary['unpaid']}
  Overdue:               {summary['overdue']}
  Due in 7 days:         {summary['due_soon']}
  Paid:                  {summary['paid']}
  Pending draft review:  {summary['pending_drafts']}  ← Go review these!
  Outstanding amount:    ₹ {summary['total_outstanding']:,.0f}
  Unique clients:        {summary['clients_count']}
  {'─' * 40}

  📊 Dashboard: http://localhost:8000/dashboard
  🗄️  Database:  {DB_PATH}
""")


def _load_sample_emails() -> list:
    """Load test emails from the JSON file."""
    path = Path(__file__).parent.parent / "emails" / "sample_emails.json"
    if not path.exists():
        print(f"❌ Sample emails not found at {path}")
        sys.exit(1)
    with open(path) as f:
        emails = json.load(f)
    print(f"  📂 Loaded {len(emails)} sample emails from {path}")
    return emails


def _load_gmail_emails() -> tuple:
    """Connect to Gmail and fetch invoice emails."""
    try:
        from services.gmail_client import get_gmail_service, fetch_invoice_emails
        print("  🔐 Connecting to Gmail...")
        service = get_gmail_service()
        print("  ✅ Connected to Gmail")
        emails = fetch_invoice_emails(service, max_results=50)
        return emails, service
    except FileNotFoundError as e:
        print(str(e))
        sys.exit(1)
    except Exception as e:
        print(f"❌ Gmail connection failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Invoice Agent Pipeline")
    parser.add_argument(
        "--mode", choices=["test", "live"], default="test",
        help="'test' uses sample emails; 'live' reads real Gmail"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show more detail during processing"
    )
    parser.add_argument(
        "--auto-send", action="store_true",
        help="Automatically send generated reminder emails"
    )
    args = parser.parse_args()

    # Pass the new auto_send argument into the function
    run_pipeline(mode=args.mode, verbose=args.verbose, auto_send=args.auto_send)
