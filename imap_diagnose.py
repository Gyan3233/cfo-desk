"""
imap_diagnose.py — why is the mail scan fetching 0?

Tests the IMAP path end-to-end: connectivity, which client addresses the scan
targets, whether any recent mail exists, and whether replies from those clients
are found. Read-only (BODY.PEEK / readonly select).

Run from E:\\cfo-copilot:  .\\venv\\Scripts\\python.exe imap_diagnose.py
"""
import sys, imaplib, email
sys.path.insert(0, ".")
try:
    from dotenv import load_dotenv; load_dotenv(".env")
except Exception:
    pass
from datetime import date, timedelta
from email.utils import parseaddr
from services.imap_reader import imap_is_configured, imap_config, fetch_replies_from
from ai.ptp_intelligence import _get_known_client_emails

L = "-" * 66
def h(t): print("\n" + L + "\n" + t + "\n" + L)

h("1. CONFIG")
c = imap_config()
print(f"  imap_is_configured : {imap_is_configured()}")
print(f"  host/port/user     : {c['host']}:{c['port']}  as  {c['user']}")
print(f"  password present    : {bool(c['password'])} (len {len(c['password'])})")
if not imap_is_configured():
    print("\n  IMAP not configured — set SMTP_USERNAME/SMTP_PASSWORD in .env. Stop.")
    sys.exit(0)

h("2. CLIENTS THE SCAN TARGETS (people you've emailed)")
known = _get_known_client_emails()
print(f"  {len(known)} address(es):")
for e in known:
    print("   ", e)

since = (date.today() - timedelta(days=30)).strftime("%d-%b-%Y")
h("3. RAW IMAP CONNECTIVITY (INBOX)")
try:
    M = imaplib.IMAP4_SSL(c["host"], c["port"])
    M.login(c["user"], c["password"])
    M.select("INBOX", readonly=True)
    allc = len(M.search(None, "ALL")[1][0].split())
    recent = M.search(None, f'(SINCE {since})')[1][0].split()
    print(f"  Connected OK. INBOX total messages: {allc}")
    print(f"  Messages since {since}: {len(recent)}")
    print("\n  Last 8 senders in INBOX (to compare against the target list):")
    for num in recent[-8:][::-1]:
        raw = M.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")[1][0][1]
        msg = email.message_from_bytes(raw)
        frm = parseaddr(msg.get("From", ""))[1].lower()
        print(f"    from {frm:38s} | {str(msg.get('Subject',''))[:34]}")
except Exception as e:
    print(f"  RAW IMAP FAILED: {type(e).__name__}: {e}")
    print("  → If this failed: wrong App Password, or IMAP not enabled on the account")
    print("    (Gmail → Settings → Forwarding and POP/IMAP → Enable IMAP).")
    sys.exit(0)

h("4. PER-CLIENT SEARCH (raw) vs fetch_replies_from()")
for addr in known:
    raw_n = len(M.search(None, f'(FROM "{addr}" SINCE {since})')[1][0].split())
    got = fetch_replies_from([addr], days_back=30, max_results=50)
    print(f"  {addr:38s} raw match={raw_n:<3} fetched={len(got)}")
try: M.logout()
except Exception: pass

h("READING")
print("  If section 3 shows messages but section 4 raw match=0 for a client who")
print("  DID reply, the reply's From differs from the stored client email.")
print("  If raw match>0 but fetched=0, it's a parser bug (send me this output).")
print("  If nobody has replied yet, fetched=0 is simply correct.")
