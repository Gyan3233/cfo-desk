import sqlite3, os
path = "auth.db"
print("DB:", os.path.abspath(path), "| exists:", os.path.exists(path))
c = sqlite3.connect(path); c.row_factory = sqlite3.Row
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("tables:", tables)
if "allowlist" in tables:
    print("\nALLOWLIST:")
    for r in c.execute("SELECT email, role FROM allowlist"):
        print("  ", repr(r["email"]), "->", r["role"])
if "users" in tables:
    print("\nUSERS:")
    for r in c.execute("SELECT email, full_name, status FROM users"):
        print("  ", repr(r["email"]), "|", r["full_name"], "|", r["status"])
