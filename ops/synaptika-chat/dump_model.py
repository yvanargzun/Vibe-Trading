#!/usr/bin/env python3
import json, sqlite3
c = sqlite3.connect("/app/backend/data/webui.db")
r = c.execute(
    "SELECT params, meta, created_at, base_model_id FROM model WHERE id=?",
    ("synaptika-chat-auto",),
).fetchone()
print("created_at", r[2], "base_model_id", r[3])
print("params", r[0])
print("meta", r[1][:500] if r[1] else None)
