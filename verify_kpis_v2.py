"""
verify_kpis_v2.py — corrected reconciliation.

Fixes the flaw in v1 (which ignored the 'Void' exclusion the app applies) and
adds the real test: recompute DSO and On-time from the actual Payments sheet
instead of the last_modified_time proxy.

Run from E:\\cfo-copilot with the venv active:
    .\\venv\\Scripts\\python.exe verify_kpis_v2.py

Read-only. Paste the whole output back.
"""
import sys
sys.path.insert(0, ".")
try:
    from dotenv import load_dotenv; load_dotenv(".env")
except Exception:
    pass

import numpy as np
import pandas as pd
from core.excel_source import build_view, compute_kpis, XLSX_PATH

LINE = "-" * 70
def h(t): print("\n" + LINE + "\n" + t + "\n" + LINE)

print("Workbook:", XLSX_PATH)
v = build_view()
allinv = v.get("all_invoices", v["invoices"]).copy()
anchor = pd.Timestamp(v["anchor_date"])
k = compute_kpis()
print("Anchor date:", anchor.date(), "| total invoice rows:", len(allinv))

# ── 1. Balance reconciliation — the RIGHT filter (Balance>0 AND not Void) ─────
h("1. OPEN BALANCE — replicating the app's filter")
bpos   = allinv[allinv["Balance"].fillna(0) > 0]
void   = bpos[bpos["Invoice Status"] == "Void"]
openok = bpos[bpos["Invoice Status"] != "Void"]

print(f"  Balance>0 (all statuses)      : {len(bpos):>6}  rows   ₹{bpos['Balance'].sum():>18,.2f}")
print(f"  ...of which Void (excluded)   : {len(void):>6}  rows   ₹{void['Balance'].sum():>18,.2f}")
print(f"  Balance>0 & NOT Void  (=app)  : {len(openok):>6}  rows   ₹{openok['Balance'].sum():>18,.2f}")
print(f"  App compute_kpis reports      : {k['open_invoices']:>6}  rows   ₹{k['total_outstanding']:>18,.2f}")
match = (len(openok) == k["open_invoices"]
         and abs(openok["Balance"].sum() - k["total_outstanding"]) < 1)
print(f"  >>> {'RECONCILES — app is correct' if match else 'STILL OFF — investigate further'}")

# statuses present, and duplicate invoice numbers
print("\n  Invoice Status values present:")
print("   ", allinv["Invoice Status"].value_counts().to_dict())
dupes = openok["Invoice Number"].duplicated().sum()
print(f"  Duplicate Invoice Numbers within the open set: {dupes}"
      f"  {'(none — good)' if dupes == 0 else '(line-item rows? would double-count)'}")

# ── 2. Aging on the correct open set → should sum to Total Outstanding ────────
h("2. AR AGING on the correct open set")
dp = (anchor - openok["Due Date"]).dt.days
buck = {
    "Current (not due)": openok["Balance"][dp < 0].sum(),
    "1-30":  openok["Balance"][(dp >= 0)  & (dp <= 30)].sum(),
    "31-60": openok["Balance"][(dp >= 31) & (dp <= 60)].sum(),
    "61-90": openok["Balance"][(dp >= 61) & (dp <= 90)].sum(),
    "90+":   openok["Balance"][dp > 90].sum(),
}
s = 0.0
for b, a in buck.items():
    a = float(a or 0); s += a
    print(f"  {b:20s} ₹{a:>18,.2f}")
print(f"  {'SUM':20s} ₹{s:>18,.2f}   vs Total Outstanding ₹{k['total_outstanding']:,.2f}"
      f"   {'OK' if abs(s - k['total_outstanding']) < 1 else '*** off ***'}")

# ── 3. DSO / ON-TIME from REAL payments vs the proxy ─────────────────────────
h("3. DSO / ON-TIME — real payments vs the last_modified_time proxy")
pay = v.get("payments", pd.DataFrame()).copy()
closed = allinv[allinv["Invoice Status"] == "Closed"].copy()
print(f"  Closed invoices: {len(closed)} | payment rows: {len(pay)}")

if pay.empty or "Invoice Number" not in pay.columns or "Date" not in pay.columns:
    print("  Payments sheet missing expected columns — cannot recompute. Columns:")
    print("   ", list(pay.columns))
else:
    pay["Date"] = pd.to_datetime(pay["Date"], errors="coerce")
    # settlement date = last payment applied to each invoice
    settled = pay.groupby("Invoice Number")["Date"].max()
    closed["paid_date"] = closed["Invoice Number"].map(settled)
    matched = closed["paid_date"].notna()
    cov = matched.mean() * 100
    print(f"  Closed invoices matched to a real payment: {matched.sum()}/{len(closed)}  ({cov:.1f}%)")

    m = closed[matched].copy()
    days_real = (m["paid_date"] - m["Invoice Date"]).dt.days
    days_real = days_real[days_real.between(0, 365)]
    dso_real  = float(days_real.mean()) if len(days_real) else float("nan")
    ontime_real = 100.0 * (m["paid_date"] <= m["Due Date"]).sum() / len(m)

    print(f"\n  {'':22s}{'PROXY (app)':>16}{'REAL payments':>16}")
    print(f"  {'DSO (days)':22s}{k['dso_days']:>16.1f}{dso_real:>16.1f}")
    print(f"  {'On-time %':22s}{k['on_time_rate']:>16.1f}{ontime_real:>16.1f}")
    gap = abs((k['on_time_rate'] or 0) - ontime_real)
    print(f"\n  On-time gap between proxy and real payments: {gap:.1f} points")
    print("  >>> " + ("Proxy is close enough." if gap < 5 else
                       "Proxy is materially off — DSO/On-time should use real payments."))

h("DONE — paste this whole output back.")
