"""
smtp_sender.py — send reminder emails via Gmail SMTP + App Password.

Part of the App-Password email path (Option B): one credential for both
sending (this file, SMTP) and reading replies (imap_reader.py, IMAP). No
Google Cloud / OAuth. All the template, review, and PTP logic is unchanged —
only the transport under "send" lives here.

.env:
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USERNAME=cfodesk2@gmail.com
    SMTP_PASSWORD=<16-char Gmail App Password, no spaces>
    SENDER_NAME=Accounts Team — Infrabeat     # already present; display name
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def smtp_config() -> dict:
    return {
        "host":      os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "port":      int(os.getenv("SMTP_PORT", "587")),
        # Auth identity — the credentials used to log into the mail server.
        "user":      (os.getenv("SMTP_USERNAME", "") or "").strip(),
        "password":  (os.getenv("SMTP_PASSWORD", "") or "").replace(" ", ""),
        # The shared/monitored mailbox shown to clients as the sender. Kept
        # separate from the auth identity so a consultant can authenticate with
        # their own account and send AS a shared mailbox (e.g. M365 "Send As").
        # Defaults to the auth user (the Gmail App-Password case, where the
        # mailbox authenticates as itself).
        "mailbox":   (os.getenv("MAILBOX_ADDRESS", "") or
                      os.getenv("SMTP_USERNAME", "") or "").strip(),
        "from_name": os.getenv("SENDER_NAME", "Accounts"),
    }


def smtp_is_configured() -> bool:
    c = smtp_config()
    return bool(c["user"] and c["password"])


def _addrs(s) -> list[str]:
    if not s:
        return []
    return [a.strip() for a in str(s).split(",") if a.strip()]


def send_email_smtp(to, subject: str, body: str, cc=None, bcc=None) -> bool:
    """Send one email via Gmail SMTP. Returns True on success.
    Raises RuntimeError only if SMTP isn't configured."""
    c = smtp_config()
    if not c["user"] or not c["password"]:
        raise RuntimeError(
            "SMTP not configured — set SMTP_USERNAME and SMTP_PASSWORD in .env "
            "(use a Gmail App Password, not the account password)."
        )

    to_list, cc_list, bcc_list = _addrs(to), _addrs(cc), _addrs(bcc)
    if not to_list:
        return False

    msg = MIMEMultipart()
    msg["From"] = f'{c["from_name"]} <{c["mailbox"]}>'      # shared mailbox shown to clients
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    if c["mailbox"].lower() != c["user"].lower():
        msg["Reply-To"] = c["mailbox"]                     # replies go to the shared mailbox
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    recipients = to_list + cc_list + bcc_list       # bcc kept out of headers
    context = ssl.create_default_context()
    with smtplib.SMTP(c["host"], c["port"], timeout=30) as server:
        server.ehlo()
        server.starttls(context=context)
        server.login(c["user"], c["password"])
        # Envelope sender = the authenticated user (accepted by the server);
        # the From header carries the shared mailbox the client sees.
        server.sendmail(c["user"] or c["mailbox"], recipients, msg.as_string())
    return True
