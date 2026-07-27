# =============================================================================
#  register_task.ps1 — one-time setup for the daily CFO Copilot Gmail scan
# =============================================================================
#  This registers a Windows Scheduled Task that runs run_daily_scan.py every
#  day at the configured time, using your venv's Python.  The task runs
#  INDEPENDENTLY of Streamlit — even if the app isn't open, the scan happens.
#
#  Usage
#  -----
#  1. Open PowerShell as ADMINISTRATOR (right-click → Run as administrator).
#  2. cd into your backend folder:
#        cd "C:\Users\sampada.suryawanshi\Downloads\OneDrive_2026-06-17\CFO Agent 2\backend"
#  3. Allow this script to run (once):
#        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#  4. Run it:
#        .\register_task.ps1
#
#  Change the daily run time
#  -------------------------
#  Edit the $Hour and $Minute variables below.  Currently: 11:00 AM.
#  Re-run this script to update the schedule.
#
#  Verify it worked
#  ----------------
#  After running, open Task Scheduler (search "Task Scheduler" in Start),
#  find "CFO Copilot Daily Scan" in the top-level list.
#  Right-click → Run — the scan should execute immediately.
#  Check backend\daily_scan.log for the result.
#
#  Unregister
#  ----------
#  If you want to remove the scheduled task later:
#        Unregister-ScheduledTask -TaskName "CFO Copilot Daily Scan" -Confirm:$false
# =============================================================================

# ── Config ────────────────────────────────────────────────────────────────
$TaskName    = "CFO Copilot Daily Scan"
$Hour        = 11    # ← 24-hour clock; 11 = 11 AM.  Change to 6 for 6 AM, 14 for 2 PM.
$Minute      = 0
$Description = "Runs the CFO Copilot Gmail reply scan for PTP intake. Independent of Streamlit."

# ── Resolve paths ─────────────────────────────────────────────────────────
$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script   = Join-Path $Here "services\run_daily_scan.py"
$Venv     = Join-Path $Here "venv\Scripts\python.exe"

if (-not (Test-Path $Script)) {
    Write-Host "ERROR: services\run_daily_scan.py not found in $Here" -ForegroundColor Red
    Write-Host "This script must be in the project root (parent of services\)." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $Venv)) {
    Write-Host "ERROR: venv Python not found at $Venv" -ForegroundColor Red
    Write-Host "Adjust the `$Venv path in this script if your venv lives elsewhere." -ForegroundColor Yellow
    exit 1
}

# ── Build the action + trigger ────────────────────────────────────────────
$Action = New-ScheduledTaskAction `
    -Execute $Venv `
    -Argument "`"$Script`"" `
    -WorkingDirectory $Here

$Trigger = New-ScheduledTaskTrigger -Daily -At "$($Hour):$('{0:D2}' -f $Minute)"

# Run whether you're logged in or not; wake the PC if sleeping.
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -WakeToRun `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# ── Register (or update) ──────────────────────────────────────────────────
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Updating existing task '$TaskName' to run at $Hour`:$('{0:D2}' -f $Minute)..." -ForegroundColor Yellow
    Set-ScheduledTask -TaskName $TaskName `
        -Action $Action -Trigger $Trigger -Settings $Settings | Out-Null
} else {
    Write-Host "Registering task '$TaskName' to run at $Hour`:$('{0:D2}' -f $Minute) daily..." -ForegroundColor Green
    Register-ScheduledTask -TaskName $TaskName `
        -Action $Action -Trigger $Trigger -Settings $Settings `
        -Description $Description | Out-Null
}

Write-Host ""
Write-Host "✅ Done. The scan will run daily at $Hour`:$('{0:D2}' -f $Minute)." -ForegroundColor Green
Write-Host ""
Write-Host "To test it right now, run:" -ForegroundColor Cyan
Write-Host "    Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
Write-Host ""
Write-Host "Then check the log:" -ForegroundColor Cyan
Write-Host "    Get-Content .\daily_scan.log -Tail 10" -ForegroundColor White
Write-Host ""
Write-Host "To unregister later:" -ForegroundColor Cyan
Write-Host "    Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor White
