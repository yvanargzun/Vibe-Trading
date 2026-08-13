#!/usr/bin/env python3
"""Enable Open WebUI web search + default on for admin user."""
import json
import sqlite3
from datetime import datetime

DB = "/app/backend/data/webui.db"


def upsert_config(cur, key: str, value) -> None:
    payload = json.dumps(value) if not isinstance(value, str) else value
    # Open WebUI stores many values as JSON-encoded strings
    if isinstance(value, (bool, int, float, dict, list)):
        payload = json.dumps(value)
    now = int(datetime.utcnow().timestamp())
    row = cur.execute("SELECT key FROM config WHERE key=?", (key,)).fetchone()
    if row:
        cur.execute(
            "UPDATE config SET value=?, updated_at=? WHERE key=?",
            (payload, now, key),
        )
    else:
        cur.execute(
            "INSERT INTO config(key, value, updated_at) VALUES(?,?,?)",
            (key, payload, now),
        )


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # Common PersistentConfig keys used by Open WebUI web search
    upsert_config(cur, "web.enable", True)
    upsert_config(cur, "web.search.enable", True)
    upsert_config(cur, "rag.web.search.enable", True)
    upsert_config(cur, "rag.web.search.engine", "duckduckgo")
    upsert_config(cur, "web.search.engine", "duckduckgo")
    upsert_config(cur, "rag.web.search.result_count", 5)
    upsert_config(cur, "web.search.result_count", 5)
    upsert_config(cur, "rag.web.search.bypass_embedding", True)
    upsert_config(cur, "web.search.bypass_embedding_and_retrieval", True)

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
        ui["webSearch"] = True
        ui["webSearchEnabled"] = True
        # Keep stream on
        ui["streamResponse"] = True
        params = ui.setdefault("params", {})
        if isinstance(params, dict):
            params["stream_response"] = True
            params["function_calling"] = "native"
        cur.execute("UPDATE user SET settings=? WHERE id=?", (json.dumps(s), uid))

    con.commit()
    print("web_search_enabled users=", len(users))


if __name__ == "__main__":
    main()
