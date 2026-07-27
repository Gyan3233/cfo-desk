# CFO Copilot — System Handbook v3.2

**Owner:** Sampada Suryawanshi (Infrabeat)
**Purpose:** Knowledge base · Presentation kit · Handoff protocol

Read top-to-bottom and you understand every KPI, every model, every design
decision. Give to a fresh engineer or AI session and they can continue the
work without prior context. ■ marks material new since v2.

---

## File & folder summary

| File | Purpose |
|---|---|
| `app.py` | Streamlit dashboard (3 tabs; ~700 lines) |
| `auth_users.py` | Allowlist-restricted signup/login (CRED-black theme) |
| `database.py` | SQLite ORM-lite for the reminder DB |
| `pipeline.py` | 8-step Gmail→LLM→DB→drafts orchestrator |
| `gmail_client.py` | OAuth, read inbox, send with CC/BCC |
| `template_manager.py` | Per-client email templates + editor UI |
| `csv_invoice_source.py` | Legacy CSV/SQL draft pipeline |
| `reminder_center.py` | Reminder lifecycle engine + Notification Center UI |
| `excel_data_source.py` | Loads the Sales Data workbook, computes KPI base |
| `kpi_catalog.py` | Central tooltip text for every KPI (ⓘ source of truth) |
| `tab1_dashboard.py` | ■ Executive Overview render logic |
| `tab2_client_profiles.py` | ■ Client Profiles rebuilt on new workbook |
| `analytics.py` | ■ 8-week forecast, cash proj, 3D risk cube, on-time rate |
| `ml_intelligence.py` | ■ P(late) ML model + all model charts |
| `templates/*.json` | Per-client email template files |
| `data/Copy_of_Sales_Data-dummy.xlsx` | ■ Primary data source |
| `invoices.db` | SQLite: clients, invoices, drafts, audit log |
| `auth.db` | SQLite: users, allowlist, password resets |
| `.env` | Config; do NOT commit |
| `Dockerfile`, `docker-compose.yml` | Container recipe |

---

## Table of Contents

§1 What this system is
§2 Highlights — Statistical tests & ML techniques ■ updated
§3 File & folder map
§4 KPI catalog for senior leadership ■ updated
§5 Dashboard evolution — v2 → v3.2 ■ new
§6 Development journey
§7 Deep dive: the ML & analytics layers ■ heavily updated
§8 Reading the dashboard ■ updated for v3.2
§9 Data model reference ■ updated
§10 Running, launching, testing
§11 Deployment & sharing
§12 Security notes
§13 Known issues & tech debt
§14 Roadmap
§15 Handoff protocol for a fresh session
§16 Session transcript / chat history ■ appended

---

## 1. What This System Is

Two things stitched together into one Streamlit app:

**A. Email-driven Invoice Agent.** Reads Gmail, uses an LLM (Ollama /
Groq / Cerebras / Gemini / Claude) to extract structured invoice data from
message bodies, stores everything in SQLite (`invoices.db`), generates
polite reminder emails when invoices approach or pass due date, and either
sends them automatically or presents them in a review queue for
edit-and-send.

**B. CFO Analytics Dashboard.** Sits on top of the Sales Data workbook
(`Copy_of_Sales_Data-dummy.xlsx`, 9,976 invoices × 30 columns). Shows
executive KPIs, retained cash-flow forecasting, an AI-driven action queue,
a real-time on-time rate, and a client-level P(late) model with proper
time-based cross-validation and calibration.

Both halves share `app.py` and a common data layer. The reminder-email
agent writes to `invoices.db`; the analytics dashboard reads from the
Excel workbook and cross-references `invoices.db` for the reminder outcomes
panel.

### What changed since v2
- **New data source**: switched from `ap_ar_data.xlsx` (small snapshot) to
  `Copy_of_Sales_Data-dummy.xlsx` (real 2-year history, 9,976 invoices).
- **New ML module**: `ml_intelligence.py` implements a properly
  cross-validated + calibrated P(late) model with SHAP-style attribution
  and a full technical detail expander for reviewers.
- **New analytics module**: `analytics.py` retains three explicit charts
  from v2 (8-week forecast, collection effectiveness, 3D risk cube) plus
  adds a Monte-Carlo cash projection.
- **Notification Center**: Tab 3 renamed and rewired — reminders now
  appear T-5 days before their send date, then auto-send unless cancelled.
- **Allowlist auth**: replaces the single shared-password gate.
- **CRED-black UI**: obsidian background, cream text, gold accent, no red
  except for errors.

---

## 2. Highlights — Statistical Tests & ML Techniques ■

One-page cheat sheet: every technique deployed, what it does in one line,
and where in the code it lives.

### 2.1 Machine Learning

| Technique | Purpose | Code Reference |
|---|---|---|
| Calibrated Logistic Regression (Platt) | Predicts P(late) per open invoice | `ml_intelligence.train_model()` |
| Class-balanced weighting | Handles subtle class imbalance | `class_weight='balanced'` |
| `CalibratedClassifierCV(cv=3)` | Turns LR scores into real probabilities | Same function |
| StandardScaler | Zero-mean unit-variance normalisation | Pipeline first step |
| SHAP-style linear attribution | Per-invoice log-odds contribution per feature | `ml_intelligence.attribute()` |
| Leakage-safe rolling features | For row *i*, features use only rows 0..i-1 for that client | `build_training_frame()` |
| `TimeSeriesSplit(n_splits=5)` | Chronological cross-validation, no shuffling | `train_model()` |

### 2.2 Statistical Methods

| Technique | Purpose | Code Reference |
|---|---|---|
| Empirical Bootstrap Monte Carlo | 400-sim probabilistic 90-day cash projection | `analytics.cash_projection()` |
| Percentile Bands (P10 / P50 / P90) | Confidence intervals over Monte-Carlo sims | Same function |
| Rolling 30-day / 90-day averages | On-time rate, DSO trend | `analytics.collection_effectiveness()` |
| Empirical CDF | Days-to-pay distribution per client | `tab2_client_profiles.fig_payment_timing_cdf()` |
| Herfindahl-Hirschman Index (HHI) | Concentration on outstanding balance by client | `excel_data_source.compute_kpis()` |
| Effective Number of Clients | 1 / Σ(share²) — inverse participation ratio | Same function |
| P10 / P50 / P90 quantiles | Payment-timing distribution markers | Tab 2 CDF chart |

### 2.3 Model Evaluation

| Metric | What It Measures | Your Value |
|---|---|---|
| ROC-AUC (held-out) | Ranking quality on unseen invoices | **0.798** (deployable) |
| Brier Score (held-out) | MSE of calibrated probabilities (0 = perfect) | **0.194** (below 0.25 baseline) |
| Average Precision (held-out) | Ranking quality at the top of the list | **0.790** |
| Train-test AUC gap | Overfitting diagnostic | **+0.056** (healthy) |
| Calibration curve | Predicted vs observed rate across 10 bins | ⚙ Technical expander |
| Feature coefficients | Direction & magnitude per feature | ⚙ Technical expander |
| Fold-by-fold AUC line | Generalisation across time folds | ⚙ Technical expander |

### 2.4 Domain Heuristics

| Heuristic | Formula | Reasoning |
|---|---|---|
| Expected Loss | Balance × P(late) | Prioritises the action queue |
| Reliability Score | round((1 − P(late)) × 100) | Human-friendly 0-100 scale |
| High-Risk Threshold | P(late) > 0.7 | Where to focus collections effort |
| Portfolio Velocity | 365 / DSO | Times AR turns over per year |
| AR Turnover Ratio | Total billed / open outstanding | Collection efficiency proxy |
| Anchor-Date Reporting | max(Invoice Date) from workbook | Handles static snapshots |

---

## 3. File & Folder Map

```
CFO Agent 2/
└── backend/
    ├── app.py                       ← Streamlit dashboard (3 tabs)
    ├── auth_users.py                ← Allowlist signup/login + admin panel
    ├── database.py                  ← SQLite ORM-lite (invoices, drafts, audit)
    ├── pipeline.py                  ← 8-step Gmail orchestrator
    ├── gmail_client.py              ← OAuth read/send with CC/BCC
    ├── template_manager.py          ← Per-client email templates + editor
    ├── csv_invoice_source.py        ← Legacy CSV/SQL draft path
    ├── reminder_center.py           ← Lifecycle engine + Notification Center
    ├── excel_data_source.py    ■   ← Reads Sales Data workbook
    ├── kpi_catalog.py          ■   ← Tooltip text single source of truth
    ├── tab1_dashboard.py       ■   ← Executive Overview render
    ├── tab2_client_profiles.py ■   ← Client Profiles render (new data)
    ├── analytics.py            ■   ← 8-week forecast, cash proj, cube
    ├── ml_intelligence.py      ■   ← P(late) model + attribution + charts
    ├── invoices.db                  ← SQLite (auto-created)
    ├── auth.db                      ← SQLite (auto-created; users/allowlist)
    ├── credentials.json             ← Google OAuth (see §12)
    ├── token.json                   ← OAuth refresh token (see §12)
    ├── .env                         ← config; do NOT commit
    ├── requirements.txt
    ├── Dockerfile
    ├── docker-compose.yml
    ├── .streamlit/config.toml
    ├── templates/
    │   ├── _default.json            ← Fallback template
    │   ├── acme_industrial.json     ← Formal MSA-style
    │   ├── beacon_retail.json       ← Short & friendly
    │   └── delta_foods.json         ← Statement-style
    └── data/
        └── Copy_of_Sales_Data-dummy.xlsx   ■  Primary data source
```

---

## 4. KPI Catalog for Senior Leadership ■

Written for a boardroom, not a data-science review. Every KPI has ⓘ
tooltip text sourced from `kpi_catalog.py`; hover any card on the
dashboard to see the same explanation.

### 4.1 Portfolio KPIs (Tab 1 primary strip)

**Total Outstanding**
Value on your data: ~₹28 Cr (all currency INR).
Formula: `Σ Balance where Balance > 0`.
Why it matters: The AR portfolio you're managing. All other KPIs are its
derivatives.

**Overdue**
Formula: `Σ Balance where Due Date < today AND Balance > 0`.
Why it matters: Money you should already have collected. Every day it
sits here is one day of cash tied up.

**Large Overdues (> ₹50K)**
Count of overdue invoices above ₹50,000.
Why: These are the P&L movers. One collected large overdue > ten small ones.

**DSO (90-day trailing)** — your value: ~47 days
Formula: `mean(days_to_pay) for closed invoices in last 90 days`.
Benchmark: ≤ payment terms + 5.
Action if off: Investigate the DSO regime-shift annotation.

**On-time Payment Rate** — your value: ~55% (v3.2 · was 30% earlier
because that number was on old data)
Formula: `mean(paid_date ≤ due_date) for closed invoices in last 90 days`.
Benchmark: > 70% healthy, > 85% best-in-class.
Action if off: Top priority to improve.

**Effective # Clients** — your value: ~7.7 of 82
Formula: `1 / Σ(client_share²)`.
Why: You have 82 open-invoice clients, but if two dominate, your true
concentration is closer to 3.

### 4.2 Retained Charts (Tab 1, from v2 dashboard)

**8-Week Cash Flow Forecast + Action Queue**
Bars: expected AR inflow per week, from Due Date grouping.
Cumulative line: running total across 8 weeks.
Action queue: top 8 open invoices by `Balance × P(late)`, with per-row
P(late) chip. Colour: red > 70%, gold 40–70%, green < 40%.

**Collection Effectiveness (rolling 90-day on-time rate)**
Filled area chart of on-time %. Threshold line at 70%.
Band label: Best-in-class / Healthy / Needs work / Concerning.

**3D Risk Cube (counterparties)**
X = open exposure · Y = P(late) from LR · Z = avg historical days past due.
Marker size = # open invoices. Colour = expected loss (green → gold → red).
Top-right-back corner is where trouble lives.

### 4.3 New in v3.2

**90-Day Probabilistic Cash Projection**
Non-parametric empirical bootstrap: for every open invoice, sample a
plausible payment date from that client's own historical days-to-pay
distribution. 400 sims. Report P10 (worst-case), P50 (median),
Expected (mean), P90 (optimistic).
Your data: Expected ~₹3.4 Cr / P10 ~₹2.8 Cr / P90 ~₹4.0 Cr at 90-day
horizon.
Why: strips away Normal-distribution assumptions. Long right tails come
out for free.
How to read: The P10 low point first — that's your planning floor.

**Late-Payment Intelligence (the ML section)**
Four headline metrics:
- Model AUC (held-out) · your value 0.798
- Brier (held-out) · your value 0.194
- High-risk exposure · sum of Balance where P(late) > 70%
- Portfolio Expected Loss · Σ P(late) × Balance across all open invoices

Business view: risk distribution histogram + feature-importance bar +
top-10 action queue by expected loss.
Technical view (⚙ expander): calibration curve, ROC, precision-recall,
train-vs-test AUC per time fold.

**Derived KPIs Strip**
- Portfolio Velocity = 365 / DSO ≈ 7.7×/year on your data.
- AR Turnover Ratio = Total billed / Open outstanding.
- Dominant Payment Mode (last 90 days).

### 4.4 Client-Level KPIs (Tab 2)

- Total invoices · Open invoices · Historical late rate · Median
  days-to-pay · Average P(late) on open invoices.
- Payment-timing empirical CDF with P50 / P90 markers.
- Days-late-over-time trend (green early, red late).
- Full history table (collapsible).

### 4.5 Working KPIs (Notification Center · Tab 3)

- Drafts awaiting review (count with gold badge)
- Reminders sent (via email_drafts.status='sent')
- Sends-in countdown per row (today / N days)

---

## 5. Dashboard Evolution — v2 → v3.2 ■

### 5.1 What was retained explicitly

Per user request, three charts survived the switch from `ap_ar_data.xlsx`
to the new workbook:

| Section | v2 source | v3.2 rebuild |
|---|---|---|
| 8-week forecast + action queue | Old snapshot | `analytics.eight_week_forecast()` on new workbook + `action_queue()` uses ML expected loss |
| Collection effectiveness (90-day on-time rate) | Old snapshot | `analytics.collection_effectiveness()` on all closed invoices |
| 3D risk cube | Random noise stand-in | `analytics.risk_cube_data()` uses real ML-derived P(late) |

### 5.2 What was replaced

| v2 approach | v3.2 approach | Why |
|---|---|---|
| INR mock numbers on Tab 1 | Real KPIs from workbook | Real data > mockup |
| ML model on 296 rows | ML model on 7,287 rows with TimeSeriesSplit CV | Ten-fold data, honest CV |
| "Reporting as of 11 Mar 2026" banner | Removed | Distracting; anchor is implicit |
| Late-payment model card as inline table | Full technical expander with 4 charts | Data-scientist skills showcase |
| Old xlsx client profiles | New workbook client profiles | Data source unification |
| Streamlit v3 red button | CRED-black + cream primary | Design refresh |

### 5.3 What was added

- **Notification Center** with T-5 reminder scheduling.
- **AR aging chart**, **DSO trend**, **top clients**, **payment mode
  donut**, **salesperson leaderboard** on Tab 1.
- **Empirical Monte-Carlo cash projection** replacing eyeball bands.
- **Derived KPIs strip** (portfolio velocity, AR turnover, dominant mode).
- **ⓘ tooltip on every KPI** sourced from `kpi_catalog.py`.
- **Allowlist auth** replacing single shared password.
- **Forgot-password flow** with admin-issued codes.

---

## 6. Development Journey

Eight working sessions between v2 and v3.2.

### Session A — v3 Foundation
Rebuilt Tab 3 as CSV/SQL invoice pipeline; added allowlist auth; created
`template_manager.py`. This is what became the base for v3.1.

### Session B — v3.1 Excel switch
Added `excel_data_source.py`, `kpi_catalog.py`, `reminder_center.py`,
`tab1_dashboard.py`. Login page redesigned twice (first navy/gold, then
CRED-black after user feedback). Multiple NaT-handling bug fixes as bad
date cells in the workbook surfaced.

### Session C — v3.2 ML + retained charts
Built `ml_intelligence.py` with proper time-based CV, calibration,
attribution. Built `analytics.py` for the three retained charts and cash
projection. Rebuilt `tab2_client_profiles.py` on the new workbook. All
existing Tab 1 sections preserved; new sections appended below.

### What's not built yet (see §14)
- Multi-touch reminder cadence (T-5 → T+3 → T+10).
- Model drift monitoring.
- Real reply-outcome tracking (currently proxied by invoice status).

---

## 7. Deep Dive: The ML & Analytics Layers

Purpose: so you can look at any number on the dashboard and know what it
means, how it was computed, its failure modes, and when to distrust it.

### 7.1 Calibrated Logistic Regression on `is_late`

**Purpose**: predict, for every open invoice, the probability it will be
paid late.

**Why LR?** With 7,287 training rows, LR (7 coefficients) captures only
the strongest signals — which is what you want. Fancier models memorise
noise at this scale. LR is linear, so SHAP attribution is exact.

**Ground truth**: `is_late = paid_date > due_date`, where `paid_date` is
the max `Date` in Payments joined on `Invoice Number`. Base rate 48.7%
(well-balanced).

**Feature engineering — leakage-safe**: For row *i*, features use only
rows 0..i-1 of that client. Computing "client's average days-to-pay" using
all invoices *including the one being predicted* would leak the answer.

**Features (7 total)**

| Feature | Captures | Sign on your data |
|---|---|---|
| `prior_late_rate` | Fraction of past invoices paid late | +1.22 (dominant) |
| `prior_avg_dtp` | Client's mean days-to-pay | +0.34 |
| `prior_std_dtp` | Payment-timing volatility | −0.27 |
| `log_amount` | ln(1 + Total) | +0.04 |
| `relative_amount` | Invoice ÷ client's median past invoice | −0.09 |
| `tenure_n` | # prior invoices | −0.17 |
| `days_since_last_inv` | Recency gap | −0.06 |

**Training set**: 7,287 rows (first 3 invoices per client dropped —
features unstable at low tenure).

**Calibration**: `CalibratedClassifierCV(cv=3, method='sigmoid')` — Platt
scaling. Ensures 70% actually means 70%. Sigmoid over isotonic because
isotonic overfits at N < 10K.

**Cross-validation**: `TimeSeriesSplit(n_splits=5)` on Invoice Date, no
shuffling — plain time-based per user decision. Reports train and test
AUC/Brier/AP per fold.

**Held-out metrics**: AUC 0.798, Brier 0.194, AP 0.790. Train-test AUC
gap **+0.056** (healthy).

**Interpretation guide**:
- AUC 0.798: pick a random late invoice + random on-time one. Model gives
  the late one a higher score 79.8% of the time.
- Brier 0.194: below the 0.25 always-predict-base-rate baseline.
  Probabilities can be multiplied by rupees.
- Gap +0.056: the model is only slightly better on training than on
  held-out. Would flag > 0.10 as concerning.

**Failure modes**:
1. New clients with < 3 invoices — features skipped, no P(late) shown.
2. `@st.cache_resource` on the model — restart Streamlit if you change
   the workbook mid-session.
3. If the base rate drifts far from 48.7% (e.g., after a major payment
   policy change), recalibrate.

### 7.2 SHAP-Style Linear Attribution

For a linear model, log-odds contribution of feature *j* to prediction *i*
is:

```
contribution(i, j) = coefficient_j × x_scaled(i, j)
```

Since features are scaled (StandardScaler), a scaled value of 0 means
"average client for that feature." Positive contribution pushes toward
LATE; negative pushes toward ON-TIME.

Attribution is exact for linear models — no approximation needed.

### 7.3 Non-Parametric Monte-Carlo Cash Projection

For each open invoice, sample a `days_to_pay` value directly from that
client's historical `days_to_pay` array (bootstrap). No shape assumption.
Long right tails come out for free. Repeat 400 times per invoice. Sum
dated cash flows. Take percentiles across sims.

**Reading the chart**:
- Solid gold line = expected (mean).
- Green dotted = P90 optimistic ceiling.
- Red dotted = P10 pessimistic floor.
- Gold shaded band = P10–P90 confidence interval.
- Read the P10 first — that's your planning floor.

Falls back to global days-to-pay distribution for clients with < 3
historical invoices.

### 7.4 Collection Effectiveness (rolling 90-day on-time rate)

Rolling window: 90 days.
Data: closed invoices only, with real paid_date from Payments.
Metric: `mean(paid_date ≤ due_date)` across the window.
Latest value on your data: **55.1%** (Needs-work band; healthy = 70%+).

### 7.5 3D Risk Cube

For every client with open invoices:
- X = open exposure (₹) — sum of Balance.
- Y = P(late) — average across their open invoices from the ML model.
- Z = avg days past due — historical, from Payments joined on Due Date.
- Marker size = # open invoices.
- Colour = expected loss (green → gold → red).

Click-drag to rotate in the browser.

### 7.6 8-Week Forecast + Action Queue

Bucket every open invoice into the week its Due Date falls in, over 8
weeks forward from workbook anchor. Bars = expected AR inflow. Line =
cumulative.

Action queue: top 8 open invoices by `expected_loss = Balance × P(late)`.
Each row shows invoice number, client, balance, and colour-coded P(late)
chip.

### 7.7 Bayesian Fallback (when sklearn missing)

Beta-Bernoulli with weak prior α=2, β=8 (base rate 20%). For 40-invoice
clients the prior gets swamped; for 2-invoice clients the estimate stays
near 20%. Now that sklearn is a hard dependency this rarely activates.

---

## 8. Reading the Dashboard

### Tab 1 — Executive Overview
1. **Primary KPI strip (6 cards)**: Total Outstanding, Overdue, Large
   Overdues, DSO, On-time %, Effective # Clients. Every card has ⓘ.
2. **AR Aging chart** + **Reminder Outcomes** panel.
3. **DSO Trend** (30-day rolling, 6 months) + **Top Clients** concentration.
4. **Payment Mode mix** (last 90 days donut) + **Salesperson Leaderboard**.
5. **8-week cash flow forecast** + **Action queue** (retained).
6. **Collection effectiveness** with band label (retained).
7. **90-day probabilistic cash projection** with P10/P50/expected/P90.
8. **Derived KPIs strip**.
9. **3D risk cube** (retained).
10. **Late-Payment Intelligence** with default business view and ⚙ technical
    detail expander for reviewers.

### Tab 2 — Client Profiles
- Client picker sortable by Name / Outstanding / P(late).
- 5-KPI header per client.
- Open-invoices table with per-invoice P(late) and expected loss.
- Payment-timing empirical CDF.
- Days-late-over-time trend.
- Full invoice history in expander.

### Tab 3 — Notification Center
- Filters: status × window × search.
- Sortable table: sends-in countdown, client, invoice, amount, template,
  status chip, actions.
- Actions: ✏️ Review · 📤 Send now · 🚫 Cancel.
- Review drawer with subject / body / CC editor.
- Recently-sent history expander.

### Sidebar
- User identity + logout.
- Admin panel (visible only if role = admin) with allowlist management,
  user disable/enable, reset-code issuance, delete-user.

---

## 9. Data Model Reference

### Copy_of_Sales_Data-dummy.xlsx (primary data source)

| Sheet | Rows | Key Columns |
|---|---|---|
| Invoices | 9,976 | Invoice ID, Invoice Number, Customer ID, Invoice Date, Due Date, Total, Balance, Invoice Status, Currency Code, salesperson_name |
| Payments | 8,806 | CustomerPayment ID, CustomerID, Date, Amount, Mode, Invoice Number |
| Contacts | 654 | Contact ID, Contact Name, Company Name, Contact Type, EmailID, EmailID_New, Payment Terms |
| Sales Order | 7,884 | SalesOrder ID, Customer ID, Order Date, Total, Status |
| DropdownData | 18 | Reference data (safe to ignore) |

**Anchor date**: `max(Invoice Date)` across all rows. Used as "today" for
DSO windows and cash projections.

**Reminder recipient**: `Contacts.EmailID_New` (per user decision — dummy
data uses internal `@infrabeat.com` owners; production would use
`EmailID`).

**Invoice status vocabulary**: `Closed` (paid, Balance = 0), `Overdue`
(past due, Balance > 0), `Void` (cancelled).

### invoices.db (SQLite, 5 tables)

| Table | Purpose |
|---|---|
| clients | One row per unique client, keyed by email |
| invoices | Extracted / imported invoices with dedupe on (invoice_number, client_id) |
| email_drafts | Reminder drafts: pending → approved → sent (or rejected/failed). CHECK constraint on status. |
| push_subscriptions | Browser push tokens (currently unused) |
| agent_runs | Pipeline audit log |

Draft status vocabulary (v3.2): `pending` (was `pending_review`),
`approved` (was `reviewed`), `rejected` (was `cancelled`), `sent`,
`failed`. UI shows friendly labels via `_status_chip()`.

### auth.db (SQLite, 4 tables) ■

| Table | Purpose |
|---|---|
| users | Registered accounts with PBKDF2-SHA256 salted password hashes |
| allowlist | Emails admin has approved for signup |
| login_attempts | For 5-per-15-min lockout window |
| password_resets | Admin-issued reset codes (30-min expiry, single-use) |

Bootstrap: `ADMIN_EMAIL` from `.env` is auto-inserted into `allowlist` on
first run.

---

## 10. Running, Launching, Testing

### First-time setup

```powershell
cd "C:\...\CFO Agent 2\backend"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Add to `requirements.txt` if not present: `scikit-learn`, `openpyxl`,
`plotly`, `streamlit`, `pandas`, `numpy`, `python-dotenv`.

### `.env` template

```
ADMIN_EMAIL=your@email.com          # bootstrap admin
JWT_SECRET=<random 40+ chars>
SALES_XLSX=data/Copy_of_Sales_Data-dummy.xlsx
CC_EMAIL=<optional CCs>
SENDER_NAME=Accounts Team — Infrabeat
SENDER_COMPANY=Infrabeat Technologies
REMINDER_BEFORE_DUE_DAYS=3
NOTIFY_LEAD_DAYS=5
DRAFT_ENGINE=template               # template | llm
DUE_SOON_DAYS=7
DB_PATH=./invoices.db
AUTH_DB_PATH=auth.db
SESSION_TIMEOUT_MIN=60
AI_PROVIDER=ollama
OLLAMA_MODEL=llama3.2
OLLAMA_HOST=http://localhost:11434
```

### Launch

```powershell
cd "C:\...\CFO Agent 2\backend"
venv\Scripts\activate
streamlit run app.py
```

Opens on http://localhost:8501.

### First-run checklist

1. Sign up with `ADMIN_EMAIL` → automatic admin.
2. First page load takes ~10 seconds (model training + Monte Carlo).
   Cached after.
3. Tab 1: verify test AUC card shows ~0.798, high-risk exposure ~₹5 Cr.
4. Tab 1 → Late-Payment Intelligence → ⚙ Technical detail → all four
   charts should render.
5. Tab 2: pick a client, verify P(late) reads and the CDF chart populates.
6. Tab 3: Notification Center should show pending drafts if any invoices
   are in the T-5 window.

### Test the reminder pipeline

- Tab 3 → 📤 Send now on a draft → gmail_client dispatches, status
  updates to `sent`.
- Errors (missing credentials, expired token, empty recipient) surface as
  a clear message and the draft goes to `failed`.

---

## 11. Deployment & Sharing

Threat model: this dashboard shows real financial data. Any shared
deployment must be authenticated. The v3+ allowlist auth is enforced
in-app, so you're covered at the layer where it matters.

| Option | Cost | Privacy | Setup | Best For |
|---|---|---|---|---|
| Streamlit Community Cloud | Free | Public code | 15 min | Demos |
| Docker + Render.com | $7/mo | Private | 60 min | Production |
| Cloudflare Tunnel | Free | Private | 10 min | Team sharing from your machine |
| ngrok | Free / $8/mo | Public URL | 5 min | Ad-hoc |

### Data handling

- `Copy_of_Sales_Data-dummy.xlsx` — bundle in Docker image or mount from
  persistent storage.
- `invoices.db` / `auth.db` — persistent volume, or migrate to Postgres
  (Neon/Supabase) if > 5 concurrent users.
- `credentials.json` / `token.json` — never bake into the image. Store
  token as env var and reconstruct at startup.
- `.env` — never commit.

---

## 12. Security Notes

Files that contained live credentials at various points in the project's
history — treat them as compromised until rotated:
- `credentials.json` (Google OAuth client_secret)
- `token.json` (Gmail refresh token)
- `GROQ_API_KEY` (was in `.env` once)
- `AGENT_SECRET`

**Rotate ALL of these before any shared deployment**:
1. Google Cloud Console → APIs & Services → Credentials → find the
   client → revoke → create new → download.
2. myaccount.google.com → Security → Third-party access → remove Invoice
   Agent → delete local `token.json`.
3. console.groq.com → API Keys → rotate.
4. `.env`: new `AGENT_SECRET` (random long string).

**Add to `.gitignore`**: `credentials.json`, `token.json`, `.env`,
`invoices.db`, `auth.db`, `*.pkl`, `.streamlit/secrets.toml`, `venv/`.

**Auth security properties** (v3+):
- PBKDF2-HMAC-SHA256 with per-user 16-byte salt, 200k iterations.
- 5 failed logins per email per 15 minutes → lockout.
- Session idle timeout (default 60 min).
- Password reset codes: 8-char, 30-min expiry, single-use, PBKDF2-hashed
  in storage.

---

## 13. Known Issues & Tech Debt

| # | Item | Severity |
|---|---|---|
| 1 | Two competing UIs (`app.py` Streamlit vs `server.py` FastAPI) still coexist | Medium — kill server.py |
| 2 | Old-version files (`app_wip.py`, `server_-_Copy.py`) still in folder | Low — delete |
| 3 | Client-name mismatch between xlsx and invoices.db → Tab 2 uses fuzzy match | Medium — migrate to shared counterparty_id |
| 4 | Payment mode analytics shows 100% Bank Transfer because dummy data has only that | Low — real data will populate |
| 5 | EmailID_New used as reminder recipient (dummy internal owners) | Low — swap to EmailID in production |
| 6 | Reminder outcomes proxied by invoice status (not true "paid within 7 days of send") | Medium — add real outcome tracking |
| 7 | Model uses plain time-based CV; client-blocked would be more honest | Low — chose per user session Q |
| 8 | `notifications.py` (push) has broken imports (v2 issue #9); handled gracefully | Low — already fail-soft |
| 9 | Streamlit `use_container_width` deprecation warnings | Low — still works |
| 10 | No model drift monitor yet | Medium — track test-AUC on recent N invoices |

---

## 14. Roadmap — What We Haven't Built Yet

Ranked by value ÷ effort.

### High value, moderate effort
- **Reminder outcomes tracker (real)**: currently `email_drafts.status='sent'`
  is used as a proxy for "reminder led to payment." Log
  `outcome_at_paid_date` per draft to compute real response rate.
- **Model drift monitoring**: small panel showing test-AUC on the most
  recent 500 scored invoices. When it drifts down 0.05 vs baseline, alert.
- **Multi-touch cadence (T-5 → T+3 → T+10 → escalation)**: needs schema
  change (`reminder_stage` column) + template variants.

### High value, higher effort
- **Real reply-outcome intelligence**: parse client replies for payment
  confirmations / promises / disputes, update invoice status
  automatically. Half-scoped in the v4 React prototype (deferred).
- **Salesperson coaching signals**: per-salesperson late rate + client
  churn correlation → highlight sellers who consistently book slow payers.
- **Client-financial-distress ensemble**: combine payment-timing
  degradation + reminder response + partial-payment onset → 2-state HMM
  (healthy vs distressed).

### Housekeeping
- Delete `app_wip.py`, `server_-_Copy.py`.
- Consolidate on Streamlit `app.py`, retire `server.py`.
- Move ML code out to a package `analytics/` folder once past prototype.
- Add temporal held-out backtest for CV metrics to a script (independent
  of Streamlit's caching).

---

## 15. Handoff Protocol for a Fresh Session

If pasting into a new AI session or handing to a new engineer:

1. **This handbook**. Full stop. Every design decision + every metric.
2. `app.py` — current dashboard code.
3. `data/Copy_of_Sales_Data-dummy.xlsx` — primary data source.
4. `ml_intelligence.py`, `analytics.py`, `tab1_dashboard.py`,
   `tab2_client_profiles.py` — the four v3.2 core modules.
5. `reminder_center.py`, `template_manager.py`, `auth_users.py`,
   `excel_data_source.py`, `kpi_catalog.py` — v3+ support.
6. `database.py`, `pipeline.py`, `gmail_client.py`, `agent.py` — if
   follow-up work touches the email-agent side.
7. Optional: `Dockerfile`, `docker-compose.yml`.
8. One-line task prompt like: *"Read the handbook. Then implement roadmap
   item X."*

### What the new session needs to know upfront

- **Two-halved system**. Analytics + agent. Both live in `app.py`.
- **New data source**: `Copy_of_Sales_Data-dummy.xlsx` (not
  `ap_ar_data.xlsx` — v2 file is gone). Anchor date = max(Invoice Date).
- **Currency is INR**, not USD.
- **Every model is trained in-process, cached, and small** — 7,287-row LR,
  400-sim Monte Carlo. No GPU.
- **Do not delete `invoices.db` or `auth.db`** — they're the audit log +
  user accounts.
- **Draft status vocab** in DB is `pending / approved / sent / rejected /
  failed`. UI shows friendly labels.
- **Reminder recipient is `EmailID_New`** (dummy data). Real deployment
  would use `EmailID`.
- **Auth is allowlist-gated**. First signup with `ADMIN_EMAIL` becomes
  admin.

### Recommended next tasks

| Task | Effort |
|---|---|
| Real reminder-outcome tracking (§14 High value #1) | Half day |
| Model drift monitor (§14 #2) | Half day |
| Multi-touch cadence with T+3 / T+10 escalation | 1-2 days |
| Kill `server.py`, delete legacy files | 1 hour |
| Deploy to Render.com behind auth (see §11) | 1-2 hours |

### Where NOT to spend time

- Fancier ML on 7K rows — won't beat the LR meaningfully.
- UI polish before real reply-outcome tracking — you have no way to
  measure reminder effectiveness without it.
- Reconciling client-name mismatch by scraping — just add the
  counterparty_id migration.

---

## 16. Session Transcript / Chat History ■

Sessions 1–7 documented in v2 handbook. Sessions 8+ produced v3.2.

### Session 8 — v3 foundation
- Prompt: build allowlist auth, per-client templates, CSV/SQL alternative
  to Gmail scan.
- Delivered: `auth_users.py`, `template_manager.py`,
  `csv_invoice_source.py`. Templates/ folder with 4 sample JSON files.

### Session 9 — v3.1 Excel switch
- Prompt: replace `ap_ar_data.xlsx` with `Copy_of_Sales_Data-dummy.xlsx`,
  update all KPIs, rename Tab 3 to Notification Center.
- Delivered: `excel_data_source.py`, `kpi_catalog.py`,
  `reminder_center.py`, `tab1_dashboard.py`. Multiple NaT-handling bug
  fixes as bad date cells surfaced (patched three times).

### Session 10 — Login redesign
- Prompt: change login look, currently boring.
- First delivered: navy gradient with gold. User feedback: too template-y.
- Second delivered: CRED-black aesthetic — obsidian background, cream
  primary button, gold accent only for focus rings and tab underline.

### Session 11 — v3.2 ML + retained charts
- Prompt: real P(late) model with time-based CV; retain 3 charts from v2
  (8-week forecast, collection effectiveness, 3D risk cube); rebuild Tab 2
  on new data.
- User chose: plain time-based CV (not client-blocked). Both business and
  technical views for ML section.
- Delivered: `ml_intelligence.py` (7 features, 7,287-row training set,
  test AUC 0.798, Brier 0.194), `analytics.py` (retained charts + cash
  projection + derived KPIs), `tab2_client_profiles.py` (client 360 on
  new data with per-invoice P(late)).

### Session 12 — Handbook v3.2
- Prompt: create a handbook of this new look, put all things in a place
  like old handbook.
- Delivered: this document.

---

## End of CFO Copilot — System Handbook v3.2

If a KPI on the dashboard looks weird, §4 tells you what it should mean;
§8 tells you how to read the chart; §7 tells you the math. If a piece of
code looks weird, §6 tells you when and why it landed. If you want to
share the app, §11 gives you copy-paste deployment paths.

For v4 material (React frontend prototype, FastAPI backend
`backend_v4/`), see the v4 architecture doc in
`/mnt/user-data/outputs/cfo_v4/ARCHITECTURE_AND_MIGRATION.md`. That branch
is deferred; v3.2 Streamlit is the current line.
