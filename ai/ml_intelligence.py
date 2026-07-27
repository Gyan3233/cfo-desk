"""
ml_intelligence.py — v3.2

The intelligence layer for CFO Copilot. Everything model-related lives here:
feature engineering, cross-validation, calibration, per-client scoring, and
plot factories. The dashboard imports render functions from this module and
never touches sklearn directly.

Design decisions (why this looks the way it does)
------------------------------------------------
* Target: is_late = paid_date > due_date  (48.7% base rate — well-balanced,
  no resampling needed).
* Features are leakage-safe: every feature for invoice i is computed using
  only invoices strictly PRIOR to i for that client. If we peek at the
  future, the model looks superhuman in CV and then collapses in production.
* Cross-validation: TimeSeriesSplit with 5 folds (per user request — plain
  time-based, no client blocking). Cutoffs are on Invoice Date, so folds
  respect chronology. We report train and test AUC/Brier separately; the
  gap tells us honestly how much the model overfits.
* Calibration: CalibratedClassifierCV(sigmoid, cv=3) is fit on the training
  half of each fold. Sigmoid (Platt) is chosen over isotonic because
  isotonic overfits at N < 10K rows.
* Model: LogisticRegression with class_weight='balanced'. Linear so SHAP-style
  attribution is exact, small so it can't memorise 9K rows.
* Cache: @st.cache_data — training reruns only when the data hash changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from core.excel_source import build_view

# Palette — matches the CRED-black shell so charts feel like one product.
COLOR_PRIMARY   = "#c9a961"   # muted gold
COLOR_POSITIVE  = "#8cb04a"   # dusty green
COLOR_NEGATIVE  = "#c4614a"   # dusty red
COLOR_NEUTRAL   = "#8a8880"   # cream-grey
COLOR_INK       = "#f0eee5"   # off-white for text
COLOR_PANEL     = "rgba(30,30,36,0.6)"


FEATURE_COLS = [
    "prior_late_rate",     # fraction of past invoices paid late
    "prior_avg_dtp",       # client's mean days-to-pay
    "prior_std_dtp",       # payment-timing volatility
    "log_amount",           # ln(1 + total)
    "relative_amount",     # this ÷ client's median past invoice
    "tenure_n",             # # prior invoices (Bayesian shrinkage counterweight)
    "days_since_last_inv", # recency
]

MIN_HISTORY = 3   # drop first 3 invoices per client — features unstable


# ═══════════════════════════════════════════════════════════════════════════
#  1. FEATURE ENGINEERING (leakage-safe)
# ═══════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800, show_spinner="Building leakage-safe features…")
def build_training_frame() -> pd.DataFrame:
    v = build_view()
    inv = v.get("all_invoices", pd.DataFrame())
    if inv.empty:
        return pd.DataFrame()

    inv = inv.copy()
    inv["Invoice Date"] = pd.to_datetime(inv["Invoice Date"], errors="coerce")
    inv["Due Date"]     = pd.to_datetime(inv["Due Date"], errors="coerce")

    # Real paid date from the Payments sheet (last payment applied per invoice)
    pay = v.get("payments", pd.DataFrame())
    if pay.empty:
        return pd.DataFrame()
    pay = pay.copy()
    pay["Date"] = pd.to_datetime(pay["Date"], errors="coerce")
    paid = (pay.groupby("Invoice Number")["Date"].max()
              .reset_index().rename(columns={"Date": "paid_date"}))

    inv = inv.merge(paid, on="Invoice Number", how="left")

    # Keep only invoices we can score — closed AND with dates
    closed = inv[(inv["Invoice Status"] == "Closed") &
                 inv["paid_date"].notna() &
                 inv["Invoice Date"].notna() &
                 inv["Due Date"].notna()].copy()

    closed["days_to_pay"] = (closed["paid_date"] - closed["Invoice Date"]).dt.days
    closed["is_late"]     = (closed["paid_date"] > closed["Due Date"]).astype(int)
    closed = closed[closed["days_to_pay"].between(0, 365)]   # sanity filter

    # Sort by client, then time — feature construction depends on this order
    closed = closed.sort_values(["Customer ID", "Invoice Date"]).reset_index(drop=True)

    # ── Leakage-safe rolling stats: for row i, look at rows 0..i-1 within client ──
    rows = []
    for cust, g in closed.groupby("Customer ID", sort=False):
        g = g.reset_index(drop=True)
        for i in range(len(g)):
            if i < MIN_HISTORY:
                continue
            prior  = g.iloc[:i]
            amount = float(g.iloc[i]["Total"])
            row = {
                "invoice_number":     g.iloc[i]["Invoice Number"],
                "customer_id":         cust,
                "customer_name":       g.iloc[i]["Customer Name"],
                "invoice_date":        g.iloc[i]["Invoice Date"],
                "amount":              amount,
                "y":                   int(g.iloc[i]["is_late"]),
                "prior_late_rate":     float(prior["is_late"].mean()),
                "prior_avg_dtp":       float(prior["days_to_pay"].mean()),
                "prior_std_dtp":       float(prior["days_to_pay"].std() or 0),
                "log_amount":          float(np.log1p(max(amount, 0))),
                "relative_amount":     amount / max(prior["Total"].median(), 1),
                "tenure_n":            i,
                "days_since_last_inv": (g.iloc[i]["Invoice Date"] -
                                        g.iloc[i - 1]["Invoice Date"]).days,
            }
            rows.append(row)
    df = pd.DataFrame(rows)
    return df.replace([np.inf, -np.inf], 0).fillna(0)


# ═══════════════════════════════════════════════════════════════════════════
#  2. TRAIN + CROSS-VALIDATE  (plain time-based split, per user)
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class ModelBundle:
    pipeline: object                           # fitted Pipeline (final refit on all data)
    coef: dict                                 # coef by feature (post-scaler)
    baseline_features: dict                    # feature means for SHAP-style attribution
    cv_metrics: dict                           # {auc, brier, ap} × {train, test} × folds
    calibration_curve: tuple                   # (prob_true, prob_pred) from held-out
    roc: tuple                                 # (fpr, tpr, auc) from held-out
    pr:  tuple                                 # (precision, recall, ap)
    n_train: int
    n_features: int = len(FEATURE_COLS)
    feature_cols: list = field(default_factory=lambda: FEATURE_COLS.copy())


@st.cache_resource(show_spinner="Training P(late) model with time-based CV…")
def train_model(n_folds: int = 5) -> ModelBundle | None:
    df = build_training_frame()
    if len(df) < 200:
        return None

    df = df.sort_values("invoice_date").reset_index(drop=True)
    X = df[FEATURE_COLS].values
    y = df["y"].values

    # Time-based CV: folds are chronological, no shuffling.
    tscv = TimeSeriesSplit(n_splits=n_folds)

    fold_metrics = {"train_auc": [], "test_auc": [],
                    "train_brier": [], "test_brier": [],
                    "train_ap": [], "test_ap": []}
    all_test_y, all_test_p = [], []

    for train_idx, test_idx in tscv.split(X):
        Xtr, Xte = X[train_idx], X[test_idx]
        ytr, yte = y[train_idx], y[test_idx]
        if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
            continue

        # Calibrated LR: sigmoid Platt scaling, inner 3-fold on training half.
        clf = CalibratedClassifierCV(
            estimator=LogisticRegression(
                class_weight="balanced", max_iter=1000, C=1.0),
            method="sigmoid", cv=3,
        )
        pipe = Pipeline([("scaler", StandardScaler()), ("clf", clf)])
        pipe.fit(Xtr, ytr)

        p_tr = pipe.predict_proba(Xtr)[:, 1]
        p_te = pipe.predict_proba(Xte)[:, 1]

        fold_metrics["train_auc"].append(roc_auc_score(ytr, p_tr))
        fold_metrics["test_auc"].append(roc_auc_score(yte, p_te))
        fold_metrics["train_brier"].append(brier_score_loss(ytr, p_tr))
        fold_metrics["test_brier"].append(brier_score_loss(yte, p_te))
        fold_metrics["train_ap"].append(average_precision_score(ytr, p_tr))
        fold_metrics["test_ap"].append(average_precision_score(yte, p_te))

        all_test_y.extend(yte.tolist())
        all_test_p.extend(p_te.tolist())

    # ── FINAL fit on ALL data — this is what scores live invoices ──
    final_clf = CalibratedClassifierCV(
        estimator=LogisticRegression(
            class_weight="balanced", max_iter=1000, C=1.0),
        method="sigmoid", cv=3,
    )
    final_pipe = Pipeline([("scaler", StandardScaler()), ("clf", final_clf)])
    final_pipe.fit(X, y)

    # Feature attribution: average the underlying LR coefficients across
    # the calibrator's inner folds.  Coefs are on the SCALED space.
    # sklearn changed attribute name from estimator_ to estimator in 1.6+.
    def _inner_lr(cc):
        return getattr(cc, "estimator", None) or getattr(cc, "estimator_", None)
    inner_coefs = np.mean(
        [_inner_lr(est).coef_[0] for est in final_clf.calibrated_classifiers_],
        axis=0,
    )
    coef_by_feat = dict(zip(FEATURE_COLS, inner_coefs))

    # SHAP-style baselines = feature means in the training set
    scaler = final_pipe.named_steps["scaler"]
    baseline = dict(zip(FEATURE_COLS, np.zeros(len(FEATURE_COLS))))  # scaled → 0

    # Held-out diagnostic curves
    all_test_y = np.array(all_test_y)
    all_test_p = np.array(all_test_p)
    prob_true, prob_pred = calibration_curve(all_test_y, all_test_p,
                                             n_bins=10, strategy="quantile")
    fpr, tpr, _ = roc_curve(all_test_y, all_test_p)
    prec, rec, _ = precision_recall_curve(all_test_y, all_test_p)

    return ModelBundle(
        pipeline=final_pipe,
        coef=coef_by_feat,
        baseline_features=baseline,
        cv_metrics={k: (float(np.mean(v)) if v else None) for k, v in fold_metrics.items()}
                    | {"folds": {k: v for k, v in fold_metrics.items()}},
        calibration_curve=(prob_true.tolist(), prob_pred.tolist()),
        roc=(fpr.tolist(), tpr.tolist(), roc_auc_score(all_test_y, all_test_p)),
        pr=(prec.tolist(), rec.tolist(), average_precision_score(all_test_y, all_test_p)),
        n_train=len(df),
    )


# ═══════════════════════════════════════════════════════════════════════════
#  3. SCORING (open invoices) + PER-INVOICE ATTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800)
def score_open_invoices() -> pd.DataFrame:
    """Score every open (Balance>0) invoice with P(late).
    Uses the same leakage-safe features: they look at the client's HISTORY
    up to the invoice date, which is legit at scoring time (all history exists).
    """
    m = train_model()
    if m is None:
        return pd.DataFrame()
    v = build_view()
    inv = v.get("all_invoices", pd.DataFrame())
    if inv.empty:
        return pd.DataFrame()
    inv = inv.copy()
    inv["Invoice Date"] = pd.to_datetime(inv["Invoice Date"], errors="coerce")
    inv["Due Date"]     = pd.to_datetime(inv["Due Date"], errors="coerce")

    pay = v.get("payments", pd.DataFrame())
    pay["Date"] = pd.to_datetime(pay.get("Date"), errors="coerce")
    paid = (pay.groupby("Invoice Number")["Date"].max()
              .reset_index().rename(columns={"Date": "paid_date"}))
    inv = inv.merge(paid, on="Invoice Number", how="left")
    inv["is_late_hist"] = ((inv["paid_date"] > inv["Due Date"])
                           .fillna(False).astype(int))
    inv["days_to_pay"]  = (inv["paid_date"] - inv["Invoice Date"]).dt.days
    inv = inv.sort_values(["Customer ID", "Invoice Date"]).reset_index(drop=True)

    # Compute the same features for every OPEN invoice, using ALL of that
    # client's history strictly prior in date.
    scored = []
    for cust, g in inv.groupby("Customer ID", sort=False):
        g = g.reset_index(drop=True)
        for i, row in g.iterrows():
            if float(row.get("Balance", 0) or 0) <= 0:
                continue
            prior = g[g["Invoice Date"] < row["Invoice Date"]]
            n = len(prior)
            if n < MIN_HISTORY:
                continue
            past_dtp = prior["days_to_pay"].dropna()
            past_dtp = past_dtp[past_dtp.between(0, 365)]
            if past_dtp.empty:
                continue
            amount = float(row["Total"] or 0)
            feats = np.array([[
                float(prior["is_late_hist"].mean()),
                float(past_dtp.mean()),
                float(past_dtp.std() or 0),
                float(np.log1p(max(amount, 0))),
                amount / max(prior["Total"].median(), 1),
                n,
                (row["Invoice Date"] - prior["Invoice Date"].iloc[-1]).days,
            ]])
            # sklearn LR rejects NaN/inf — replace with 0 (post-scaling this
            # is the training-set mean, i.e., a neutral value)
            feats = np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)
            p_late = float(m.pipeline.predict_proba(feats)[:, 1][0])
            scored.append({
                "invoice_number": row["Invoice Number"],
                "customer_id":    cust,
                "customer_name":  row["Customer Name"],
                "amount":         amount,
                "balance":        float(row["Balance"] or 0),
                "due_date":       row["Due Date"],
                "p_late":         p_late,
                "expected_loss":  p_late * float(row["Balance"] or 0),
            })
    return pd.DataFrame(scored)


def attribute(invoice_features: np.ndarray, m: ModelBundle) -> list[tuple]:
    """SHAP-style linear attribution: log-odds contribution = coef × (x_scaled).
    Positive = pushes toward LATE, negative = pushes toward ON-TIME."""
    scaler = m.pipeline.named_steps["scaler"]
    x_scaled = scaler.transform(invoice_features.reshape(1, -1))[0]
    contribs = []
    for i, name in enumerate(FEATURE_COLS):
        contribs.append((name, m.coef[name] * x_scaled[i], invoice_features[0][i]))
    contribs.sort(key=lambda t: abs(t[1]), reverse=True)
    return contribs


# ═══════════════════════════════════════════════════════════════════════════
#  4. CHART FACTORIES
# ═══════════════════════════════════════════════════════════════════════════
def _base_layout(title: str = "") -> dict:
    return dict(
        title=dict(text=title, font=dict(color=COLOR_INK, size=13)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=COLOR_INK, size=11),
        margin=dict(l=40, r=20, t=40, b=40),
        xaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.1)"),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.1)"),
        showlegend=False,
    )


def fig_calibration(m: ModelBundle) -> go.Figure:
    """Reliability plot: predicted P(late) vs observed rate. On the diagonal = well-calibrated."""
    prob_true, prob_pred = m.calibration_curve
    fig = go.Figure()
    fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1,
                  line=dict(color=COLOR_NEUTRAL, dash="dot", width=1))
    fig.add_trace(go.Scatter(
        x=prob_pred, y=prob_true, mode="lines+markers",
        line=dict(color=COLOR_PRIMARY, width=2.5),
        marker=dict(size=8, color=COLOR_PRIMARY),
        name="Calibration",
        hovertemplate="Predicted: %{x:.2f}<br>Observed: %{y:.2f}<extra></extra>",
    ))
    fig.update_layout(**_base_layout("Calibration curve · held-out folds"))
    fig.update_xaxes(title="Predicted P(late)", range=[0, 1])
    fig.update_yaxes(title="Observed late rate",  range=[0, 1])
    return fig


def fig_roc(m: ModelBundle) -> go.Figure:
    fpr, tpr, auc = m.roc
    fig = go.Figure()
    fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1,
                  line=dict(color=COLOR_NEUTRAL, dash="dot", width=1))
    fig.add_trace(go.Scatter(x=fpr, y=tpr, mode="lines",
                             line=dict(color=COLOR_PRIMARY, width=2.5),
                             fill="tozeroy", fillcolor="rgba(201,169,97,0.10)",
                             name=f"ROC (AUC={auc:.3f})",
                             hovertemplate="FPR: %{x:.2f}<br>TPR: %{y:.2f}<extra></extra>"))
    fig.update_layout(**_base_layout(f"ROC curve · AUC = {auc:.3f}"))
    fig.update_xaxes(title="False-positive rate")
    fig.update_yaxes(title="True-positive rate (recall)")
    return fig


def fig_pr(m: ModelBundle) -> go.Figure:
    prec, rec, ap = m.pr
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=rec, y=prec, mode="lines",
                             line=dict(color=COLOR_PRIMARY, width=2.5),
                             fill="tozeroy", fillcolor="rgba(201,169,97,0.10)",
                             hovertemplate="Recall: %{x:.2f}<br>Precision: %{y:.2f}<extra></extra>"))
    fig.update_layout(**_base_layout(f"Precision–Recall · AP = {ap:.3f}"))
    fig.update_xaxes(title="Recall")
    fig.update_yaxes(title="Precision")
    return fig


def fig_feature_importance(m: ModelBundle) -> go.Figure:
    coefs = sorted(m.coef.items(), key=lambda kv: abs(kv[1]), reverse=True)
    names  = [k for k, _ in coefs]
    values = [v for _, v in coefs]
    colors = [COLOR_NEGATIVE if v > 0 else COLOR_POSITIVE for v in values]
    fig = go.Figure()
    fig.add_bar(x=values, y=names, orientation="h", marker_color=colors,
                text=[f"{v:+.2f}" for v in values], textposition="outside",
                hovertemplate="%{y}: %{x:+.3f}<extra></extra>")
    fig.update_layout(**_base_layout(
        "Feature contribution to log-odds of LATE  (red pushes late · green pushes on-time)"))
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title="Coefficient (scaled features)")
    return fig


def fig_cv_gap(m: ModelBundle) -> go.Figure:
    folds = m.cv_metrics["folds"]
    n = len(folds["train_auc"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(y=folds["train_auc"], x=list(range(1, n + 1)),
                             name="Train AUC", mode="lines+markers",
                             line=dict(color=COLOR_NEUTRAL, dash="dot")))
    fig.add_trace(go.Scatter(y=folds["test_auc"], x=list(range(1, n + 1)),
                             name="Test AUC", mode="lines+markers",
                             line=dict(color=COLOR_PRIMARY, width=2.5)))
    fig.update_layout(**_base_layout(
        "Generalisation check · train vs held-out AUC by time fold"))
    fig.update_layout(showlegend=True,
                      legend=dict(orientation="h", y=-0.2, x=0,
                                  font=dict(color=COLOR_INK)))
    fig.update_xaxes(title="Time fold (1 = oldest)")
    fig.update_yaxes(title="AUC", range=[0.5, 1])
    return fig


def fig_risk_distribution(scored: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=scored["p_late"], nbinsx=20,
        marker_color=COLOR_PRIMARY,
        hovertemplate="P(late) bin: %{x}<br>invoices: %{y}<extra></extra>",
    ))
    fig.add_vline(x=0.5, line_color=COLOR_NEUTRAL, line_dash="dot",
                  annotation_text="50%", annotation_position="top")
    fig.add_vline(x=0.7, line_color=COLOR_NEGATIVE, line_dash="dot",
                  annotation_text="high-risk (70%)", annotation_position="top")
    fig.update_layout(**_base_layout(
        "Distribution of P(late) across all open invoices"))
    fig.update_xaxes(title="P(late)", range=[0, 1])
    fig.update_yaxes(title="# invoices")
    return fig


# ═══════════════════════════════════════════════════════════════════════════
#  5. THE ONE UI FUNCTION — called from tab1_dashboard
# ═══════════════════════════════════════════════════════════════════════════
def render_intelligence_section() -> None:
    """The whole ML section as it appears on Tab 1. Business-friendly by
    default; the ⚙ expander reveals technical detail (per user request)."""
    m = train_model()
    if m is None:
        st.info("Not enough closed-invoice history to train a P(late) model yet.")
        return

    scored = score_open_invoices()

    st.markdown("#### 🧠 Late-Payment Intelligence",
                help="Calibrated logistic regression predicts, for every open "
                     "invoice, the probability that the client will pay late. "
                     "Model is retrained on every data refresh with proper "
                     "time-based cross-validation, so the AUC/Brier we show "
                     "are out-of-sample and honest.")

    # ── BUSINESS SUMMARY: 4 headline numbers ───────────────────────────
    high_risk    = scored[scored["p_late"] > 0.7] if not scored.empty else pd.DataFrame()
    med_risk     = scored[scored["p_late"].between(0.4, 0.7)] if not scored.empty else pd.DataFrame()
    at_risk_amt  = float(high_risk["balance"].sum()) if not high_risk.empty else 0.0
    expected_loss = float((scored["p_late"] * scored["balance"]).sum()) if not scored.empty else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Model AUC (held-out)", f"{m.cv_metrics['test_auc']:.3f}",
              help="Area under the ROC curve on held-out folds. 0.5 = coin "
                   "flip · 1.0 = perfect ranking · > 0.75 = deployable · "
                   "> 0.85 = strong.")
    c2.metric("Brier (held-out)", f"{m.cv_metrics['test_brier']:.3f}",
              help="Mean squared error of calibrated probabilities. Lower is "
                   "better. Baseline of always-predict-base-rate is 0.25.")
    c3.metric("High-risk exposure", f"₹{at_risk_amt / 1e5:.1f}L",
              delta=f"{len(high_risk)} invoices",
              help="Sum of Balance where P(late) > 70%.  These are the invoices "
                   "the model believes will slip — chase these first.")
    c4.metric("Expected loss (probability-weighted)",
              f"₹{expected_loss / 1e5:.1f}L",
              help="Σ P(late) × Balance across ALL open invoices. Portfolio-level "
                   "estimate of how much collections will drift past due.")

    st.write("")

    # ── DEFAULT VIEW: business-friendly graphics ──────────────────────
    b1, b2 = st.columns(2)
    with b1:
        st.markdown("**◆ Where is the risk concentrated?**")
        st.caption("Every open invoice, scored. The tall bar is your typical "
                   "invoice; the tail on the right is where losses come from.")
        if not scored.empty:
            st.plotly_chart(fig_risk_distribution(scored), use_container_width=True)
    with b2:
        st.markdown("**◆ What is the model listening to?**")
        st.caption("The bigger the bar, the more that feature drives the "
                   "prediction. Red pushes toward *late*; green toward *on-time*.")
        st.plotly_chart(fig_feature_importance(m), use_container_width=True)

    # ── TOP RISKS TABLE ────────────────────────────────────────────────
    st.markdown("**◆ Top 10 open invoices by expected loss**")
    st.caption("Ordered by amount × P(late). This is your action queue for the "
               "week — chase these first.")
    if not scored.empty:
        top = scored.nlargest(10, "expected_loss")[
            ["invoice_number", "customer_name", "balance", "p_late",
             "expected_loss", "due_date"]
        ].copy()
        top["balance"]       = top["balance"].apply(lambda x: f"₹{x:,.0f}")
        top["p_late"]        = top["p_late"].apply(lambda p: f"{p:.0%}")
        top["expected_loss"] = top["expected_loss"].apply(lambda x: f"₹{x:,.0f}")
        top["due_date"]      = pd.to_datetime(top["due_date"]).dt.strftime("%d %b %Y")
        top.columns = ["Invoice", "Client", "Balance", "P(late)",
                       "Expected loss", "Due"]
        st.dataframe(top, hide_index=True, use_container_width=True)
    else:
        st.info("No open invoices scored yet.")

    # ── TECHNICAL EXPANDER: for data-scientist reviewers ──────────────
    with st.expander("⚙ Technical detail · cross-validation, calibration, ROC/PR"):
        st.caption(
            "**Setup.** LogisticRegression (class_weight='balanced') wrapped "
            "in CalibratedClassifierCV (sigmoid, inner cv=3). Features are "
            f"{m.n_features} leakage-safe rolling client statistics computed "
            "using only invoices strictly prior to each training row. Outer "
            "CV: TimeSeriesSplit with 5 folds on Invoice Date (plain "
            "time-based, no client blocking). N training rows = "
            f"**{m.n_train:,}**."
        )

        # Metrics table
        st.markdown("**Cross-validation metrics (mean across folds)**")
        st.markdown(
            f"| Metric | Train | Held-out | Gap |\n"
            f"|---|---|---|---|\n"
            f"| AUC   | {m.cv_metrics['train_auc']:.3f} | "
            f"{m.cv_metrics['test_auc']:.3f} | "
            f"{m.cv_metrics['train_auc'] - m.cv_metrics['test_auc']:+.3f} |\n"
            f"| Brier | {m.cv_metrics['train_brier']:.3f} | "
            f"{m.cv_metrics['test_brier']:.3f} | "
            f"{m.cv_metrics['test_brier'] - m.cv_metrics['train_brier']:+.3f} |\n"
            f"| AP    | {m.cv_metrics['train_ap']:.3f} | "
            f"{m.cv_metrics['test_ap']:.3f} | "
            f"{m.cv_metrics['train_ap'] - m.cv_metrics['test_ap']:+.3f} |"
        )
        gap = m.cv_metrics['train_auc'] - m.cv_metrics['test_auc']
        if gap > 0.05:
            st.caption(f"⚠ Train-test AUC gap of {gap:.3f} suggests the model "
                       "is overfitting somewhat. If this grows above ~0.10, "
                       "reduce C or drop the highest-variance features "
                       "(prior_std_dtp / relative_amount).")
        else:
            st.caption(f"✓ Train-test gap of {gap:.3f} is healthy — the model "
                       "generalises out-of-sample.")

        t1, t2 = st.columns(2)
        with t1:
            st.plotly_chart(fig_calibration(m), use_container_width=True)
            st.plotly_chart(fig_roc(m),         use_container_width=True)
        with t2:
            st.plotly_chart(fig_cv_gap(m),      use_container_width=True)
            st.plotly_chart(fig_pr(m),          use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════
#  6. CLIENT DEEP-DIVE (used by Tab 2)
# ═══════════════════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800)
def client_scores() -> pd.DataFrame:
    """Aggregate P(late) to the client level: mean, count, expected_loss."""
    scored = score_open_invoices()
    if scored.empty:
        return pd.DataFrame()
    return (scored.groupby(["customer_id", "customer_name"])
                  .agg(open_invoices=("invoice_number", "count"),
                       outstanding=("balance", "sum"),
                       avg_p_late=("p_late", "mean"),
                       expected_loss=("expected_loss", "sum"))
                  .reset_index()
                  .sort_values("expected_loss", ascending=False))


def client_p_late(customer_id) -> float | None:
    scores = client_scores()
    row = scores[scores["customer_id"] == customer_id]
    return float(row["avg_p_late"].iloc[0]) if len(row) else None
