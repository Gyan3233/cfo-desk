"""
FILE: backend/csv_invoice_source.py   ■ new in v3
PURPOSE: Generate reminder drafts from a CSV / SQL invoice table instead of
         scanning Gmail — the second position of the Tab 3 "Invoice source"
         toggle.

WHERE IT SITS IN THE PIPELINE:
  Original: Gmail scan → LLM extraction → store_invoice → drafts → review queue
  This:     CSV/SQL rows ─────────────→ store_invoice → drafts → review queue

Everything downstream is IDENTICAL: invoices go through database.store_invoice
(same dedupe rules), drafts go through database.save_draft into email_drafts
with status='pending', so the existing Tab 3 Draft Review Queue, editing, and
gmail_client.send_email() work unchanged.

Draft wording comes from template_manager.render_for_client() — per-client
templates in templates/*.json.

CSV CONTRACT (data/sample_invoices.csv shows the format):
  client_name, client_email, contact_name, invoice_number,
  amount, currency, issue_date (YYYY-MM-DD), due_date (YYYY-MM-DD), notes

SQL ALTERNATIVE: any table/view with the same columns, default name
'invoice_source' inside invoices.db. One-off load:
  df.to_sql("invoice_source", sqlite3.connect("invoices.db"),
            if_exists="replace", index=False)
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# Real project helpers — single write path shared with the Gmail pipeline.
from core.database import (
    DB_PATH, get_db, init_db, save_draft, store_invoice,
)
from core.template_manager import render_for_client

CC_EMAIL = os.getenv("CC_EMAIL", "")
REMINDER_HORIZON_DAYS = int(os.getenv("DUE_SOON_DAYS", "7"))  # same env as pipeline.py

REQUIRED_COLS = [
    "client_name", "client_email", "invoice_number",
    "amount", "currency", "issue_date", "due_date",
]


# ---------------------------------------------------------------- loading
def load_invoices_from_csv(path_or_buffer) -> tuple[pd.DataFrame, list[str]]:
    """Read + validate the CSV. Returns (clean_df, list_of_row_errors)."""
    df = pd.read_csv(path_or_buffer, dtype=str).fillna("")
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    errors: list[str] = []
    keep_rows = []
    for i, row in df.iterrows():
        problems = []
        if not row["client_email"] or "@" not in row["client_email"]:
            problems.append("invalid client_email")
        try:
            float(row["amount"])
        except ValueError:
            problems.append("amount not numeric")
        for col in ("issue_date", "due_date"):
            try:
                datetime.strptime(str(row[col])[:10], "%Y-%m-%d")
            except ValueError:
                problems.append(f"{col} not YYYY-MM-DD")
        if problems:
            errors.append(
                f"Row {i + 2} ({row.get('invoice_number', '?')}): " + ", ".join(problems)
            )
        else:
            keep_rows.append(i)
    return df.loc[keep_rows].reset_index(drop=True), errors


def load_invoices_from_sql(table: str = "invoice_source",
                           db_path: str | None = None) -> pd.DataFrame:
    """Same contract as the CSV loader, reading from a SQLite table/view."""
    with sqlite3.connect(db_path or DB_PATH) as c:
        df = pd.read_sql_query(f'SELECT * FROM "{table}"', c).astype(str)
    missing = [col for col in REQUIRED_COLS if col not in df.columns]
    if missing:
        raise ValueError(f"Table '{table}' is missing required columns: {missing}")
    return df.fillna("")


# ---------------------------------------------------------------- pipeline
def _needs_reminder(due_date_str: str, today: date) -> bool:
    try:
        due = datetime.strptime(str(due_date_str)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return (due - today).days <= REMINDER_HORIZON_DAYS  # includes overdue


def _draft_already_queued(conn, invoice_id: int) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM email_drafts WHERE invoice_id = ? "
        "AND status IN ('pending','approved')",
        (invoice_id,),
    ).fetchone())


def run_csv_pipeline(df: pd.DataFrame, today: date | None = None,
                     force_all: bool = False) -> dict:
    """Structured rows → database.store_invoice → per-client-template drafts
    via database.save_draft (status='pending') → Tab 3 review queue.

    force_all=True drafts every row regardless of due-date horizon (useful
    when testing with dummy data whose dates are far out).

    Dedupe layers:
      1. store_invoice: same invoice_number + client → 'updated', not re-inserted
      2. _draft_already_queued: invoice with a pending/approved draft → skipped
    """
    today = today or date.today()
    log: list[str] = []
    created = skipped_dup = skipped_not_due = 0

    init_db(DB_PATH)
    with get_db(DB_PATH) as conn:
        for _, row in df.iterrows():
            r = row.to_dict()

            # 1) Store the invoice through the SAME path Gmail extraction uses.
            invoice_id, action = store_invoice(conn, {
                "client_name":    r["client_name"],
                "client_email":   r["client_email"],
                "invoice_number": r["invoice_number"],
                "invoice_date":   r["issue_date"],
                "due_date":       r["due_date"],
                "amount":         float(r["amount"]),
                "total_amount":   float(r["amount"]),
                "currency":       r.get("currency", "USD") or "USD",
                "status":         "unpaid",
                "description":    r.get("notes", ""),
                "confidence":     1.0,          # structured data — no LLM uncertainty
            })
            if invoice_id is None:
                log.append(f"⏭ {r['invoice_number']}: store_invoice skipped ({action})")
                continue

            client_id = conn.execute(
                "SELECT client_id FROM invoices WHERE id = ?", (invoice_id,)
            ).fetchone()["client_id"]

            # 2) Due-date horizon (same DUE_SOON_DAYS rule as the Gmail pipeline).
            if not force_all and not _needs_reminder(r["due_date"], today):
                skipped_not_due += 1
                log.append(
                    f"⏭ {r['invoice_number']}: not due within "
                    f"{REMINDER_HORIZON_DAYS}d, no draft"
                )
                continue

            # 3) Don't double-queue.
            if _draft_already_queued(conn, invoice_id):
                skipped_dup += 1
                log.append(f"⏭ {r['invoice_number']}: draft already in queue, skipped")
                continue

            # 4) Per-client template → draft in the normal review queue.
            rendered = render_for_client({
                "client_name":    r["client_name"],
                "client_email":   r["client_email"],
                "contact_name":   r.get("contact_name", ""),
                "invoice_number": r["invoice_number"],
                "amount":         r["amount"],
                "currency":       r.get("currency", "USD") or "USD",
                "issue_date":     r["issue_date"],
                "due_date":       r["due_date"],
            }, today=today)

            draft_id = save_draft(conn, {
                "invoice_id": invoice_id,
                "client_id":  client_id,
                "to_email":   r["client_email"],
                "cc_email":   CC_EMAIL,
                "subject":    rendered["subject"],
                "body":       rendered["body"],
            })
            created += 1
            log.append(
                f"✅ {r['invoice_number']} → draft #{draft_id} for "
                f"{r['client_name']} (template: {rendered['template_name']})"
            )
        conn.commit()

    return {
        "created": created,
        "skipped_duplicate": skipped_dup,
        "skipped_not_due": skipped_not_due,
        "log": log,
    }


# ---------------------------------------------------------------- streamlit UI
def csv_source_ui() -> None:
    """Rendered in Tab 3 when the invoice-source toggle = CSV / SQL table."""
    import streamlit as st

    st.markdown("##### 📄 Draft generation from CSV / SQL table")
    st.caption(
        "Skips Gmail + LLM extraction; invoices stored via the same "
        "`store_invoice` path, drafts join the normal Review Queue below "
        "with per-client templates applied."
    )

    src = st.radio("Data source",
                   ["Upload CSV", "Bundled sample CSV", "SQL table"],
                   horizontal=True, key="csv_src_kind")
    df, errors = None, []

    if src == "Upload CSV":
        up = st.file_uploader("Invoice CSV", type=["csv"], key="csv_upload")
        if up:
            df, errors = load_invoices_from_csv(up)
    elif src == "Bundled sample CSV":
        sample = Path(__file__).parent / "data" / "sample_invoices.csv"
        if sample.exists():
            df, errors = load_invoices_from_csv(sample)
        else:
            st.error(f"Sample file not found: {sample}")
    else:
        table = st.text_input("SQLite table/view name", "invoice_source",
                              key="csv_sql_table")
        if st.button("Load table", key="csv_sql_load"):
            try:
                st.session_state["csv_sql_df"] = load_invoices_from_sql(table)
            except Exception as e:
                st.error(f"Could not read table: {e}")
        df = st.session_state.get("csv_sql_df")

    if errors:
        st.warning("Some rows were skipped:\n\n" + "\n".join(f"- {e}" for e in errors))

    if df is not None and len(df):
        st.dataframe(df, use_container_width=True)
        force_all = st.checkbox(
            "Draft ALL rows (ignore the due-date horizon) — useful for testing",
            value=True, key="csv_force_all",
        )
        if st.button("🚀 Generate drafts from this data", type="primary",
                     key="csv_generate"):
            result = run_csv_pipeline(df, force_all=force_all)
            st.success(
                f"{result['created']} drafts created · "
                f"{result['skipped_duplicate']} already queued · "
                f"{result['skipped_not_due']} not yet due"
            )
            st.code("\n".join(result["log"]) or "(nothing to do)", language="text")
            st.info("Scroll down to the **Draft Review Queue** to edit and send.")
