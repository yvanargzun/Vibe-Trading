#!/usr/bin/env python3
import sqlite3
con = sqlite3.connect("/app/backend/data/webui.db")
cur = con.cursor()
print("tables", [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
for t in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
    name = t[0]
    if "user" in name.lower() or "auth" in name.lower():
        cols = [d[0] for d in cur.execute(f"PRAGMA table_info({name})").fetchall()]
        # pragma returns cid,name,...
        cols = [d[1] for d in cur.execute(f"PRAGMA table_info({name})").fetchall()]
        print("TABLE", name, cols)
        try:
            rows = cur.execute(f"SELECT * FROM {name} LIMIT 5").fetchall()
            for r in rows:
                print(r[:12])
        except Exception as e:
            print("err", e)
