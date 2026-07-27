# CFO Copilot — AI-Powered Receivables Dashboard

An AR analytics + reminder-email agent for Indian corporate finance teams.
Streamlit UI, calibrated ML risk model, PTP tracking, and a RAG chatbot,
all backed by a real invoice workbook.

## Features

- **Executive KPI dashboard** — Total Outstanding, DSO, On-time %,
  concentration (HHI), 8-week cash flow forecast, 90-day probabilistic
  cash projection, 3D counterparty risk cube.
- **Calibrated P(late) model** — 7-feature logistic regression with
  time-based cross-validation and SHAP-style attribution. Test AUC 0.798,
  Brier 0.194 on 7,287 invoices.
- **Client 360** — payment-timing CDF, days-late-over-time,
  per-invoice P(late) scores, full history.
- **PTP analysis** — captures every promise-to-pay from client email replies
  via Ollama/Groq extraction. Enforces strict rule (promised date > current
  due date). Original Due Date is captured at ingestion and NEVER
  overwritten across any number of extensions.
- **AI communication timeline** — chat-style view of every reminder sent
  and every reply received, with AI classification of each reply.
- **Notification Center** — reminder review, edit, send. Threaded background
  send with a real Stop button. Auto-dedupes ghost drafts on invoices
  sent recently.
- **RAG chatbot** — floating gold-avatar assistant on every page, answers
  questions grounded in SQL retrieval from the live database.
- **Auto Gmail scan** — throttled on-open scan + daily job at configurable
  time. Only mail from clients we've actually reminded is fetched, so
  scans run in seconds even against large inboxes.
- **Allowlist auth** — PBKDF2-SHA256 with 200k iterations, 5-in-15-min
  lockout, forgot-password codes with 30-min expiry.

## Quick start (local)

```bash
git clone https://github.com/<YOUR-USERNAME>/cfo-copilot.git
cd cfo-copilot
python -m venv venv
venv\Scripts\activate               # Windows
# source venv/bin/activate          # Mac/Linux

pip install -r requirements.txt

copy .env.example .env              # Windows
# cp .env.example .env              # Mac/Linux
# Edit .env — set ADMIN_EMAIL and JWT_SECRET at minimum.

streamlit run app/main.py
```

Opens on <http://localhost:8501>. First signup with `ADMIN_EMAIL` becomes
admin.

## Configuration

Everything runs off `.env` locally, or Streamlit Cloud Secrets when
deployed. See `.env.example` for every setting with defaults and comments.

Minimum required to launch:

- `ADMIN_EMAIL` — the first signup with this email is auto-promoted to
  admin.
- `JWT_SECRET` — a long random string. Generate one:
  `python -c "import secrets; print(secrets.token_hex(32))"`
- `SALES_XLSX` — path to your invoice workbook (relative to project root).

Optional but recommended:

- `OLLAMA_HOST` + `OLLAMA_MODEL` — for local RAG chatbot + reply extraction
  (keeps everything private on your machine).
- `GROQ_API_KEY` — cloud fallback for the chatbot when Ollama isn't
  reachable. Required for Streamlit Cloud deployment.

## Project structure

```
cfo-copilot/
├── app/                    Streamlit UI code (entry point + tabs)
│   ├── main.py             streamlit run app/main.py
│   ├── auth.py             allowlist auth + admin panel
│   └── tabs/
│       ├── executive_overview.py
│       ├── client_profiles.py
│       ├── notification_center.py
│       └── ptp_ui.py
├── core/                   business logic & data access
│   ├── database.py         SQLite ORM-lite
│   ├── excel_source.py     workbook loader + KPI computer
│   ├── kpi_catalog.py      tooltip text source of truth
│   └── template_manager.py per-client email templates
├── services/               integrations & background workers
│   ├── gmail_client.py     Gmail OAuth read + send  ← keep your existing one
│   ├── scheduler.py        APScheduler daily job
│   ├── pipeline.py         Gmail→LLM→DB orchestrator
│   ├── csv_invoice_source.py legacy CSV path
│   └── run_daily_scan.py   standalone scan script (Task Scheduler)
├── ai/                     ML + LLM + RAG
│   ├── ml_intelligence.py  P(late) model + attribution
│   ├── analytics.py        cash forecast, risk cube, on-time rate
│   ├── ptp_intelligence.py PTP extraction + reply intake
│   └── chatbot.py          RAG chatbot (floating gold avatar)
├── data/                   Copy_of_Sales_Data-dummy.xlsx lives here
├── templates/              per-client email templates (JSON)
├── credentials/            Google OAuth (gitignored — NEVER commit)
├── docs/                   handbooks & guides
├── .streamlit/config.toml  dark theme with CRED-black palette
├── requirements.txt
├── .env.example
├── register_task.ps1       Windows Task Scheduler setup
└── README.md
```

## Deployment (Streamlit Community Cloud)

See `docs/DEPLOYMENT.md` for the step-by-step. Summary:

1. Push this repo to GitHub.
2. Sign in to <https://share.streamlit.io> with the same GitHub account.
3. New app → select this repo → main branch → main file path
   `app/main.py`.
4. Advanced settings → Secrets → paste the contents of
   `.streamlit/secrets.toml.example`, with real values.
5. Deploy. Your app is at `https://<something>.streamlit.app`.

Auto-redeploys on every `git push` to `main`.

## Documentation

- `docs/CFO_Copilot_Handbook_v3_2.md` — architecture, KPI definitions,
  ML deep-dive, roadmap.
- `docs/DEPLOYMENT.md` — Streamlit Cloud + Render.com + Cloudflare Tunnel.

## License

Proprietary — Infrabeat Technologies. All rights reserved.
