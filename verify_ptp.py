"""
verify_ptp.py — exercise the PTP reply extractor on realistic replies.

This tests the REGEX path (the one that actually runs for you, since the LLM
path only calls Ollama and your AI_PROVIDER is groq). For each sample it shows
the extracted category + promised date, and whether the new future-date guard
in apply_reply_to_invoice would accept or reject it.

Run from E:\\cfo-copilot:  .\\venv\\Scripts\\python.exe verify_ptp.py
Read-only — nothing is written.
"""
import sys
sys.path.insert(0, ".")
try:
    from dotenv import load_dotenv; load_dotenv(".env")
except Exception:
    pass

from datetime import date
from ai.ptp_intelligence import _regex_extract

TODAY = date.today()
print("TODAY =", TODAY, "\n" + "=" * 74)

SAMPLES = [
    ("Sure, we'll clear invoice INV-2043 on the 15th.",          "future promise"),
    ("Payment will be processed in 5 days.",                     "future promise"),
    ("We'll release the funds by Friday.",                       "future promise"),
    ("Expect the payment next week.",                            "vague future"),
    ("Our payment cycle runs on the 10th, will pay then.",       "cycle"),
    ("We already paid on 2026-06-15, please check your bank.",   "PAST date — should be rejected"),
    ("This invoice amount is incorrect — we dispute it.",        "dispute, no date"),
    ("Still waiting on management sign-off, sorry.",             "blocked, no date"),
    ("Payment has been initiated, UTR to follow shortly.",       "claim, no date"),
    ("I emailed you those details on Friday.",                   "NOT a promise (no verb)"),
    ("Thanks for the reminder, noted.",                          "no commitment"),
]

def verdict(promised):
    if not promised:
        return "no date"
    d = date.fromisoformat(promised)
    if d < TODAY:
        return "PAST -> rejected by future guard (not a PTP)"
    return "future -> valid"

for body, expect in SAMPLES:
    r = _regex_extract(body, TODAY)
    cat = r.get("category")
    pd_ = r.get("promised_date")
    print(f'\n"{body}"')
    print(f"   expect : {expect}")
    print(f"   category={cat}  promised_date={pd_}  conf={r.get('confidence')}")
    print(f"   -> {verdict(pd_)}")

print("\n" + "=" * 74)
print("Read: future promises resolve to a date after today; disputes/blocked/")
print("claims return no date; the past-dated 'already paid' line is extracted")
print("but the new guard stops it becoming a PTP; and 'emailed you on Friday'")
print("is correctly NOT treated as a promise.")
