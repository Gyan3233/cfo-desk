"""
verify_ml.py — audit the P(late) reliability model.

Checks, on YOUR data:
  1. Data + class balance
  2. Real signal: out-of-fold AUC / Brier / AP, and the train-vs-test gap
  3. Leakage sanity: shuffle the target — honest AUC must collapse to ~0.50
  4. Calibration error on held-out folds
  5. Train/serve skew: how many scored invoices have unpaid priors that get
     counted as 'on-time', biasing prior_late_rate down

Run from E:\\cfo-copilot:  .\\venv\\Scripts\\python.exe verify_ml.py
Read-only.
"""
import sys
sys.path.insert(0, ".")
try:
    from dotenv import load_dotenv; load_dotenv(".env")
except Exception:
    pass

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.metrics import roc_auc_score, brier_score_loss, average_precision_score

from ai.ml_intelligence import build_training_frame, train_model, FEATURE_COLS, MIN_HISTORY
from core.excel_source import build_view

L = "-" * 68
def h(t): print("\n" + L + "\n" + t + "\n" + L)


def _cv_auc(X, y, shuffle_y=False, seed=0):
    if shuffle_y:
        rng = np.random.default_rng(seed)
        y = y.copy(); rng.shuffle(y)
    tscv = TimeSeriesSplit(n_splits=5)
    aucs = []
    for tr, te in tscv.split(X):
        if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
            continue
        pipe = Pipeline([("s", StandardScaler()),
                         ("c", CalibratedClassifierCV(
                             LogisticRegression(class_weight="balanced",
                                                max_iter=1000, C=1.0),
                             method="sigmoid", cv=3))])
        pipe.fit(X[tr], y[tr])
        aucs.append(roc_auc_score(y[te], pipe.predict_proba(X[te])[:, 1]))
    return float(np.mean(aucs)) if aucs else float("nan")


df = build_training_frame()
h("1. TRAINING DATA")
if df.empty or len(df) < 200:
    print("  Not enough data to train (need >=200 rows). Rows:", len(df))
    sys.exit(0)
print(f"  rows: {len(df)} | clients: {df['customer_id'].nunique()} "
      f"| is_late rate: {df['y'].mean()*100:.1f}%")

X = df[FEATURE_COLS].values.astype(float)
y = df["y"].values.astype(int)

h("2. MODEL SIGNAL (out-of-fold)")
m = train_model()
cv = m.cv_metrics
print(f"  test AUC : {cv['test_auc']:.3f}   (0.5 = coin flip, ~1.0 = suspicious)")
print(f"  train AUC: {cv['train_auc']:.3f}   gap: {cv['train_auc']-cv['test_auc']:+.3f} (large gap = overfit)")
print(f"  test Brier (lower better): {cv['test_brier']:.3f}")
print(f"  test avg-precision       : {cv['test_ap']:.3f}  (base rate {y.mean():.3f})")

h("3. LEAKAGE SANITY — shuffle the target, AUC must fall to ~0.50")
real = _cv_auc(X, y, shuffle_y=False)
shuf = np.mean([_cv_auc(X, y, shuffle_y=True, seed=s) for s in range(3)])
print(f"  real target   OOF AUC: {real:.3f}")
print(f"  shuffled target AUC  : {shuf:.3f}")
print("  >>> " + ("OK — shuffled collapses to chance, no leakage."
                  if shuf < 0.58 else
                  "WARNING — shuffled AUC still high; possible leakage."))

h("4. CALIBRATION (held-out)")
pt, pp = m.calibration_curve
pt, pp = np.array(pt), np.array(pp)
ece = float(np.mean(np.abs(pt - pp)))
print(f"  mean |predicted - observed| across bins: {ece:.3f}  (lower = better)")
for a, b in zip(pp, pt):
    print(f"      predicted {a:5.2f}  ->  observed {b:5.2f}")

h("5. TRAIN/SERVE SKEW — unpaid priors counted as 'on-time' at scoring")
v = build_view()
inv = v.get("all_invoices", pd.DataFrame()).copy()
inv["Invoice Date"] = pd.to_datetime(inv["Invoice Date"], errors="coerce")
pay = v.get("payments", pd.DataFrame()).copy()
pay["Date"] = pd.to_datetime(pay.get("Date"), errors="coerce")
paid = pay.groupby("Invoice Number")["Date"].max().reset_index()
paid.columns = ["Invoice Number", "paid_date"]
inv = inv.merge(paid, on="Invoice Number", how="left")
inv = inv.sort_values(["Customer ID", "Invoice Date"]).reset_index(drop=True)

open_scored = unpaid_prior = 0
for cust, g in inv.groupby("Customer ID", sort=False):
    g = g.reset_index(drop=True)
    for _, row in g.iterrows():
        if float(row.get("Balance", 0) or 0) <= 0:
            continue
        prior = g[g["Invoice Date"] < row["Invoice Date"]]
        if len(prior) < MIN_HISTORY:
            continue
        open_scored += 1
        if prior["paid_date"].isna().any():        # has unpaid priors
            unpaid_prior += 1
print(f"  open invoices scored           : {open_scored}")
print(f"  ...with >=1 unpaid prior invoice: {unpaid_prior} "
      f"({(unpaid_prior/open_scored*100 if open_scored else 0):.0f}%)")
print("  These are the ones whose prior_late_rate is biased down (risk under-stated).")

h("DONE — paste this whole output back.")
