"""
excel_data_source.py  (v3.1)
------------------------------------------------------------------
Initial-load ingestion for the NEW workbook (Copy_of_Sales_Data-dummy.xlsx).

Purpose
-------
Replaces the old `ap_ar_data.xlsx` extract with a richer source that
has 5 sheets: Sales Order, Invoices, Payments, Contacts, DropdownData.
This module turns those sheets into what the rest of the dashboard
already expects:
  - invoices  (Open + last-90-days Closed  → dashboard + reminder pipeline)
  - clients   (Contacts, keyed by EmailID_New — the reminder recipient)
  - payments  (for DSO, on-time-rate, aging trend)
  - kpis      (computed cache — no re-scan on every widget)

Where the file lives
--------------------
Put the workbook at:  backend/data/Copy_of_Sales_Data-dummy.xlsx
(matches ai_ar_data.xlsx pattern; SALES_XLSX env var overrides.)

Design decisions
----------------
- Reminder recipient = Contacts.EmailID_New  (confirmed with user;
  dummy data uses internal @infrabeat.com owners on purpose).
- Initial scope = every invoice with balance>0 PLUS every Closed
  invoice paid in the last 90 days. Keeps the app snappy and matches
  what a working CFO actually looks at.
- Cached with @st.cache_data(ttl=300) so the CFO screens are instant
  even on a 10k-row workbook.
- Idempotent: import_initial_load() upserts through database.store_invoice
  and skips rows already present, so re-running is safe.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from core.database import get_db, DB_PATH, store_invoice

# The workbook location (override with env var if you want).
XLSX_PATH = os.getenv(
    "SALES_XLSX",
    str(Path(__file__).parent / "data" / "Copy_of_Sales_Data-dummy.xlsx"),
)

CLOSED_WINDOW_DAYS = 90

# ---------------------------------------------------------- LOAD
@st.cache_data(ttl=300, show_spinner="Reading workbook…")
def load_sheets(path: str = XLSX_PATH) -> dict[str, pd.DataFrame]:
    """Load the 4 sheets we care about (skip DropdownData)."""
    if not Path(path).exists():
        return {}
    xl = pd.ExcelFile(path)
    out = {}
    for sheet in ("Invoices", "Payments", "Contacts", "Sales Order"):
        if sheet in xl.sheet_names:
            out[sheet] = xl.parse(sheet)
    return out


@st.cache_data(ttl=300)
def build_view(path: str = XLSX_PATH) -> dict:
    """
    Compute the working slice: Open + recent Closed, joined to contacts.
    Returned dict keys used elsewhere:
        invoices     - the working DataFrame (subset of Invoices sheet)
        clients      - Contacts, indexed for lookup by Customer ID
        payments     - full payments log
        anchor_date  - the 'as-of' day (max of Invoice Date across all rows)
    """
    sheets = load_sheets(path)
    if not sheets:
        return {"invoices": pd.DataFrame(), "clients": pd.DataFrame(),
                "payments": pd.DataFrame(), "anchor_date": date.today()}

    inv = sheets["Invoices"].copy()

    # Collapse line-item rows to one row per invoice. This workbook is a
    # line-item export: multi-line invoices repeat their invoice-level fields
    # (Balance, Total, dates, status) identically on every line row. Summing
    # Balance across all rows double-counts — e.g. a 15-line invoice was
    # counted 15×. Verified: repeats carry an identical Balance and customer,
    # so keeping the first row per Invoice Number is exact, and no code uses
    # line-level columns.
    if "Invoice Number" in inv.columns:
        inv = inv.drop_duplicates(subset=["Invoice Number"], keep="first") \
                 .reset_index(drop=True)

    # Normalise dates
    for col in ("Invoice Date", "Due Date", "Expected Payment Date"):
        if col in inv.columns:
            inv[col] = pd.to_datetime(inv[col], errors="coerce")

    anchor = inv["Invoice Date"].max().date() if inv["Invoice Date"].notna().any() else date.today()

    # Open + last-90-days Closed (the scope confirmed with user)
    open_mask = (inv["Balance"].fillna(0) > 0) & (inv["Invoice Status"] != "Void")
    recent_closed = (inv["Invoice Status"] == "Closed") & \
                    (inv["Invoice Date"] >= pd.Timestamp(anchor - timedelta(days=CLOSED_WINDOW_DAYS)))
    working = inv[open_mask | recent_closed].copy()

    contacts = sheets["Contacts"].copy()
    contacts = contacts[contacts["Contact Type"] == "customer"]

    return {
        "invoices":     working.reset_index(drop=True),
        "clients":      contacts.set_index("Contact ID", drop=False),
        "payments":     sheets.get("Payments", pd.DataFrame()),
        "anchor_date":  anchor,
        "all_invoices": inv,   # kept for aging / trend analytics
    }


# ---------------------------------------------------------- KPIs
@st.cache_data(ttl=300)
def compute_kpis(path: str = XLSX_PATH) -> dict:
    """Board-grade KPIs, all with sensible defaults so widgets never NaN."""
    v = build_view(path)
    inv = v["invoices"]
    anchor = v["anchor_date"]
    if inv.empty:
        return {}

    open_inv = inv[inv["Balance"].fillna(0) > 0]
    overdue  = open_inv[open_inv["Due Date"] < pd.Timestamp(anchor)]

    # DSO / On-time from the settlement date of each Closed invoice.
    # Prefer the REAL last payment date from the Payments sheet; fall back to
    # the last_modified_time proxy only where an invoice has no payment record.
    # Measure over ALL closed invoices, not the working set's recent-closed
    # subset: a recent invoice only appears here once it's Closed, but recent
    # *slow* payers are still Open — so the recent subset is skewed toward fast
    # payers and inflates On-time. The full settled population is unbiased.
    allinv = v.get("all_invoices", inv)
    closed = allinv[allinv["Invoice Status"] == "Closed"].copy()
    dso = None
    on_time = None
    if not closed.empty:
        proxy = pd.to_datetime(closed["last_modified_time"], errors="coerce", dayfirst=True)
        pay = v.get("payments", pd.DataFrame())
        if (not pay.empty and "Invoice Number" in pay.columns
                and "Date" in pay.columns):
            pdates = pd.to_datetime(pay["Date"], errors="coerce")
            settle_by_num = pdates.groupby(pay["Invoice Number"]).max()   # last payment
            settle = closed["Invoice Number"].map(settle_by_num)
            settle = settle.fillna(proxy)                                  # fallback
        else:
            settle = proxy

        deltas = (settle - closed["Invoice Date"]).dt.days
        deltas = deltas[deltas.between(0, 365)]   # sanity filter
        if len(deltas):
            dso = float(deltas.mean())
        on_time = 100.0 * (settle <= closed["Due Date"]).sum() / len(closed)

    total_out    = float(open_inv["Balance"].fillna(0).sum())
    total_over   = float(overdue["Balance"].fillna(0).sum())
    n_over_50k   = int((overdue["Balance"] > 50_000).sum())

    # Concentration: HHI on outstanding balance by customer
    if not open_inv.empty:
        by_cust = open_inv.groupby("Customer Name")["Balance"].sum()
        shares  = by_cust / by_cust.sum()
        hhi     = float((shares ** 2).sum() * 10_000)
        eff_n   = float(1 / (shares ** 2).sum())
    else:
        hhi = eff_n = 0.0

    return {
        "total_outstanding":  total_out,
        "open_invoices":      int(len(open_inv)),
        "overdue_amount":     total_over,
        "overdue_count":      int(len(overdue)),
        "overdue_over_50k":   n_over_50k,
        "dso_days":           dso,
        "on_time_rate":       on_time,
        "hhi":                hhi,
        "effective_clients":  eff_n,
        "unique_clients":     int(open_inv["Customer ID"].nunique()) if not open_inv.empty else 0,
        "anchor_date":        anchor,
        "currency":           "INR",
    }


# ---------------------------------------------------------- INGEST → DB
def import_initial_load(reminder_email_col: str = "EmailID_New",
                        force: bool = False) -> dict:
    """
    Push in-scope invoices into invoices.db via database.store_invoice(),
    joining Contacts to get the reminder-recipient email.

    reminder_email_col : which contact column is the reminder recipient
                        ("EmailID_New" per user; "EmailID" would be the
                        client mailbox for real-world runs).
    force              : ignored today, reserved for a future "re-import"
                        that resets statuses.
    """
    v = build_view()
    if v["invoices"].empty:
        return {"error": "Workbook not found or has no rows",
                "path": XLSX_PATH}

    clients = v["clients"]
    inserted = updated = skipped = missing_email = 0

    with get_db(DB_PATH) as conn:
        for _, r in v["invoices"].iterrows():
            cust_id  = r["Customer ID"]
            cust_row = clients.loc[cust_id] if cust_id in clients.index else None
            recip    = cust_row[reminder_email_col] if cust_row is not None else None
            if not recip or "@" not in str(recip):
                missing_email += 1
                continue

            due = r["Due Date"]
            due_str = due.date().isoformat() if pd.notna(due) else None
            iss = r["Invoice Date"]
            iss_str = iss.date().isoformat() if pd.notna(iss) else None

            _, action = store_invoice(conn, {
                "invoice_number": r["Invoice Number"],
                "client_name":    r["Customer Name"],
                "client_email":   recip,
                "invoice_date":   iss_str,
                "due_date":       due_str,
                "amount":         float(r["Balance"] or 0) if pd.notna(r["Balance"]) else float(r["Total"] or 0),
                "total_amount":   float(r["Total"] or 0),
                "currency":       r["Currency Code"] or "INR",
                "status":         _map_status(r["Invoice Status"], r["Balance"], due, v["anchor_date"]),
                "description":    f"{r.get('Item Name', '')} · PO {r.get('reference_number', '')}",
                "confidence":     1.0,     # structured data, not LLM
            })
            if action == "inserted": inserted += 1
            elif action == "updated": updated += 1
            else: skipped += 1
        conn.commit()

    return {
        "path": XLSX_PATH,
        "inserted": inserted,
        "updated":  updated,
        "skipped":  skipped,
        "missing_email": missing_email,
        "scope": f"Open + last-{CLOSED_WINDOW_DAYS}-days Closed",
        "recipient_column": reminder_email_col,
    }


def _map_status(raw: str, balance, due_date, anchor) -> str:
    """Zoho status → our vocabulary (matches database.normalize_status)."""
    if raw == "Closed" or (balance == 0 and raw != "Void"):
        return "paid"
    if pd.notna(due_date) and due_date < pd.Timestamp(anchor):
        return "overdue"
    return "unpaid"
