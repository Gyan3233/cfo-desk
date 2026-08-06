"""
imap_reader.py — read client replies via Gmail IMAP + App Password.

The read half of Option B. Uses the SAME App Password as smtp_sender. Fetches
messages FROM known client addresses (sender-based matching, exactly what the
PTP reply-scan already does) and returns dicts in the shape ingest_reply
expects: {id, gmail_message_id, from, subject, body, date, thread_id}.

.env (reuses the SMTP creds):
    IMAP_HOST=imap.gmail.com      # optional; this is the default
    IMAP_PORT=993                 # optional
    SMTP_USERNAME=cfodesk2@gmail.com
    SMTP_PASSWORD=<Gmail App Password>

Enable IMAP once on the account: Gmail → Settings → Forwarding and POP/IMAP →
Enable IMAP.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr


def imap_config() -> dict:
    # The MONITORED mailbox is the account these IMAP credentials open — the
    # configured shared/finance mailbox (e.g. cfodesk2@…), NOT whoever is logged
    # into the app. Any authorised consultant can run the scan; it always reads
    # this mailbox. For a delegated corporate shared mailbox, point these
    # credentials at the account that has access to it.
    return {
        "host":     os.getenv("IMAP_HOST", "imap.gmail.com"),
        "port":     int(os.getenv("IMAP_PORT", "993")),
        "user":     (os.getenv("SMTP_USERNAME", "") or "").strip(),
        "password": (os.getenv("SMTP_PASSWORD", "") or "").replace(" ", ""),
    }


def imap_is_configured() -> bool:
    c = imap_config()
    return bool(c["user"] and c["password"])


def _dec(s) -> str:
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return str(s)


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def _body(msg) -> str:
    """Prefer text/plain; fall back to stripped HTML."""
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition") or "")
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return _strip_html(part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace"))
                except Exception:
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True)
        text = payload.decode(msg.get_content_charset() or "utf-8", "replace")
        return _strip_html(text) if msg.get_content_type() == "text/html" else text
    except Exception:
        return ""


def fetch_replies_from(client_emails, days_back: int = 30,
                       max_results: int = 100) -> list[dict]:
    """Return client replies (newest first) from the given addresses within
    `days_back` days. Read-only (BODY.PEEK, so nothing is marked as read)."""
    c = imap_config()
    if not c["user"] or not c["password"]:
        raise RuntimeError(
            "IMAP not configured — set SMTP_USERNAME and SMTP_PASSWORD "
            "(Gmail App Password) in .env, and enable IMAP on the account."
        )
    if not client_emails:
        return []

    since = (date.today() - timedelta(days=days_back)).strftime("%d-%b-%Y")
    out: list[dict] = []
    seen: set[str] = set()

    M = imaplib.IMAP4_SSL(c["host"], c["port"])
    try:
        M.login(c["user"], c["password"])
        M.select("INBOX", readonly=True)
        for addr in client_emails:
            if len(out) >= max_results:
                break
            try:
                typ, data = M.search(None, f'(FROM "{addr}" SINCE {since})')
            except Exception:
                continue
            if typ != "OK" or not data or not data[0]:
                continue
            for num in reversed(data[0].split()):        # newest first
                if len(out) >= max_results:
                    break
                try:
                    typ, md = M.fetch(num, "(BODY.PEEK[])")
                except Exception:
                    continue
                if typ != "OK" or not md or not md[0]:
                    continue
                msg = email.message_from_bytes(md[0][1])
                mid = (msg.get("Message-ID") or "").strip()
                if mid and mid in seen:
                    continue
                if mid:
                    seen.add(mid)
                out.append({
                    "id":               mid or None,
                    "gmail_message_id": mid or None,
                    "from":             parseaddr(msg.get("From", ""))[1].lower(),
                    "subject":          _dec(msg.get("Subject")),
                    "body":             _body(msg),
                    "date":             (msg.get("Date") or "").strip(),
                    "thread_id":        None,
                })
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return out
