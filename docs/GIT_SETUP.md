# Git Setup & Push to GitHub

Copy-paste commands. Runs from PowerShell in the project root.

## Step 0 — One-time system setup

Only needed if you've never used Git on this machine.

```powershell
# Install Git if not present
winget install --id Git.Git -e

# Configure your identity — used as commit author
git config --global user.name "Sampada Suryawanshi"
git config --global user.email "your.email@company.com"

# Verify
git --version
git config --global --list
```

## Step 1 — Initialise the repo

```powershell
cd "C:\path\to\cfo-copilot"

# If this folder was NEVER a git repo:
git init
git branch -m main

# If you're switching from a previous repo, remove old .git first:
# Remove-Item -Recurse -Force .git
# git init
# git branch -m main
```

## Step 2 — Sanity-check that secrets are excluded

CRITICAL — do NOT skip. If you accidentally commit .env or token.json, you
have to rotate every secret and rewrite git history to remove it.

```powershell
git status
```

The output MUST NOT list any of these:
- `.env`
- `credentials.json` / `token.json` / `credentials/`
- `invoices.db` / `auth.db`
- `venv/`
- `__pycache__/`

If any of them shows up, `.gitignore` isn't being applied properly.
Fix `.gitignore` FIRST, then re-run `git status` until the list is clean.

## Step 3 — Copy your existing local files INTO this refactored folder

The refactored folder I gave you has 20 Python files with rewritten
imports, but it doesn't have:

- `services/gmail_client.py` — YOUR working Gmail OAuth client
- `credentials/credentials.json` + `credentials/token.json` — YOUR OAuth
  files (gitignored, keep them local only)
- `data/Copy_of_Sales_Data-dummy.xlsx` — YOUR workbook
- `templates/*.json` — YOUR per-client email templates
- `.env` — YOUR configuration with real values

Copy these from your existing `backend\` folder into the new folder BEFORE
committing. From PowerShell, if your existing project is at
`C:\Users\sampada.suryawanshi\Downloads\OneDrive_2026-06-17\CFO Agent 2\backend`:

```powershell
$OLD = "C:\Users\sampada.suryawanshi\Downloads\OneDrive_2026-06-17\CFO Agent 2\backend"
$NEW = Get-Location

Copy-Item "$OLD\gmail_client.py" "$NEW\services\gmail_client.py"
Copy-Item "$OLD\.env" "$NEW\.env"

New-Item -ItemType Directory -Force "$NEW\credentials" | Out-Null
Copy-Item "$OLD\credentials.json" "$NEW\credentials\" -ErrorAction SilentlyContinue
Copy-Item "$OLD\token.json" "$NEW\credentials\" -ErrorAction SilentlyContinue

# Data + templates (if not already in the pack)
Copy-Item "$OLD\Copy_of_Sales_Data-dummy.xlsx" "$NEW\data\" -ErrorAction SilentlyContinue
Copy-Item "$OLD\templates\*.json" "$NEW\templates\" -ErrorAction SilentlyContinue
```

## Step 4 — Test that Streamlit launches from the refactored folder

BEFORE pushing anything, prove the app works with the new structure:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
streamlit run app/main.py
```

Log in, click through all 3 tabs, ask the chatbot a question. If everything
works, proceed. If something is broken, don't push — come back and I'll
debug.

## Step 5 — Stage & commit

```powershell
# Add every file EXCEPT the gitignored ones
git add .

# One more sanity check — review what will be committed
git status

# You should see:
#   new file: .gitignore
#   new file: .env.example
#   new file: .streamlit/config.toml
#   new file: .streamlit/secrets.toml.example
#   new file: README.md
#   new file: requirements.txt
#   new file: register_task.ps1
#   new file: app/__init__.py
#   ... all the app/, ai/, core/, services/, docs/ files ...
#   new file: templates/*.json
#   new file: data/Copy_of_Sales_Data-dummy.xlsx
#
# You must NOT see:
#   .env, credentials/, credentials.json, token.json, *.db, venv/,
#   __pycache__/

# If everything looks right:
git commit -m "Initial commit: CFO Copilot v3.5"
```

## Step 6 — Create the GitHub repo

Option A — Web:
1. Go to <https://github.com/new>
2. Owner: your account
3. Repository name: `cfo-copilot`
4. Public / Private: your call (private recommended for finance data)
5. IMPORTANT: **Do NOT** initialise with README, .gitignore, or LICENSE.
   You already have all three locally.
6. Create repository.

Option B — GitHub CLI:
```powershell
winget install --id GitHub.cli
gh auth login
gh repo create cfo-copilot --private --source=. --remote=origin --push
```

If you use Option B, you're done — skip to Step 8.

## Step 7 — Wire up the remote (Option A path)

GitHub shows you the exact commands after you create the empty repo.
Roughly:

```powershell
git remote add origin https://github.com/<YOUR-USERNAME>/cfo-copilot.git
git push -u origin main
```

You'll be prompted for GitHub credentials. If browser-based login doesn't
work automatically, generate a Personal Access Token at
<https://github.com/settings/tokens> and paste it as the password.

## Step 8 — Verify

Go to <https://github.com/YOUR-USERNAME/cfo-copilot>. You should see all
your files. Click into `.gitignore` to verify it's there and looks right.

Then, most importantly, search the repo (top-right search box) for:
- `JWT_SECRET` — should only appear in `.env.example`, never as a real value.
- `GROQ_API_KEY` — should only appear in `.env.example` (empty) or docs.
- `sk-` or `gsk_` — should return zero hits.

If you find real secrets, STOP and rotate them immediately.

## What to do if you leaked a secret

1. Rotate the secret NOW (revoke API key, generate new one).
2. Update `.env` locally with the new secret.
3. Remove the file from git history:
   ```powershell
   # Install BFG once
   # Download bfg.jar from https://rtyley.github.io/bfg-repo-cleaner/
   java -jar bfg.jar --delete-files .env
   git reflog expire --expire=now --all
   git gc --prune=now --aggressive
   git push --force
   ```
4. Notify anyone who cloned the old version.

## Next — Deploy to Streamlit Cloud

See `docs/DEPLOYMENT.md`.
