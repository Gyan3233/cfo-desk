"""
ptp_ui.py — v3.3
Render helpers for PTP + communication timeline. Kept separate from the
data/rule module (ptp_intelligence.py) so tests can run headlessly.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.database import DB_PATH, get_db
from ai.ptp_intelligence import (
    client_communication_timeline, client_ptp_kpis, ensure_schema,
    invoice_ptp_status, poll_gmail_replies, ptp_summary,
)

COLOR_INK   = "#f0eee5"
COLOR_MUTED = "#8a8880"
COLOR_GOLD  = "#c9a961"
COLOR_GOOD  = "#8cb04a"
COLOR_BAD   = "#c4614a"


def _layout(title: str = "", height: int = 260) -> dict:
    return dict(
        title=dict(text=title, font=dict(color=COLOR_INK, size=13)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=COLOR_INK, size=11),
        margin=dict(l=40, r=20, t=40, b=40), height=height,
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
        showlegend=False,
    )


def _fmt_date(s: str | None) -> str:
    if not s: return "—"
    try:
        return pd.to_datetime(s).strftime("%d %b %Y")
    except Exception:
        return str(s)[:10]


# ═══════════════════════════════════════════════════════════════════════════
#  TAB 1 — PTP summary section
# ═══════════════════════════════════════════════════════════════════════════
def render_tab1_ptp_summary() -> None:
    ensure_schema()
    summ = ptp_summary()

    st.markdown("#### <span class='sec-dia'>◆</span> Promise-to-Pay (PTP) Analysis", unsafe_allow_html=True,
                help="A Promise-to-Pay is a client commitment to pay AFTER "
                     "the invoice's current due date. Confirmations that "
                     "the client will pay on or before the due date do NOT "
                     "count. Days delayed is always measured against the "
                     "ORIGINAL due date, so serial extenders are visible.")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total PTPs recorded", f"{summ['total_ptps']:,}",
              help="Total number of promise-to-pay commitments captured "
                   "from client replies.")
    k2.metric("Avg days extended", f"{summ['avg_days_extended']:.1f}d",
              help="Across every PTP event, mean gap between previous due "
                   "date and the new promised date.")
    k3.metric("Repeat offenders (≥3 PTPs)", f"{summ['repeat_offenders']}",
              help="Number of DISTINCT clients who have promised extension "
                   "3 or more times. These are structural late payers, not "
                   "one-offs.")
    hi_days = (max([t["total_days_extended"] for t in summ["top_extenders"]])
                if summ["top_extenders"] else 0)
    k4.metric("Longest client delay",
              f"{hi_days} days" if hi_days else "—",
              help="Total days extended by the single most-delayed client "
                   "across all their invoices.")

    if summ["total_ptps"] == 0:
        st.caption("_No PTP events yet. Poll Gmail replies from Tab 3 to start_ "
                   "_capturing client commitments._")
        return

    c1, c2 = st.columns([1, 1])

    with c1:
        st.markdown("**<span class='sec-dia'>◆</span> Extension request histogram**", unsafe_allow_html=True)
        st.caption("How many invoices had 1 vs 2 vs 3+ promise-to-pay events. "
                   "Long tail = collections process needs to escalate sooner.")
        hist = summ["extension_histogram"]
        if hist:
            df = pd.DataFrame(hist)
            df["bucket"] = df["extension_count"].apply(
                lambda n: f"{n} extension{'s' if n > 1 else ''}")
            fig = go.Figure()
            fig.add_bar(
                x=df["bucket"], y=df["n"],
                marker_color=[COLOR_GOLD if n < 3 else COLOR_BAD
                              for n in df["extension_count"]],
                text=df["n"], textposition="outside",
                hovertemplate="%{x}<br>%{y} invoices<extra></extra>",
            )
            fig.update_layout(**_layout(""))
            fig.update_yaxes(title="# invoices")
            st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.markdown("**<span class='sec-dia'>◆</span> Top 10 clients by total days extended**", unsafe_allow_html=True)
        st.caption("Sum of days-extended across every PTP event by client. "
                   "These are the clients dragging your DSO the most.")
        top = summ["top_extenders"]
        if top:
            df = pd.DataFrame(top).head(10)
            fig = go.Figure()
            fig.add_bar(
                x=df["total_days_extended"], y=df["client"],
                orientation="h", marker_color=COLOR_BAD,
                text=[f"{d}d ({int(n)}×)" for d, n
                      in zip(df["total_days_extended"], df["extension_count"])],
                textposition="outside",
                hovertemplate="%{y}<br>%{x} total days · "
                              "avg %{customdata:.1f}d/PTP<extra></extra>",
                customdata=df["avg_days"],
            )
            fig.update_layout(**_layout("", height=300))
            fig.update_yaxes(autorange="reversed", title=None)
            fig.update_xaxes(title="Total days extended")
            st.plotly_chart(fig, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
#  TAB 2 — PTP section for a single client's invoices
# ═══════════════════════════════════════════════════════════════════════════
def render_tab2_ptp_block(client_id: int) -> None:
    ensure_schema()
    with get_db(DB_PATH) as conn:
        # An invoice qualifies as a real PTP only when:
        #   - it has at least one row in ptp_events (the strict test), OR
        #   - its latest_due_date differs from its original_due_date, OR
        #   - it has an expected_payment_date that differs from original_due_date.
        # This excludes confirmations that were incorrectly marked as PTPs
        # by older code (extension_count > 0 but dates identical).
        invs = conn.execute("""
            SELECT DISTINCT i.id, i.invoice_number, i.original_due_date,
                   i.latest_due_date, i.expected_payment_date, i.extension_count,
                   i.status, i.total_amount, i.amount, i.currency, i.due_date
              FROM invoices i
             WHERE i.client_id = ?
               AND (
                   EXISTS (SELECT 1 FROM ptp_events p WHERE p.invoice_id = i.id)
                   OR (i.latest_due_date IS NOT NULL
                       AND i.original_due_date IS NOT NULL
                       AND substr(i.latest_due_date,1,10) != substr(i.original_due_date,1,10))
                   OR (i.expected_payment_date IS NOT NULL
                       AND i.original_due_date IS NOT NULL
                       AND substr(i.expected_payment_date,1,10) != substr(i.original_due_date,1,10))
               )
             ORDER BY i.due_date DESC
        """, (client_id,)).fetchall()

    if not invs:
        st.info("No promise-to-pay events for this client yet — either the "
                "client has never extended, or Gmail replies haven't been "
                "scanned in.")
        return

    st.caption("Days delayed is measured from the **original** due date, "
               "so it accumulates across every extension. That's the honest "
               "number to bring to a collections call.")

    rows = []
    for inv in invs:
        s = invoice_ptp_status(inv["id"])
        if not s: continue
        rows.append({
            "Invoice":       s["invoice_number"],
            "Original due":  _fmt_date(s["original_due"]),
            "Latest due":    _fmt_date(s["latest_due"]),
            "PTP date":      _fmt_date(s["ptp_date"]),
            "Days delayed":  s["days_delayed"],
            "Extensions":    s["extension_count"],
            "Status":        s["status"] or "—",
            "Amount":        f"₹{float(s['amount'] or 0):,.0f}",
        })
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, hide_index=True, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
#  TAB 2 — communication timeline for one client
# ═══════════════════════════════════════════════════════════════════════════
CATEGORY_LABEL = {
    "specific_day":     ("🗓️ Specific date",   COLOR_GOOD),
    "cycle":            ("🔁 Payment cycle",   COLOR_GOLD),
    "vague":            ("💭 Vague",           COLOR_MUTED),
    "claim_initiated":  ("✅ Claims initiated", COLOR_GOOD),
    "blocked_internal": ("⏸ Blocked internal", COLOR_MUTED),
    "disputed":         ("⚠ Disputed",         COLOR_BAD),
    "no_commitment":    ("— No commitment",    COLOR_MUTED),
}


def render_tab2_timeline(client_id: int) -> None:
    ensure_schema()
    events = client_communication_timeline(client_id)
    kpis   = client_ptp_kpis(client_id)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Reminders sent",     kpis["reminders_sent"],
              help="Outbound reminder emails dispatched to this client.")
    k2.metric("Replies received",   kpis["replies_received"],
              help="Inbound emails from this client scanned & AI-parsed.")
    k3.metric("Extension requests", kpis["extension_requests"],
              help="PTP events for this client — commitments to pay AFTER "
                   "the invoice due date.")
    k4.metric("Avg days extended",
              f"{kpis['avg_days_extended']:.1f}d"
              if kpis["avg_days_extended"] else "—",
              help="Mean gap between previous due date and each new "
                   "promised date, across every PTP by this client.")

    last = kpis.get("latest_commitment")
    if last:
        badge = ("PTP" if last["is_ptp"] else "confirmation only")
        badge_color = COLOR_BAD if last["is_ptp"] else COLOR_MUTED
        st.markdown(
            f"<div style='background:rgba(30,30,36,0.5); "
            f"border-left:3px solid {badge_color}; padding:10px 14px; "
            f"border-radius:6px; margin:8px 0;'>"
            f"<div style='color:#8a8880; font-size:11px; "
            f"text-transform:uppercase; letter-spacing:1.5px;'>"
            f"Latest commitment · {badge}</div>"
            f"<div style='color:#f0eee5; margin-top:6px;'>"
            f"{last['ai_summary'] or '—'}</div>"
            f"<div style='color:#8a8880; font-size:12px; margin-top:4px;'>"
            f"Promised: <b>{_fmt_date(last['ai_promised_date'])}</b> · "
            f"received {_fmt_date(last['received_at'])}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.divider()

    if not events:
        st.info("No communication captured yet for this client. Send a "
                "reminder from the Notification Center, then poll Gmail "
                "replies from Tab 3.")
        return

    st.markdown("**📜 Communication timeline** — oldest first")
    for e in events:
        at = _fmt_date(e.get("at"))
        # Show the Original Due + Latest PTP footer ONLY when a real PTP
        # exists — i.e. the invoice's latest_due_date or expected_payment_date
        # is different from its original_due_date.  Pure confirmations
        # ('yes will pay by due date') and outbound reminders on invoices
        # that never got extended have NO due-date footer.
        orig_raw   = e.get("original_due_date")
        latest_raw = e.get("expected_payment_date") or e.get("latest_due_date")
        has_real_ptp = bool(orig_raw and latest_raw and str(orig_raw)[:10]
                             != str(latest_raw)[:10])

        due_footer = ""
        if has_real_ptp:
            inv_num = e.get("invoice_number") or "—"
            due_footer = (
                f"<div style='color:#8a8880; font-size:11px; margin-top:6px; "
                f"padding-top:6px; border-top:1px dashed rgba(255,255,255,0.08);'>"
                f"Invoice <b>{inv_num}</b> · Original Due: "
                f"<b>{_fmt_date(orig_raw)}</b> · Latest PTP: "
                f"<b>{_fmt_date(latest_raw)}</b></div>"
            )

        if e["kind"] == "outbound":
            st.markdown(
                f"<div style='background:rgba(30,30,36,0.4); "
                f"border-left:3px solid {COLOR_GOLD}; padding:10px 14px; "
                f"border-radius:6px; margin:6px 0;'>"
                f"<div style='color:#8a8880; font-size:11px; "
                f"text-transform:uppercase; letter-spacing:1.5px;'>"
                f"→ Outbound · {at} · <i>{e.get('status')}</i></div>"
                f"<div style='color:#f0eee5; margin-top:6px;'>"
                f"<b>{(e.get('subject') or '')[:120]}</b></div>"
                f"<div style='color:#8a8880; font-size:12px; margin-top:4px;'>"
                f"Template: {e.get('template') or '—'}</div>"
                f"{due_footer}"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            label, color = CATEGORY_LABEL.get(e.get("ai_category") or "",
                                               ("—", COLOR_MUTED))
            ptp_flag = ("<span style='color:#c4614a;'> · PTP applied</span>"
                        if e.get("is_ptp") else "")
            promised = _fmt_date(e.get("ai_promised"))
            conf = e.get("confidence") or 0
            st.markdown(
                f"<div style='background:rgba(30,30,36,0.5); "
                f"border-left:3px solid {color}; padding:10px 14px; "
                f"border-radius:6px; margin:6px 0;'>"
                f"<div style='color:#8a8880; font-size:11px; "
                f"text-transform:uppercase; letter-spacing:1.5px;'>"
                f"← Inbound · {at} · {label}{ptp_flag}</div>"
                f"<div style='color:#f0eee5; margin-top:6px; font-size:13px;'>"
                f"{e.get('ai_summary') or '—'}</div>"
                f"<div style='color:#8a8880; font-size:12px; margin-top:4px;'>"
                f"Promised: <b>{promised}</b> · confidence {conf:.0%}</div>"
                f"{due_footer}"
                f"</div>",
                unsafe_allow_html=True,
            )
            with st.expander("Show original email", expanded=False):
                st.code((e.get("body") or "")[:2000] or "(empty)", language="text")


# ═══════════════════════════════════════════════════════════════════════════
#  TAB 3 — Gmail poll button (optional but the natural home)
# ═══════════════════════════════════════════════════════════════════════════
def render_tab3_reply_poll() -> None:
    if st.button("🔄 Scan Gmail for client replies",
                 help="Fetches new inbound emails from Gmail, extracts "
                      "commitments with the LLM, matches to invoices, and "
                      "applies PTP updates. Idempotent — messages already "
                      "ingested are skipped."):
        with st.spinner("Scanning Gmail and calling the LLM…"):
            result = poll_gmail_replies()
        if result.get("error"):
            st.error(f"Gmail fetch failed: {result['error']}")
        else:
            st.success(
                f"Fetched {result['fetched']}, processed {result['processed']} "
                f"new replies, {result.get('ptps', 0)} were promises-to-pay."
            )
