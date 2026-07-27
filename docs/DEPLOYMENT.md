# Deploying to Streamlit Community Cloud

Free public URL, GitHub push auto-deploys. Takes 15 minutes end-to-end.

## Prerequisites

- [ ] Repo pushed to GitHub (private repo is fine).
- [ ] `.env`, `credentials/`, `token.json`, and DB files are gitignored
      (verify with `git status` — they should NOT be listed).
- [ ] Groq API key from <https://console.groq.com> (Ollama can't run on
      Streamlit Cloud, so the chatbot needs cloud LLM access).

## Step 1 — Register the app

1. Go to <https://share.streamlit.io>.
2. Sign in with the same GitHub account that owns your repo.
3. Click **New app**.
4. Fill in:
   - **Repository:** `<YOUR-USERNAME>/cfo-copilot`
   - **Branch:** `main`
   - **Main file path:** `app/main.py`
5. Click **Advanced settings** → **Secrets**.

## Step 2 — Paste your secrets

In the Secrets textarea, paste this TOML with your REAL values:

```toml
ADMIN_EMAIL = "your.email@company.com"
JWT_SECRET = "your-real-32-char-random-secret-here"

SALES_XLSX = "data/Copy_of_Sales_Data-dummy.xlsx"
DB_PATH = "./invoices.db"
AUTH_DB_PATH = "./auth.db"

CC_EMAIL = "sampada317@gmail.com"
SENDER_NAME = "Accounts Team"
SENDER_COMPANY = "Infrabeat Technologies"
REMINDER_BEFORE_DUE_DAYS = "3"
NOTIFY_LEAD_DAYS = "5"
REMINDER_COOLDOWN_DAYS = "5"

GROQ_API_KEY = "gsk_your_real_key_here"
GROQ_MODEL = "llama-3.3-70b-versatile"

SCAN_DAILY_HOUR = "11"
SCAN_DAILY_MINUTE = "0"
SCAN_DAYS_BACK = "30"
SCAN_MIN_INTERVAL_MIN = "60"
```

The app reads these into `os.environ` on startup, so every module that
already uses `os.getenv(...)` just works. No code changes needed.

## Step 3 — Deploy

Click **Deploy**. Streamlit installs dependencies from `requirements.txt`
and boots the app. First deploy takes 3-5 minutes. When it's up, your app
lives at `https://<repo-name>-<hash>.streamlit.app`.

Every subsequent `git push origin main` triggers an auto-redeploy in ~90
seconds.

## Cloud limitations you MUST know

### 1. Ephemeral filesystem
`invoices.db` and `auth.db` are wiped on every app restart (typically once
a day, or whenever Streamlit reboots your instance). This is fine for a
demo but means:

- Every day, first user has to sign up again as `ADMIN_EMAIL`.
- Reminder history / PTP events don't persist across restarts.

**Fix for real production:** move to Turso (managed SQLite) or Neon
(managed PostgreSQL) — both free tiers. Requires changing `DB_PATH` to a
connection string and swapping `sqlite3` for the relevant driver.
Estimated effort: 4-6 hours if you keep the schema identical.

### 2. Ollama can't run
There's no way to install Ollama on Streamlit Cloud. The chatbot and
PTP reply extraction both fall back to Groq automatically — that's why
the Groq key in Secrets is required.

### 3. No cron / background jobs after sleep
Streamlit Cloud sleeps inactive apps after ~30 minutes. When it wakes,
the APScheduler daily job restarts, meaning the "run at 11 AM IST"
guarantee doesn't hold if nobody visited the app in the previous 30 min.

**Fix:** GitHub Actions cron. Add `.github/workflows/daily_scan.yml`
that runs `python services/run_daily_scan.py` on a schedule. Requires
your `GROQ_API_KEY` etc. as GitHub Secrets.

### 4. Gmail OAuth
`credentials.json` + `token.json` can't be safely committed. Two options:

- **Demo mode**: leave Gmail unconfigured. The Notification Center still
  works, the Send button will just fail with an authenticate-error.
- **Full email**: on first local run, complete OAuth locally to generate
  `token.json`. Encode it as base64 and paste into Streamlit Secrets:
  ```toml
  GMAIL_TOKEN_JSON_B64 = "eyJ0eXBlI..."
  ```
  Then in `app/main.py`, near the top:
  ```python
  import base64, json
  if "GMAIL_TOKEN_JSON_B64" in st.secrets:
      Path("credentials").mkdir(exist_ok=True)
      Path("credentials/token.json").write_text(
          base64.b64decode(st.secrets["GMAIL_TOKEN_JSON_B64"]).decode()
      )
  ```

Same treatment for `credentials.json` if needed.

## Security checklist BEFORE going public

- [ ] `.env` and `credentials/` files are NOT in `git log` — verify with:
      `git log --all -- .env credentials.json token.json`
      Must return zero lines. If not, use BFG Repo-Cleaner and force-push.
- [ ] Rotate any secret that was ever committed accidentally.
- [ ] `JWT_SECRET` is a fresh 32+ character random string, DIFFERENT from
      your local dev value.
- [ ] `ADMIN_EMAIL` uses your work email so unauthorised signups don't
      escalate to admin.
- [ ] The login screen makes it obvious "this is invite-only" — the
      allowlist auth already enforces this at the code level, but a
      subtle warning banner helps.
- [ ] Enable 2FA on your GitHub account (repo settings).

## Alternative: Docker + Render.com ($7/mo)

If ephemeral filesystem is a dealbreaker, Render offers a persistent disk.
Create `Dockerfile` at project root:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app/main.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Then on Render: New → Web Service → connect this repo → runtime Docker.
Fill env vars from your local `.env`. Deploy.

## Alternative: Cloudflare Tunnel (free, private)

Runs Streamlit on your PC, tunnels public URL to it via Cloudflare.
Requires your PC to be on. Good for team demos, not production.

```powershell
winget install --id Cloudflare.cloudflared
cloudflared tunnel login
cloudflared tunnel create cfo-copilot
cloudflared tunnel run cfo-copilot
```

Prints a `https://random-name.trycloudflare.com` URL.
