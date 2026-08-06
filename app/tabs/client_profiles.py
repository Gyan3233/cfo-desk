"""
tab2_client_profiles.py — v3.2 Client Profiles rebuilt on the new workbook.

Provides a client-picker, per-client KPIs, payment-timing distribution,
open-invoice list, days-late-over-time trend, and P(late) score with SHAP-style
attribution — all driven by the Sales Data workbook and ml_intelligence.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.excel_source import build_view
from ai.ml_intelligence import (
    FEATURE_COLS, attribute, client_scores, score_open_invoices, train_model,
)

COLOR_INK    = "#f0eee5"
COLOR_MUTED  = "#8a8880"
COLOR_GOLD   = "#c9a961"
COLOR_GOOD   = "#8cb04a"
COLOR_BAD    = "#c4614a"


def _base_layout(title: str = "", height: int = 260) -> dict:
    return dict(
        title=dict(text=title, font=dict(color=COLOR_INK, size=13)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=COLOR_INK, size=11),
        margin=dict(l=40, r=20, t=40, b=40),
        height=height,
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
        showlegend=False,
    )


def _money(v) -> str:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return "₹0"
    if abs(n) >= 1e7: return f"₹{n / 1e7:,.2f} Cr"
    if abs(n) >= 1e5: return f"₹{n / 1e5:,.2f} L"
    return f"₹{n:,.0f}"


# ═══════════════════════════════════════════════════════════════════════════
#  DATA
# ═══════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800)
def client_history(customer_id) -> dict:
    """Load one client's full invoice + payment history from the workbook."""
    v = build_view()
    inv = v.get("all_invoices", pd.DataFrame())
    pay = v.get("payments", pd.DataFrame())
    if inv.empty:
        return {}

    my_inv = inv[inv["Customer ID"] == customer_id].copy()
    if my_inv.empty:
        return {}

    my_inv["Invoice Date"] = pd.to_datetime(my_inv["Invoice Date"], errors="coerce")
    my_inv["Due Date"]     = pd.to_datetime(my_inv["Due Date"], errors="coerce")

    pay = pay.copy()
    pay["Date"] = pd.to_datetime(pay["Date"], errors="coerce")
    paid = (pay.groupby("Invoice Number")["Date"].max()
              .reset_index().rename(columns={"Date": "paid_date"}))
    my_inv = my_inv.merge(paid, on="Invoice Number", how="left")

    my_inv["days_to_pay"]  = (my_inv["paid_date"] - my_inv["Invoice Date"]).dt.days
    my_inv["days_late"]    = (my_inv["paid_date"] - my_inv["Due Date"]).dt.days
    my_inv["was_late"]     = (my_inv["days_late"] > 0).astype(int)

    closed = my_inv[my_inv["Invoice Status"] == "Closed"].copy()
    open_  = my_inv[my_inv["Balance"].fillna(0) > 0].copy()

    return {
        "name":          my_inv["Customer Name"].iloc[0],
        "customer_id":   customer_id,
        "all":           my_inv,
        "closed":        closed,
        "open":          open_,
        "n_all":         len(my_inv),
        "n_closed":      len(closed),
        "n_open":        len(open_),
        "outstanding":   float(open_["Balance"].sum()) if not open_.empty else 0.0,
        "late_rate":     float(closed["was_late"].mean()) if not closed.empty else None,
        "avg_dtp":       float(closed["days_to_pay"].dropna().mean()) if not closed.empty else None,
        "median_dtp":    float(closed["days_to_pay"].dropna().median()) if not closed.empty else None,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  CHARTS
# ═══════════════════════════════════════════════════════════════════════════
def fig_payment_timing_cdf(closed: pd.DataFrame) -> go.Figure:
    """Empirical CDF of days-to-pay — 'X% paid within Y days'."""
    dtp = closed["days_to_pay"].dropna().sort_values().values
    if len(dtp) == 0:
        return go.Figure()
    n = len(dtp)
    x = np.concatenate([[0], dtp])
    y = np.concatenate([[0], np.arange(1, n + 1) / n * 100])
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines",
                              line=dict(color=COLOR_GOLD, width=2),
                              fill="tozeroy",
                              fillcolor="rgba(201,169,97,0.10)",
                              hovertemplate="%{x:.0f} days: %{y:.0f}% paid<extra></extra>"))
    # P50 / P90 markers
    p50 = np.percentile(dtp, 50)
    p90 = np.percentile(dtp, 90)
    fig.add_vline(x=p50, line_color=COLOR_MUTED, line_dash="dot",
                  annotation_text=f"P50: {p50:.0f}d",
                  annotation_font=dict(color=COLOR_MUTED, size=10))
    fig.add_vline(x=p90, line_color=COLOR_BAD, line_dash="dot",
                  annotation_text=f"P90: {p90:.0f}d",
                  annotation_font=dict(color=COLOR_BAD, size=10))
    fig.update_layout(**_base_layout("Payment timing · empirical CDF"))
    fig.update_xaxes(title="Days to pay")
    fig.update_yaxes(title="% invoices paid within", range=[0, 100])
    return fig


def fig_days_late_over_time(closed: pd.DataFrame) -> go.Figure:
    if closed.empty:
        return go.Figure()
    d = closed.sort_values("Invoice Date")
    fig = go.Figure()
    fig.add_hline(y=0, line_color=COLOR_MUTED, line_dash="dot")
    fig.add_trace(go.Scatter(x=d["Invoice Date"], y=d["days_late"],
                              mode="lines+markers",
                              line=dict(color=COLOR_GOLD, width=1.5),
                              marker=dict(size=5,
                                          color=[COLOR_BAD if v > 0 else COLOR_GOOD
                                                 for v in d["days_late"].fillna(0)]),
                              hovertemplate="%{x|%d %b %Y}<br>%{y:+.0f} days<extra></extra>"))
    fig.update_layout(**_base_layout("Days late over time"))
    fig.update_yaxes(title="days late (positive = late)")
    return fig


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN RENDER
# ═══════════════════════════════════════════════════════════════════════════
def render_tab2() -> None:
    st.markdown("### 👤 Client Profiles")
    st.caption("Deep-dive on any customer — payment history, P(late) score, "
               "and the drivers behind it.")

    v = build_view()
    inv = v.get("all_invoices", pd.DataFrame())
    if inv.empty:
        st.warning("Workbook has no invoice data.")
        return

    # Only show clients with any invoice history
    all_clients = (inv[["Customer ID", "Customer Name"]]
                    .dropna().drop_duplicates()
                    .sort_values("Customer Name")
                    .reset_index(drop=True))

    # Optional score-based sort
    scores = client_scores()
    if not scores.empty:
        all_clients = all_clients.merge(
            scores[["customer_id", "avg_p_late", "outstanding"]],
            left_on="Customer ID", right_on="customer_id", how="left"
        )
        default_sort = st.radio(
            "Sort clients by",
            ["Name", "Outstanding (₹)", "P(late) — risk"],
            horizontal=True, index=0,
        )
        if default_sort == "Outstanding (₹)":
            all_clients = all_clients.sort_values("outstanding", ascending=False,
                                                    na_position="last")
        elif default_sort == "P(late) — risk":
            all_clients = all_clients.sort_values("avg_p_late", ascending=False,
                                                    na_position="last")

    labels = [f"{r['Customer Name']}"
              + (f"  ·  ₹{(r['outstanding'] or 0) / 1e5:.1f}L"
                 if "outstanding" in r and pd.notna(r.get("outstanding")) else "")
              + (f"  ·  P(late) {r['avg_p_late']:.0%}"
                 if "avg_p_late" in r and pd.notna(r.get("avg_p_late")) else "")
              for _, r in all_clients.iterrows()]

    idx = st.selectbox("Select a client", range(len(labels)),
                        format_func=lambda i: labels[i])
    cust_id = all_clients.iloc[idx]["Customer ID"]

    hist = client_history(cust_id)
    if not hist:
        st.warning("No history for this client.")
        return

    # ── HEADER KPI STRIP ────────────────────────────────────────────────
    st.divider()
    st.markdown(f"### {hist['name']}")

    # ML score — pull the specific per-invoice scores for this client
    open_scored = pd.DataFrame()
    if not scores.empty:
        all_open = score_open_invoices()
        if not all_open.empty:
            open_scored = all_open[all_open["customer_id"] == cust_id]

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total invoices", f"{hist['n_all']:,}",
              help="Every invoice ever raised for this client, all statuses.")
    k2.metric("Open invoices", f"{hist['n_open']:,}",
              delta=_money(hist["outstanding"]),
              help="Invoices with Balance > 0 today.")
    k3.metric("Historical late rate",
              f"{hist['late_rate'] * 100:.0f}%" if hist["late_rate"] is not None else "—",
              help="Fraction of closed invoices paid after the due date.")
    k4.metric("Median days-to-pay",
              f"{hist['median_dtp']:.0f}d" if hist["median_dtp"] is not None else "—",
              help="How long, on average, this client takes to pay.")
    if not open_scored.empty:
        avg_p = float(open_scored["p_late"].mean())
        k5.metric("Avg P(late) on open",
                  f"{avg_p:.0%}",
                  help="Mean predicted late-probability across this client's "
                       "currently-open invoices.")
    else:
        k5.metric("Avg P(late) on open", "—",
                  help="No open invoices to score.")

    st.divider()

    # ── OPEN INVOICES + SCORES ─────────────────────────────────────────
    if not open_scored.empty:
        st.markdown("#### 📄 Open invoices for this client")
        show = open_scored[["invoice_number", "balance", "due_date",
                             "p_late", "expected_loss"]].copy()
        show["balance"]       = show["balance"].apply(lambda x: f"₹{x:,.0f}")
        show["expected_loss"] = show["expected_loss"].apply(lambda x: f"₹{x:,.0f}")
        show["p_late"]        = show["p_late"].apply(lambda p: f"{p:.0%}")
        show["due_date"]      = pd.to_datetime(show["due_date"]).dt.strftime("%d %b %Y")
        show.columns = ["Invoice", "Balance", "Due", "P(late)", "Expected loss"]
        st.dataframe(show.sort_values("Expected loss", ascending=False),
                      hide_index=True, use_container_width=True)
    elif hist["n_open"] > 0:
        st.info("This client has open invoices but not enough history to score them.")

    st.divider()

    # ── CHARTS ─────────────────────────────────────────────────────────
    cc1, cc2 = st.columns(2)
    with cc1:
        st.markdown("**<span class='sec-dia'>◆</span> Payment timing distribution**", unsafe_allow_html=True,
                    help="How many days after invoice date this client "
                         "typically pays. Read: '80% pay within X days'.")
        if hist["closed"].empty:
            st.caption("_No closed invoices yet._")
        else:
            st.plotly_chart(fig_payment_timing_cdf(hist["closed"]),
                             use_container_width=True)

    with cc2:
        st.markdown("**<span class='sec-dia'>◆</span> Days late over time**", unsafe_allow_html=True,
                    help="Each dot is a paid invoice. Above the line = late; "
                         "below = paid early. Trend up-and-right means "
                         "collections are slipping for this client.")
        if hist["closed"].empty:
            st.caption("_No closed invoices yet._")
        else:
            st.plotly_chart(fig_days_late_over_time(hist["closed"]),
                             use_container_width=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════════
    #  v3.3 · Promise-to-Pay per invoice + AI communication timeline
    # ══════════════════════════════════════════════════════════════════
    # Look up client_id in invoices.db by client email so we can pull the
    # PTP and reply data — tab2 above uses the workbook's Customer ID
    # which doesn't match invoices.db's clients.id.
    from core.database import DB_PATH, get_db
    from app.tabs.ptp_ui import render_tab2_ptp_block, render_tab2_timeline

    db_client_id = None
    with get_db(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM clients WHERE lower(name) = lower(?) LIMIT 1",
            (hist["name"],),
        ).fetchone()
        if row:
            db_client_id = row["id"]

    if db_client_id is None:
        st.caption("_This client has no reminder/reply history in the database yet._")
    else:
        st.markdown("#### <span class='sec-dia'>◆</span> Promise-to-Pay status per invoice", unsafe_allow_html=True,
                    help="Every invoice for this client that has a PTP "
                         "promised date or one or more extension requests.")
        render_tab2_ptp_block(db_client_id)

        st.divider()

        st.markdown("#### <span class='sec-dia'>◆</span> AI communication timeline", unsafe_allow_html=True,
                    help="Full history of reminder emails sent and client "
                         "replies received, with AI classification of "
                         "each reply and payment commitments extracted.")
        render_tab2_timeline(db_client_id)

    st.divider()

    # ── FULL HISTORY TABLE (collapsible) ───────────────────────────────
    with st.expander(f"📚 Full invoice history ({hist['n_all']:,} rows)"):
        h = hist["all"][["Invoice Number", "Invoice Date", "Due Date",
                          "Total", "Balance", "Invoice Status", "days_late"]].copy()
        h["Invoice Date"] = pd.to_datetime(h["Invoice Date"]).dt.strftime("%d %b %Y")
        h["Due Date"]     = pd.to_datetime(h["Due Date"]).dt.strftime("%d %b %Y")
        h["Total"]        = h["Total"].apply(
            lambda x: f"₹{x:,.0f}" if pd.notna(x) else "—")
        h["Balance"]      = h["Balance"].apply(
            lambda x: f"₹{x:,.0f}" if pd.notna(x) else "—")
        st.dataframe(h.rename(columns={"days_late": "Days late"}),
                      hide_index=True, use_container_width=True, height=380)
