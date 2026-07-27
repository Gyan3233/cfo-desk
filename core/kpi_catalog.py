"""
kpi_catalog.py — single source of truth for every KPI's tooltip.

Why: request #6 was "add an ⓘ symbol to every KPI so users know what it is".
Rather than sprinkle explanations across widgets, we keep them here and
render with a helper. If leadership asks 'what does DSO mean?' the answer
lives in one place.

Usage in app.py:
    from core.kpi_catalog import kpi_metric
    kpi_metric("total_outstanding", value=fmt_money(k['total_outstanding']))
"""
import streamlit as st

CATALOG = {
    # ── Cash / Liquidity ────────────────────────────────────────────────
    "total_outstanding": {
        "label": "Total Outstanding",
        "help":  "Sum of Balance across every open invoice (Balance > 0). "
                 "The money clients owe you right now — the AR portfolio "
                 "you're managing.",
    },
    "open_invoices": {
        "label": "Open Invoices",
        "help":  "How many invoices currently have Balance > 0. Volume signal — "
                 "helps size the collections workload.",
    },
    "overdue_amount": {
        "label": "Overdue Amount",
        "help":  "Sum of Balance for invoices whose Due Date is before today. "
                 "This is money you should already have collected.",
    },
    "overdue_count": {
        "label": "Overdue Invoices",
        "help":  "Number of invoices past their due date. Every one of these "
                 "warrants a reminder or a phone call.",
    },
    "overdue_over_50k": {
        "label": "Large Overdues",
        "help":  "Overdue invoices with Balance > ₹50,000 — the ones that "
                 "move the P&L if collected this month.",
    },
    "dso_days": {
        "label": "DSO (days)",
        "help":  "Days Sales Outstanding — average days between invoice date "
                 "and payment date, computed over Closed invoices in the last "
                 "90 days. Every extra day of DSO is one day of cash tied up. "
                 "Target: ≤ payment terms + 5.",
    },
    "on_time_rate": {
        "label": "On-time Payment Rate",
        "help":  "Share of Closed invoices where paid_date ≤ due_date, over "
                 "the last 90 days. > 70% healthy, > 85% best-in-class. Most "
                 "honest measure of collections quality.",
    },
    "hhi": {
        "label": "Concentration (HHI)",
        "help":  "Herfindahl-Hirschman Index on outstanding balance by "
                 "customer, ×10,000. < 1,500 = diversified · 1,500–2,500 = "
                 "moderate · > 2,500 = highly concentrated (vulnerable to "
                 "one client's stress).",
    },
    "effective_clients": {
        "label": "Effective # of Clients",
        "help":  "1 / Σ(client share²). You might have many clients on paper, "
                 "but if two dominate, your effective number is small.",
    },
    "unique_clients": {
        "label": "Active Clients",
        "help":  "Unique customers with at least one open invoice today.",
    },

    # ── Reminder pipeline ────────────────────────────────────────────────
    "drafts_pending": {
        "label": "Drafts Awaiting Review",
        "help":  "Reminder drafts sitting in the Notification Center waiting "
                 "for a finance user to review/edit before their scheduled "
                 "send date. Do nothing and they auto-send on schedule.",
    },
    "reminders_sent_30d": {
        "label": "Reminders Sent (30d)",
        "help":  "How many reminder emails were dispatched in the trailing "
                 "30 days — the volume signal of active collections.",
    },
    "reminder_response_rate": {
        "label": "Reminder Response Rate",
        "help":  "Share of reminders that got a client reply within 7 days. "
                 "Measures whether your templates are landing.",
    },

    # ── Client-level ────────────────────────────────────────────────────
    "reliability_score": {
        "label": "Reliability Score",
        "help":  "0–100 score = 100 × (1 − P(late)) from the calibrated "
                 "logistic regression. ≥ 65 green · 40–64 gold · < 40 red.",
    },
    "distress_trend": {
        "label": "Payment-Timing Trend",
        "help":  "Slope (days/month) of the client's rolling days-to-pay. "
                 "> +0.5 = degrading · < −0.5 = improving · else stable. "
                 "Leading indicator, catches problems 60–90 days early.",
    },
}


def kpi_metric(key: str, value, delta=None, delta_color="normal"):
    """Streamlit metric with our catalog tooltip.

    Falls back to the raw key if we forgot to catalog something — better to
    show than to crash.
    """
    meta = CATALOG.get(key, {"label": key.replace("_", " ").title(),
                             "help": "(no description yet — add to kpi_catalog.py)"})
    st.metric(label=meta["label"], value=value, delta=delta,
              delta_color=delta_color, help=meta["help"])


def kpi_help(key: str) -> str:
    """Return the tooltip text — for chart titles etc. where st.metric isn't used."""
    return CATALOG.get(key, {}).get("help", "")


def kpi_label(key: str) -> str:
    return CATALOG.get(key, {}).get("label", key)
