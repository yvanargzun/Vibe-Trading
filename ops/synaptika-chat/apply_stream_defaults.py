#!/usr/bin/env python3
import sqlite3
import json

con = sqlite3.connect("/app/backend/data/webui.db")
cur = con.cursor()
print("config cols", [r[1] for r in cur.execute("PRAGMA table_info(config)")])
rows = cur.execute("SELECT * FROM config LIMIT 1").fetchall()
print("config rows", len(rows))
if rows:
    print("row0 types", [type(x).__name__ for x in rows[0]])
    for i, x in enumerate(rows[0]):
        s = str(x)
        print(i, s[:120].replace("\n", " "))

PARAMS = {"stream_response": True, "temperature": 0.5, "max_tokens": 2048}
users = cur.execute("SELECT id, settings FROM user").fetchall()
for uid, settings in users:
    try:
        s = json.loads(settings) if settings else {}
    except json.JSONDecodeError:
        s = {}
    if not isinstance(s, dict):
        s = {}
    ui = s.setdefault("ui", {})
    if not isinstance(ui, dict):
        ui = {}
        s["ui"] = ui
    ui["streamResponse"] = True
    params = ui.setdefault("params", {})
    if isinstance(params, dict):
        params.update(PARAMS)
    else:
        ui["params"] = dict(PARAMS)
    models = ui.setdefault("models", [])
    if isinstance(models, list) and "synaptika-chat-auto" not in models:
        models.insert(0, "synaptika-chat-auto")
    cur.execute("UPDATE user SET settings=? WHERE id=?", (json.dumps(s), uid))

# config table: try common shapes
cols = [r[1] for r in cur.execute("PRAGMA table_info(config)")]
if "data" in cols:
    row = cur.execute("SELECT rowid, data FROM config LIMIT 1").fetchone()
    if row:
        rid, data = row
        try:
            doc = json.loads(data) if isinstance(data, str) else (data or {})
        except json.JSONDecodeError:
            doc = {}
        if isinstance(doc, dict):
            ui = doc.setdefault("ui", {})
            if isinstance(ui, dict):
                ui["default_models"] = "synaptika-chat-auto"
                p = ui.setdefault("default_model_params", {})
                if isinstance(p, dict):
                    p.update(PARAMS)
                else:
                    ui["default_model_params"] = dict(PARAMS)
            cur.execute("UPDATE config SET data=? WHERE rowid=?", (json.dumps(doc), rid))
            print("config_updated")

con.commit()
print("users_updated", len(users))
