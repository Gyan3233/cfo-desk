"""
template_manager.py — Per-client reminder-email templates for CFO Copilot.

How it works
------------
Templates live as JSON files in a `templates/` folder next to app.py.
One file per client, plus `_default.json` used when no client-specific
template exists. The pipeline (Gmail path) and the CSV path both call
`render_for_client(...)` to build the draft subject/body, so every draft
automatically picks up the right wording per company.

Template file format (templates/acme_industrial.json):

    {
      "template_name": "Acme Industrial",
      "match": {
        "emails": ["accounts@acme-industrial.com"],
        "name_contains": ["acme"]
      },
      "subject": "Payment reminder — Invoice {invoice_number} · {amount_fmt}",
      "body": "Dear {contact_name},\n\n ... {due_date} ... {days_text} ..."
    }

Matching order (first hit wins):
    1. exact client email match against match.emails
    2. case-insensitive substring match of client name vs match.name_contains
    3. _default.json

Available placeholders (unknown placeholders are left as-is, never crash):
    {contact_name}   {client_name}   {invoice_number}   {amount}
    {amount_fmt}     {currency}      {issue_date}       {due_date}
    {days_text}      -> "is due in 5 days" / "is due today" / "is 12 days overdue"
    {sender_name}    {sender_company}

Managing templates — three ways:
    A. In the dashboard: Tab 3 → "✉️ Email Templates" section (edit / add /
       delete / live preview). Changes write straight to the JSON files.
    B. By hand: add/edit/delete a .json file in templates/ — no restart
       needed, files are re-read on every render.
    C. In code: add_or_update_template(...) / delete_template(...).
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path

TEMPLATE_DIR = Path(os.getenv("TEMPLATE_DIR", "templates"))
DEFAULT_KEY = "_default"

SENDER_NAME = os.getenv("SENDER_NAME", "Accounts Receivable Team")
SENDER_COMPANY = os.getenv("SENDER_COMPANY", "Infrabeat Technologies")


# ---------------------------------------------------------------- file I/O
def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "template"


def list_templates() -> dict[str, dict]:
    """Return {slug: template_dict} for every JSON file in TEMPLATE_DIR."""
    out: dict[str, dict] = {}
    if not TEMPLATE_DIR.exists():
        return out
    for p in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # skip broken files rather than take the app down
    return out


def add_or_update_template(slug: str, template: dict) -> str:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(slug)
    (TEMPLATE_DIR / f"{slug}.json").write_text(
        json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return slug


def delete_template(slug: str) -> bool:
    if slug == DEFAULT_KEY:
        return False  # never delete the fallback
    p = TEMPLATE_DIR / f"{slug}.json"
    if p.exists():
        p.unlink()
        return True
    return False


# ---------------------------------------------------------------- matching
def find_template(client_name: str = "", client_email: str = "") -> dict:
    templates = list_templates()
    email = (client_email or "").lower().strip()
    name = (client_name or "").lower().strip()

    for slug, tpl in templates.items():
        if slug == DEFAULT_KEY:
            continue
        match = tpl.get("match", {})
        if email and email in [e.lower() for e in match.get("emails", [])]:
            return tpl
    for slug, tpl in templates.items():
        if slug == DEFAULT_KEY:
            continue
        match = tpl.get("match", {})
        if name and any(frag.lower() in name for frag in match.get("name_contains", [])):
            return tpl

    return templates.get(DEFAULT_KEY, _builtin_default())


def _builtin_default() -> dict:
    """Last-resort template if templates/_default.json is missing."""
    return {
        "template_name": "Built-in default",
        "subject": "Payment reminder — Invoice {invoice_number} ({amount_fmt})",
        "body": (
            "Dear {contact_name},\n\n"
            "This is a friendly reminder that invoice {invoice_number} for "
            "{amount_fmt} {days_text}.\n\n"
            "If payment has already been made, please disregard this message.\n\n"
            "Best regards,\n{sender_name}\n{sender_company}"
        ),
    }


# ---------------------------------------------------------------- rendering
class _SafeDict(dict):
    def __missing__(self, key):  # leave unknown placeholders visible, don't crash
        return "{" + key + "}"


def _days_text(due_date_str: str, today: date | None = None) -> str:
    today = today or date.today()
    try:
        due = datetime.strptime(str(due_date_str)[:10], "%Y-%m-%d").date()
    except ValueError:
        return "is approaching its due date"
    delta = (due - today).days
    if delta > 1:
        return f"is due in {delta} days ({due.strftime('%d %b %Y')})"
    if delta == 1:
        return f"is due tomorrow ({due.strftime('%d %b %Y')})"
    if delta == 0:
        return "is due today"
    if delta == -1:
        return "is 1 day overdue"
    return f"is {-delta} days overdue (was due {due.strftime('%d %b %Y')})"


def render_for_client(invoice: dict, today: date | None = None) -> dict:
    """invoice keys expected: client_name, client_email, contact_name (opt),
    invoice_number, amount, currency, issue_date, due_date.
    Returns {"subject": ..., "body": ..., "template_name": ...}."""
    tpl = find_template(invoice.get("client_name", ""), invoice.get("client_email", ""))

    try:
        amount_fmt = f"{invoice.get('currency', 'USD')} {float(invoice.get('amount', 0)):,.2f}"
    except (TypeError, ValueError):
        amount_fmt = f"{invoice.get('currency', 'USD')} {invoice.get('amount', '')}"

    ctx = _SafeDict(
        contact_name=invoice.get("contact_name") or invoice.get("client_name", "Sir/Madam"),
        client_name=invoice.get("client_name", ""),
        client_email=invoice.get("client_email", ""),
        invoice_number=invoice.get("invoice_number", ""),
        amount=invoice.get("amount", ""),
        amount_fmt=amount_fmt,
        currency=invoice.get("currency", "USD"),
        issue_date=invoice.get("issue_date", ""),
        due_date=invoice.get("due_date", ""),
        days_text=_days_text(invoice.get("due_date", ""), today),
        sender_name=SENDER_NAME,
        sender_company=SENDER_COMPANY,
    )
    return {
        "subject": tpl.get("subject", "").format_map(ctx),
        "body": tpl.get("body", "").format_map(ctx),
        "template_name": tpl.get("template_name", "unnamed"),
    }


# ---------------------------------------------------------------- streamlit UI
def template_manager_ui() -> None:
    """Drop into Tab 3: full CRUD editor with live preview."""
    import streamlit as st

    st.subheader("✉️ Email Templates (per client)")
    st.caption(
        "One template per company; `_default` is used when no client template "
        "matches. Placeholders: {contact_name} {client_name} {invoice_number} "
        "{amount_fmt} {due_date} {days_text} {sender_name} {sender_company}"
    )

    templates = list_templates()
    slugs = list(templates.keys()) or [DEFAULT_KEY]
    col_a, col_b = st.columns([2, 1])
    selected = col_a.selectbox("Template", slugs + ["➕ New template…"])

    if selected == "➕ New template…":
        slug_input = st.text_input("New template file name (e.g. helio_energy)")
        tpl = _builtin_default() | {"match": {"emails": [], "name_contains": []}}
    else:
        slug_input = selected
        tpl = templates.get(selected, _builtin_default())

    name = st.text_input("Template name", tpl.get("template_name", ""))
    emails = st.text_input(
        "Match: client emails (comma-separated)",
        ", ".join(tpl.get("match", {}).get("emails", [])),
    )
    frags = st.text_input(
        "Match: name contains (comma-separated)",
        ", ".join(tpl.get("match", {}).get("name_contains", [])),
    )
    subject = st.text_input("Subject", tpl.get("subject", ""))
    body = st.text_area("Body", tpl.get("body", ""), height=260)

    c1, c2, c3 = st.columns(3)
    if c1.button("💾 Save template", use_container_width=True) and slug_input:
        add_or_update_template(
            slug_input,
            {
                "template_name": name,
                "match": {
                    "emails": [e.strip() for e in emails.split(",") if e.strip()],
                    "name_contains": [f.strip() for f in frags.split(",") if f.strip()],
                },
                "subject": subject,
                "body": body,
            },
        )
        st.success(f"Saved templates/{_slug(slug_input)}.json")
        st.rerun()

    if (
        selected not in (DEFAULT_KEY, "➕ New template…")
        and c2.button("🗑 Delete", use_container_width=True)
    ):
        delete_template(selected)
        st.warning(f"Deleted {selected}")
        st.rerun()

    if c3.button("👁 Preview", use_container_width=True):
        # Preview uses the *edited* (unsaved) subject/body against sample data.
        ctx = _SafeDict(
            contact_name="Priya",
            client_name=name or "Sample Client Ltd",
            invoice_number="INV-9999",
            amount="12500.50",
            amount_fmt="USD 12,500.50",
            currency="USD",
            issue_date="2026-06-01",
            due_date="2026-07-25",
            days_text=_days_text("2026-07-25"),
            sender_name=SENDER_NAME,
            sender_company=SENDER_COMPANY,
        )
        st.info(f"**Subject:** {subject.format_map(ctx)}")
        st.text(body.format_map(ctx))
