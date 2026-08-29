#!/usr/bin/env python3
"""Disable search-query rewriting + skip full page loader."""
import json
import sqlite3
import time

DB = "/app/backend/data/webui.db"
now = int(time.time())


def upsert(cur, key, value):
    payload = json.dumps(value)
    if cur.execute("SELECT 1 FROM config WHERE key=?", (key,)).fetchone():
        cur.execute(
            "UPDATE config SET value=?, updated_at=? WHERE key=?",
            (payload, now, key),
        )
    else:
        cur.execute(
            "INSERT INTO config(key, value, updated_at) VALUES (?,?,?)",
            (key, payload, now),
        )


con = sqlite3.connect(DB)
cur = con.cursor()
upsert(cur, "web.search.enable", True)
upsert(cur, "web.search.engine", "duckduckgo")
upsert(cur, "web.search.bypass_embedding_and_retrieval", True)
upsert(cur, "web.search.bypass_web_loader", True)
upsert(cur, "task.query.search.enable", False)
con.commit()
print("ok_query_gen_off_loader_bypass_on")
for key, val in cur.execute(
    "SELECT key, value FROM config WHERE key LIKE '%search%' OR key LIKE '%query%'"
):
    print(key, "=", repr(val)[:160])
