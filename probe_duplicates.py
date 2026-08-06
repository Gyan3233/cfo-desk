"""
probe_duplicates.py — is Total Outstanding double-counted?

The reconciliation found repeated Invoice Numbers in the open set. This tells
us whether those repeats carry the SAME balance (double-count bug) or DIFFERENT
balances (legitimate line charges — summing is fine).

Run from E:\\cfo-copilot:  .\\venv\\Scripts\\python.exe probe_duplicates.py
Read-only.
"""
import sys
sys.path.insert(0, ".")
try:
    from dotenv import load_dotenv; load_dotenv(".env")
except Exception:
    pass

import pandas as pd
from core.excel_source import build_view

v = build_view()
inv = v["all_invoices"]
openok = inv[(inv["Balance"].fillna(0) > 0) & (inv["Invoice Status"] != "Void")]

n_rows = len(openok)
n_uniq = openok["Invoice Number"].nunique()
print(f"Open rows: {n_rows}  |  unique Invoice Numbers: {n_uniq}  |  duplicate rows: {n_rows - n_uniq}")

vc = openok["Invoice Number"].value_counts()
dups = vc[vc > 1]
print(f"Invoice Numbers appearing more than once: {len(dups)}  (max repeats: {int(dups.max())})")

cols = [c for c in ["Invoice Number", "Customer Name", "Invoice Date",
                    "Due Date", "Total", "Balance", "Invoice Status"]
        if c in openok.columns]

print("\n--- A few repeated invoice numbers, all their rows ---")
for num in dups.index[:4]:
    print(f"\nInvoice Number: {num}")
    print(openok[openok["Invoice Number"] == num][cols].to_string(index=False))

# The decisive test: within each repeated invoice number, is the Balance the same?
gb = openok.groupby("Invoice Number")["Balance"].nunique()
identical = gb[dups.index] == 1
print("\n" + "=" * 62)
print(f"Of the {len(dups)} repeated invoice numbers, "
      f"{identical.mean()*100:.0f}% have an IDENTICAL balance on every row.")
same_cust = (openok.groupby("Invoice Number")["Customer Name"].nunique()[dups.index] == 1).mean()*100
print(f"{same_cust:.0f}% also share the same customer across the repeats.")
print("=" * 62)

# What the total would be if we collapsed duplicates to one row each
dedup_total = openok.drop_duplicates("Invoice Number")["Balance"].sum()
print(f"\nTotal Outstanding as summed now (all rows): ₹{openok['Balance'].sum():,.2f}")
print(f"Total if collapsed to 1 row per invoice #  : ₹{dedup_total:,.2f}")
print("\nIf these two differ a lot AND balances are identical per number,")
print("the current Total Outstanding is double-counting. If they're equal,")
print("or balances differ per row, the sum is fine.")
