# CFO Desk — Supabase (Postgres) Setup

This moves the app's databases (`auth.db` **and** `invoices.db`) to Supabase
Postgres, so accounts and data **persist** — you sign up once, and nothing is
wiped on restart/redeploy. Locally the app still uses SQLite automatically; it
switches to Postgres only when `DATABASE_URL` is set.

## 1. Create a Supabase project
1. Go to https://supabase.com → New project (free tier is fine).
2. Set a database password and pick a region near you. Wait for it to provision.

## 2. Create the tables
1. In Supabase → **SQL Editor** → **New query**.
2. Paste the entire contents of `schema_supabase.sql` and click **Run**.
3. You should see the tables under **Table editor** (clients, invoices,
   email_drafts, users, allowlist, …).

## 3. Get the connection string
1. Supabase → **Project Settings → Database → Connection string**.
2. Choose the **Connection pooling** string (recommended for cloud apps),
   "Transaction" or "Session" mode. It looks like:
   ```
   postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```
   (The direct string `...@db.<ref>.supabase.co:5432/postgres` also works.)
3. Replace `<PASSWORD>` with your database password. If the app can't connect,
   append `?sslmode=require` to the end.

## 4. Point the app at Supabase
Add `DATABASE_URL` to your config alongside the others.

**Streamlit Cloud** → app → ⋮ → Settings → Secrets (root-level keys):
```toml
DATABASE_URL  = "postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:6543/postgres"
ADMIN_EMAIL   = "gyan.prakash@infrabeat.com"
SALES_XLSX    = "data/Copy_of_Sales_Data-dummy.xlsx"
# ...your existing SMTP_*, IMAP_*, GROQ_API_KEY, JWT_SECRET, AGENT_SECRET...
```
`DB_PATH` / `AUTH_DB_PATH` are ignored when `DATABASE_URL` is set — all tables
live in the one Supabase database.

**Local `.env`** (optional — only if you want local dev to use Supabase too):
```
DATABASE_URL=postgresql://...
```
Leave it out locally to keep using the SQLite files.

## 5. Deploy
Commit and push (Streamlit Cloud deploys from the repo, and `psycopg2-binary`
is now in `requirements.txt`):
```
git add -A && git commit -m "Add Supabase/Postgres backend" && git push
```

## 6. Verify
- Sign up once with your `ADMIN_EMAIL`. Restart the app (or redeploy) — you
  should **still be able to log in**. That confirms persistence.
- In Supabase → Table editor → `users`, you'll see your account row.

## What this does and doesn't cover
- ✅ Persistent accounts (no more re-signup) and persistent reminders / PTP data.
- ✅ Local dev unchanged (SQLite when `DATABASE_URL` is unset).
- ⚠️ **KPIs still read the Excel workbook** — commit `data/Copy_of_Sales_Data-dummy.xlsx`
  to the repo (remove it from `.gitignore` or `git add -f` it) so the dashboard
  has data. Moving invoice analytics into Postgres is a later step.
- ⚠️ Existing local SQLite data is **not** auto-migrated. You start fresh on
  Supabase (fine for auth; operational data accrues from here on).

## Rollback
Remove `DATABASE_URL` and the app instantly reverts to local SQLite. No code change.
