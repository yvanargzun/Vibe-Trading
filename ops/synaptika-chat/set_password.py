#!/usr/bin/env python3
"""Set Open WebUI password for admin@localhost."""
import sqlite3
import sys

try:
    import bcrypt
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "bcrypt"])
    import bcrypt

PASSWORD = sys.argv[1] if len(sys.argv) > 1 else "m59466Fr"
EMAIL = "admin@localhost"
hashed = bcrypt.hashpw(PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

con = sqlite3.connect("/app/backend/data/webui.db")
cur = con.cursor()
cur.execute("UPDATE auth SET password=? WHERE email=?", (hashed, EMAIL))
if cur.rowcount == 0:
    print("ERROR: no auth row for", EMAIL)
    sys.exit(1)
cur.execute("UPDATE user SET name=? WHERE email=?", ("Admin", EMAIL))
con.commit()
# verify
row = cur.execute("SELECT email, password FROM auth WHERE email=?", (EMAIL,)).fetchone()
ok = bcrypt.checkpw(PASSWORD.encode("utf-8"), row[1].encode("utf-8"))
print("updated", EMAIL, "ok" if ok else "VERIFY_FAIL")
