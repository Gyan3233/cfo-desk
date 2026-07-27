"""
run_daily_scan.py — standalone Gmail-scan runner for Windows Task Scheduler.

Usage
-----
This is called by Windows Task Scheduler at the daily time (11 AM IST by
default) INDEPENDENTLY of Streamlit — so the scan runs even if the app
isn't open.

Setup (one-time)
----------------
1. Save this file next to app.py in your backend/ folder.
2. Run register_task.ps1 (in the same folder) as Administrator ONCE to
   register the scheduled task with Windows.  See that file for details.

What it does
------------
- Loads .env from the current folder (or one directory up).
- Imports poll_gmail_replies() from ptp_intelligence and calls it.
- Writes a line to daily_scan.log next to this file so you can see
  whether the scan ran, even without opening Streamlit.
- Exits 0 on success, 1 on failure (Windows Task Scheduler shows this
  in the History tab).

Manual test
-----------
Open PowerShell in the backend/ folder, activate the venv, and run:

    python run_daily_scan.py

You should see output like:
    ✅ Scan complete: fetched 5, processed 3 new, 1 PTPs
Or:
    ❌ Scan failed: <error>

Then check daily_scan.log — same message + timestamp.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Load .env from THIS folder or the parent (matches how Streamlit resolves it).
try:
    from dotenv import load_dotenv
    HERE = Path(__file__).resolve().parent
    for candidate in [HERE / ".env", HERE.parent / ".env"]:
        if candidate.exists():
            load_dotenv(candidate)
            break
except ImportError:
    pass

# Add project root to sys.path so `from ai.ptp_intelligence import ...` works
import sys as _sys
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))

LOG_FILE = Path(__file__).resolve().parent / "daily_scan.log"


def _log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def main() -> int:
    try:
        # Import at call time so we can log import errors cleanly
        from ai.ptp_intelligence import poll_gmail_replies
    except Exception as e:
        _log(f"❌ Import failed — is the venv active? {type(e).__name__}: {e}")
        return 1

    try:
        result = poll_gmail_replies()
    except Exception as e:
        _log(f"❌ Scan raised: {type(e).__name__}: {e}")
        return 1

    if result.get("error"):
        _log(f"❌ Scan returned error: {result['error']}")
        return 1

    _log(
        f"✅ Scan complete: fetched {result.get('fetched', 0)}, "
        f"processed {result.get('processed', 0)} new, "
        f"{result.get('ptps', 0)} PTPs, "
        f"across {result.get('targeted_clients', 0)} known clients."
    )
    if result.get("note"):
        _log(f"   Note: {result['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
