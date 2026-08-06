import sqlite3, datetime
c = sqlite3.connect("auth.db")
c.execute(
    "INSERT OR REPLACE INTO allowlist (email, role, added_by, added_at) VALUES (?,?,?,?)",
    ("gyan.prakash@infrabeat.com", "admin", "manual", datetime.datetime.utcnow().isoformat()),
)
c.commit()
print("allowlist now:")
for r in c.execute("SELECT email, role FROM allowlist"):
    print("  ", r[0], "->", r[1])
