"""
auth_users.py — Restricted signup/login for CFO Copilot (Streamlit).

Replaces the single shared-password auth.py with real per-user accounts,
but signup is LOCKED to an allowlist: only email addresses that an admin
has pre-approved can create an account. Everyone else sees
"This email is not authorised for access."

Design decisions
----------------
- SQLite (auth.db) — same zero-infra philosophy as invoices.db.
- PBKDF2-HMAC-SHA256 with per-user salt, 200k iterations. No plaintext,
  no external dependencies (bcrypt not required).
- Login lockout: 5 failed attempts per email per 15 minutes.
- Roles: 'admin' and 'member'. Admins manage the allowlist and users
  from a panel inside the app (sidebar).
- Bootstrap: the email in env var ADMIN_EMAIL is auto-allowlisted as
  admin, so the very first person can sign up without a chicken-and-egg
  problem.
- Session: streamlit session_state + an idle timeout (default 60 min).

Integration (app.py, right after st.set_page_config):

    from app.auth import require_login, logout_button, admin_sidebar_panel
    user = require_login()          # blocks until authenticated
    logout_button()                 # renders in sidebar
    if user["role"] == "admin":
        admin_sidebar_panel()       # allowlist + user management

.env additions:
    ADMIN_EMAIL=cfo@yourcompany.com
    AUTH_DB_PATH=auth.db            # optional
    SESSION_TIMEOUT_MIN=60          # optional
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime

import streamlit as st

def _cfg(key: str, default: str = "") -> str:
    """Read config from the environment first, then Streamlit secrets.
    Streamlit Community Cloud keeps top-level secrets in st.secrets and does
    not always mirror them into os.environ, so we check both."""
    v = os.getenv(key)
    if v:
        return v
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return default


DB_PATH = _cfg("AUTH_DB_PATH", "auth.db")
ADMIN_EMAIL = _cfg("ADMIN_EMAIL", "").strip().lower()
SESSION_TIMEOUT_MIN = int(_cfg("SESSION_TIMEOUT_MIN", "60"))

PBKDF2_ITERATIONS = 200_000
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SEC = 15 * 60


# ---------------------------------------------------------------- database
def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db() -> None:
    with _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS allowlist (
                email      TEXT PRIMARY KEY,
                role       TEXT NOT NULL DEFAULT 'member',   -- role granted on signup
                added_by   TEXT,
                added_at   TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                email         TEXT PRIMARY KEY,
                full_name     TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                salt          TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'member',
                status        TEXT NOT NULL DEFAULT 'active', -- active | disabled
                created_at    TEXT,
                last_login    TEXT
            );
            CREATE TABLE IF NOT EXISTS login_attempts (
                email   TEXT,
                ts      REAL,
                success INTEGER
            );
            """
        )
        # Bootstrap: guarantee the configured admin can always sign up.
        # Resolve at runtime (not just module import) so it works on Streamlit
        # Cloud, where secrets may not be in os.environ at import time.
        admin = _cfg("ADMIN_EMAIL", "").strip().lower()
        if admin:
            c.execute(
                "INSERT OR IGNORE INTO allowlist (email, role, added_by, added_at) "
                "VALUES (?, 'admin', 'bootstrap', ?)",
                (admin, datetime.utcnow().isoformat()),
            )


# ---------------------------------------------------------------- hashing
def _hash_password(password: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS
    )
    return dk.hex()


def _verify_password(password: str, salt_hex: str, stored_hash: str) -> bool:
    return hmac.compare_digest(_hash_password(password, salt_hex), stored_hash)


# ---------------------------------------------------------------- core ops
def is_allowlisted(email: str) -> sqlite3.Row | None:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM allowlist WHERE email = ?", (email.lower().strip(),)
        ).fetchone()


def signup(email: str, full_name: str, password: str) -> tuple[bool, str]:
    email = email.lower().strip()
    if not email or "@" not in email:
        return False, "Enter a valid email address."
    if len(password) < 10:
        return False, "Password must be at least 10 characters."

    # Final safeguard: make sure the configured admin is always allowlisted at
    # the moment they sign up (covers Cloud cases where the init-time bootstrap
    # ran before the secret was available).
    admin = _cfg("ADMIN_EMAIL", "").strip().lower()
    if admin and email == admin:
        with _conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO allowlist (email, role, added_by, added_at) "
                "VALUES (?, 'admin', 'bootstrap', ?)",
                (admin, datetime.utcnow().isoformat()),
            )

    entry = is_allowlisted(email)
    if entry is None:
        return False, (
            "This email is not authorised for access. "
            "Ask an administrator to add you to the approved list."
        )

    with _conn() as c:
        if c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            return False, "An account already exists for this email — please log in."
        salt = secrets.token_hex(16)
        c.execute(
            "INSERT INTO users (email, full_name, password_hash, salt, role, "
            "status, created_at) VALUES (?,?,?,?,?, 'active', ?)",
            (
                email,
                full_name.strip() or email,
                _hash_password(password, salt),
                salt,
                entry["role"],
                datetime.utcnow().isoformat(),
            ),
        )
    return True, "Account created — you can log in now."


def _is_locked_out(email: str) -> bool:
    cutoff = time.time() - LOCKOUT_WINDOW_SEC
    with _conn() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM login_attempts "
            "WHERE email = ? AND ts > ? AND success = 0",
            (email, cutoff),
        ).fetchone()[0]
    return n >= MAX_FAILED_ATTEMPTS


def login(email: str, password: str) -> tuple[bool, str, dict | None]:
    email = email.lower().strip()
    if _is_locked_out(email):
        return False, "Too many failed attempts. Try again in 15 minutes.", None

    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        ok = bool(
            row
            and row["status"] == "active"
            and _verify_password(password, row["salt"], row["password_hash"])
        )
        c.execute(
            "INSERT INTO login_attempts (email, ts, success) VALUES (?,?,?)",
            (email, time.time(), int(ok)),
        )
        if ok:
            c.execute(
                "UPDATE users SET last_login = ? WHERE email = ?",
                (datetime.utcnow().isoformat(), email),
            )

    if not ok:
        return False, "Invalid email or password, or account disabled.", None
    return True, "", {"email": row["email"], "name": row["full_name"], "role": row["role"]}


# ---------------------------------------------------------------- admin ops
def add_to_allowlist(email: str, role: str, added_by: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO allowlist (email, role, added_by, added_at) "
            "VALUES (?,?,?,?)",
            (email.lower().strip(), role, added_by, datetime.utcnow().isoformat()),
        )


def remove_from_allowlist(email: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM allowlist WHERE email = ?", (email.lower().strip(),))


def set_user_status(email: str, status: str) -> None:
    with _conn() as c:
        c.execute("UPDATE users SET status = ? WHERE email = ?", (status, email))


def delete_user(email: str) -> None:
    """Fully remove a user's account. Allowlist entry is kept, so admin can
    re-invite the same email if needed."""
    email = email.lower().strip()
    with _conn() as c:
        c.execute("DELETE FROM users WHERE email = ?", (email,))
        c.execute("DELETE FROM login_attempts WHERE email = ?", (email,))
        c.execute("DELETE FROM password_resets WHERE email = ?", (email,))


# ---- password reset (admin-issued codes; no SMTP required) ----
def _ensure_reset_table() -> None:
    with _conn() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS password_resets ("
            "  email TEXT PRIMARY KEY,"
            "  code_hash TEXT NOT NULL,"
            "  salt TEXT NOT NULL,"
            "  expires_at REAL NOT NULL,"
            "  issued_by TEXT)"
        )


def issue_reset_code(email: str, issued_by: str) -> str:
    """Admin generates a one-time reset code. Returned in plain text so the
    admin can share it via any trusted channel (Teams / phone / in person).
    Code expires in 30 minutes; single use."""
    _ensure_reset_table()
    email = email.lower().strip()
    with _conn() as c:
        if not c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            return ""
        code = secrets.token_hex(4).upper()   # 8-char code
        salt = secrets.token_hex(16)
        c.execute(
            "INSERT OR REPLACE INTO password_resets "
            "(email, code_hash, salt, expires_at, issued_by) VALUES (?,?,?,?,?)",
            (email, _hash_password(code, salt), salt,
             time.time() + 30 * 60, issued_by),
        )
    return code


def redeem_reset_code(email: str, code: str, new_password: str) -> tuple[bool, str]:
    _ensure_reset_table()
    if len(new_password) < 10:
        return False, "New password must be at least 10 characters."
    email = email.lower().strip()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM password_resets WHERE email = ?", (email,)
        ).fetchone()
        if not row:
            return False, "No reset code has been issued for this email. Ask an admin to generate one."
        if row["expires_at"] < time.time():
            c.execute("DELETE FROM password_resets WHERE email = ?", (email,))
            return False, "That reset code has expired. Ask the admin to issue a new one."
        if not hmac.compare_digest(
            _hash_password(code.strip().upper(), row["salt"]), row["code_hash"]
        ):
            return False, "Invalid reset code."
        new_salt = secrets.token_hex(16)
        c.execute(
            "UPDATE users SET salt = ?, password_hash = ? WHERE email = ?",
            (new_salt, _hash_password(new_password, new_salt), email),
        )
        c.execute("DELETE FROM password_resets WHERE email = ?", (email,))
    return True, "Password updated — please log in with your new password."


def list_allowlist() -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute("SELECT * FROM allowlist ORDER BY email").fetchall()


def list_users() -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute(
            "SELECT email, full_name, role, status, last_login FROM users ORDER BY email"
        ).fetchall()


# ---------------------------------------------------------------- streamlit UI
def _session_expired() -> bool:
    last = st.session_state.get("auth_last_seen")
    return bool(last and time.time() - last > SESSION_TIMEOUT_MIN * 60)


LOGIN_CSS = """
<style>
  /* ═══════════════════════════════════════════════════════════════════
     CRED-inspired: obsidian black, muted cream text, one accent colour,
     generous whitespace, no gradients on the button, no red anywhere.
     Red is reserved for error states in the app.
     ═══════════════════════════════════════════════════════════════════ */

  #MainMenu, footer, header {visibility: hidden;}

  /* Deep obsidian background with a subtle radial highlight — the CRED
     'glossy black' feel without going full noir. Works in both themes. */
  .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
      background:
          radial-gradient(1200px 600px at 50% -10%, #1a1a1f 0%, transparent 60%),
          radial-gradient(900px 500px at 50% 110%, #16161b 0%, transparent 55%),
          #0a0a0d !important;
      color: #e8e6df !important;
  }

  /* ── HERO ─────────────────────────────────────────────────────────── */
  .login-hero { text-align:center; padding: 56px 12px 12px 12px; }
  .login-hero .brand {
      font-size: 34px; font-weight: 500; letter-spacing: 1.2px;
      margin: 0; color: #ffffff;
      font-family: 'Segoe UI', 'Inter', -apple-system, sans-serif;
  }
  .login-hero .brand .mono {          /* was ".gold" — same class works */
      color: #ffffff;
      font-weight: 300;
      letter-spacing: 2px;
  }
  .login-hero .divider {
      width: 32px; height: 1px; background: #3a3a42;
      margin: 22px auto 18px auto;
  }
  .login-hero .tag {
      color: #c9c4b6 !important; font-size: 15px; margin: 0;
      font-weight: 300; letter-spacing: .4px;
      font-family: 'Segoe UI', 'Inter', sans-serif;
  }
  .login-hero .sub {
      color: #6b6a63 !important; font-size: 12px;
      margin-top: 10px; letter-spacing: 3px; text-transform: uppercase;
  }

  /* ── FORM CARD ─────────────────────────────────────────────────────── */
  .login-card {
      background: #111114 !important;
      border: 1px solid #23232a !important;
      border-radius: 16px;
      padding: 32px 34px 24px 34px;
      box-shadow:
          0 1px 0 rgba(255,255,255,.03) inset,
          0 24px 60px rgba(0,0,0,.5);
      max-width: 440px; margin: 32px auto 0 auto;
  }
  /* The `<div class='login-card'>` opening tag renders as an EMPTY styled box
     (Streamlit closes the unclosed div within its own markdown block, and the
     tabs/form render as siblings below it). Hide that empty orphan so only the
     real content shows. A card that actually contains widgets is not :empty. */
  .login-card:empty {
      display: none !important;
      padding: 0 !important; margin: 0 !important; border: none !important;
      background: none !important; box-shadow: none !important; min-height: 0 !important;
  }

  /* Field labels — muted cream, small caps */
  .login-card label, .login-card label p,
  .login-card [data-testid="stWidgetLabel"] p {
      color: #8a8880 !important;
      font-weight: 500 !important;
      font-size: 11px !important;
      text-transform: uppercase !important;
      letter-spacing: 1.8px !important;
      margin-bottom: 6px !important;
  }

  /* Input fields — dark neutral, cream text, subtle focus */
  .login-card input,
  .login-card [data-baseweb="input"] input,
  .login-card [data-baseweb="base-input"] input {
      background-color: #1a1a1f !important;
      color: #f0eee5 !important;
      -webkit-text-fill-color: #f0eee5 !important;
      font-size: 15px !important;
      caret-color: #c9a961 !important;
  }
  .login-card [data-baseweb="input"],
  .login-card [data-baseweb="base-input"] {
      background-color: #1a1a1f !important;
      border: 1px solid #2a2a32 !important;
      border-radius: 10px !important;
      transition: border-color .2s ease, box-shadow .2s ease;
  }
  .login-card input::placeholder {
      color: #5a5952 !important; opacity: 1;
  }
  .login-card [data-baseweb="input"]:focus-within,
  .login-card [data-baseweb="base-input"]:focus-within {
      border-color: #c9a961 !important;
      box-shadow: 0 0 0 3px rgba(201, 169, 97, 0.10) !important;
  }

  /* Password reveal icon */
  .login-card [data-baseweb="input"] button {
      color: #6b6a63 !important; background: transparent !important;
  }
  .login-card [data-baseweb="input"] button:hover {
      color: #c9a961 !important;
  }

  /* ── TABS ─────────────────────────────────────────────────────────── */
  .login-card .stTabs [data-baseweb="tab-list"] {
      justify-content: flex-start !important;
      gap: 28px !important;
      border-bottom: 1px solid #23232a !important;
      margin-bottom: 24px !important;
  }
  .login-card .stTabs [data-baseweb="tab"] {
      background: transparent !important;
      padding: 8px 0 !important;
  }
  .login-card .stTabs [data-baseweb="tab"] p {
      color: #6b6a63 !important;
      font-weight: 500 !important;
      font-size: 12px !important;
      letter-spacing: 1.5px !important;
      text-transform: uppercase !important;
  }
  .login-card .stTabs [aria-selected="true"] p {
      color: #f0eee5 !important;
  }
  .login-card .stTabs [data-baseweb="tab-highlight"] {
      background-color: #c9a961 !important;
      height: 1px !important;
  }
  .login-card .stTabs [data-baseweb="tab-border"] { display: none !important; }

  /* Captions */
  .login-card .stCaption p, .login-card [data-testid="stCaptionContainer"] p,
  .login-card small {
      color: #6b6a63 !important;
      font-size: 12px !important; line-height: 1.6 !important;
  }

  /* ── PRIMARY BUTTON — CRED-style, quiet, no red ──────────────────── */
  .login-card .stButton>button,
  .login-card .stButton>button:focus,
  .login-card [data-testid="stForm"] .stButton>button {
      background: #f0eee5 !important;      /* soft cream */
      background-color: #f0eee5 !important;
      color: #0a0a0d !important;
      border: 0 !important;
      border-radius: 10px !important;
      height: 46px !important;
      font-weight: 600 !important;
      font-size: 13px !important;
      letter-spacing: 2px !important;
      text-transform: uppercase !important;
      box-shadow: 0 2px 12px rgba(0,0,0,.4) !important;
      transition: all .2s ease !important;
      margin-top: 8px !important;
  }
  .login-card .stButton>button:hover {
      background: #ffffff !important;
      background-color: #ffffff !important;
      color: #0a0a0d !important;
      transform: translateY(-1px);
      box-shadow: 0 4px 18px rgba(240, 238, 229, 0.15) !important;
  }
  .login-card .stButton>button:active { transform: translateY(0); }

  /* Error / alert boxes inside the card — red stays reserved for these */
  .login-card [data-testid="stAlert"] {
      background: rgba(220, 76, 76, 0.08) !important;
      border: 1px solid rgba(220, 76, 76, 0.25) !important;
      border-radius: 8px !important;
      color: #f5b8b8 !important;
  }
  .login-card [data-testid="stAlert"][data-baseweb="notification"] p {
      color: #f5b8b8 !important;
  }

  /* Success (rare) */
  .login-card .stAlert.st-emotion-cache-success {
      background: rgba(140, 189, 122, 0.08) !important;
      border-color: rgba(140, 189, 122, 0.25) !important;
      color: #a8d091 !important;
  }

  /* ── FOOTER FEATURE STRIP ────────────────────────────────────────── */
  .features {
      color: #4a4943 !important; font-size: 11px; text-align: center;
      margin-top: 40px; letter-spacing: 2px; text-transform: uppercase;
  }
  .features span { margin: 0 14px; color: #6b6a63; }
  .features .dot { color: #3a3a42; margin: 0 6px; }
</style>
"""


def require_login() -> dict:
    """Render the login/signup gate. Returns the user dict once authenticated;
    calls st.stop() otherwise, so nothing below it runs for anonymous visitors."""
    init_auth_db()

    if st.session_state.get("auth_user") and not _session_expired():
        st.session_state["auth_last_seen"] = time.time()
        return st.session_state["auth_user"]

    if _session_expired():
        st.session_state.pop("auth_user", None)
        session_expired_flag = True
    else:
        session_expired_flag = False

    st.markdown(LOGIN_CSS, unsafe_allow_html=True)

    # Elegant hero block
    st.markdown(
        """
        <div class='login-hero'>
            <p class='brand'>CFO <span class='mono'>DESK</span></p>
            <div class='divider'></div>
            <p class='tag'>AI-Powered Finance Intelligence Platform</p>
            <p class='sub'>Cash · Collections · Clarity</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Centered card
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown("<div class='login-card'>", unsafe_allow_html=True)
        if session_expired_flag:
            st.warning("Session expired — please log in again.")

        tab_login, tab_signup, tab_forgot = st.tabs(
            ["Sign In", "Create Account", "Forgot Password"]
        )

        with tab_login:
            with st.form("login_form", clear_on_submit=False):
                email = st.text_input("Work email",
                                      placeholder="you@company.com")
                password = st.text_input("Password", type="password",
                                         placeholder="Your password")
                if st.form_submit_button("Sign In", use_container_width=True,
                                         type="primary"):
                    ok, msg, user = login(email, password)
                    if ok:
                        st.session_state["auth_user"] = user
                        st.session_state["auth_last_seen"] = time.time()
                        st.rerun()
                    else:
                        st.error(msg)

        with tab_signup:
            st.caption(
                "Access is invitation-only. Signup works only for emails "
                "an administrator has pre-approved."
            )
            with st.form("signup_form"):
                s_email = st.text_input("Work email", key="su_email")
                s_name = st.text_input("Full name", key="su_name")
                s_pw = st.text_input("Password (min 10 chars)",
                                     type="password", key="su_pw")
                s_pw2 = st.text_input("Confirm password",
                                      type="password", key="su_pw2")
                if st.form_submit_button("Create Account",
                                         use_container_width=True,
                                         type="primary"):
                    if s_pw != s_pw2:
                        st.error("Passwords do not match.")
                    else:
                        ok, msg = signup(s_email, s_name, s_pw)
                        (st.success if ok else st.error)(msg)

        with tab_forgot:
            st.caption(
                "Password reset codes are issued by an administrator "
                "(no email server involved). Ask them to generate one for "
                "you — it's an 8-character code, valid for 30 minutes."
            )
            with st.form("forgot_form"):
                f_email = st.text_input("Your email", key="fp_email")
                f_code  = st.text_input("Reset code from admin",
                                        key="fp_code",
                                        placeholder="e.g. A3F921C0")
                f_pw    = st.text_input("New password (min 10 chars)",
                                        type="password", key="fp_pw")
                f_pw2   = st.text_input("Confirm new password",
                                        type="password", key="fp_pw2")
                if st.form_submit_button("Reset Password",
                                         use_container_width=True,
                                         type="primary"):
                    if f_pw != f_pw2:
                        st.error("Passwords do not match.")
                    else:
                        ok, msg = redeem_reset_code(f_email, f_code, f_pw)
                        (st.success if ok else st.error)(msg)

        st.markdown("</div>", unsafe_allow_html=True)

    # Footer feature strip
    st.markdown(
        """
        <div class='features'>
            <span>Real-time KPIs</span><span class='dot'>·</span>
            <span>AI Reminders</span><span class='dot'>·</span>
            <span>Smart Notifications</span><span class='dot'>·</span>
            <span>Portfolio Risk</span><span class='dot'>·</span>
            <span>Enterprise Access</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.stop()  # never reached when authenticated


def logout_button() -> None:
    user = st.session_state.get("auth_user")
    if not user:
        return
    with st.sidebar:
        st.markdown(f"**{user['name']}**  \n`{user['role']}`")
        if st.button("Log out", use_container_width=True):
            st.session_state.pop("auth_user", None)
            st.rerun()


def admin_sidebar_panel() -> None:
    """Allowlist + user management. Call only for role == 'admin'."""
    with st.sidebar.expander("⚙️ Access management (admin)"):
        st.markdown("**Approved emails**")
        for row in list_allowlist():
            c1, c2 = st.columns([4, 1])
            c1.write(f"{row['email']} · {row['role']}")
            if row["email"] != ADMIN_EMAIL and c2.button("✕", key=f"rm_{row['email']}"):
                remove_from_allowlist(row["email"])
                st.rerun()

        new_email = st.text_input("Add email to allowlist", key="al_new_email")
        new_role = st.selectbox("Role", ["member", "admin"], key="al_new_role")
        if st.button("Approve email") and new_email:
            add_to_allowlist(new_email, new_role, st.session_state["auth_user"]["email"])
            st.success(f"Approved {new_email}")
            st.rerun()

        st.divider()
        st.markdown("**Registered users**")
        for u in list_users():
            c1, c2, c3, c4 = st.columns([4, 1.2, 1.4, 1])
            c1.write(f"{u['email']}  ·  _{u['status']}_")
            is_self = u["email"] == st.session_state["auth_user"]["email"]

            # enable / disable
            if not is_self:
                lbl = "disable" if u["status"] == "active" else "enable"
                if c2.button(lbl, key=f"tg_{u['email']}",
                             use_container_width=True):
                    set_user_status(
                        u["email"],
                        "disabled" if lbl == "disable" else "active"
                    )
                    st.rerun()
            else:
                c2.write("_(you)_")

            # issue reset code
            if c3.button("reset code",
                         key=f"rst_{u['email']}",
                         use_container_width=True,
                         help="Generate a one-time password-reset code. Share it with the user via a trusted channel. Expires in 30 minutes."):
                code = issue_reset_code(u["email"],
                                        st.session_state["auth_user"]["email"])
                if code:
                    st.session_state[f"_show_code_{u['email']}"] = code
                st.rerun()
            if code := st.session_state.get(f"_show_code_{u['email']}"):
                st.info(
                    f"🔑 Code for **{u['email']}** (valid 30 min): `{code}`  "
                    "— share via Teams / phone. Ask them to use the "
                    "*Forgot Password* tab.",
                    icon="🔐",
                )

            # delete user (double-click safeguard via session flag)
            if not is_self:
                flag_key = f"_confirm_del_{u['email']}"
                if c4.button("🗑",
                             key=f"del_{u['email']}",
                             use_container_width=True,
                             help="Delete this user's account entirely. The email stays on the allowlist so they can sign up again."):
                    if st.session_state.get(flag_key):
                        delete_user(u["email"])
                        st.session_state.pop(flag_key, None)
                        st.success(f"Deleted {u['email']}")
                        st.rerun()
                    else:
                        st.session_state[flag_key] = True
                        st.warning(
                            f"Click 🗑 again to confirm deleting **{u['email']}**"
                        )
