#!/usr/bin/env python3
"""Attach Synaptika Chat system prompt to default model + user."""
import json
import sqlite3
from pathlib import Path

DB = "/app/backend/data/webui.db"
PROMPT_FILE = Path("/srv/SYSTEM_PROMPT.md")


def main() -> None:
    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # model table (custom model / params)
    cols = [r[1] for r in cur.execute("PRAGMA table_info(model)")]
    print("model_cols", cols)
    if cols:
        rows = cur.execute("SELECT id, name FROM model").fetchall()
        print("models", rows[:20])
        # Upsert a custom model wrapper if supported
        if "id" in cols and "meta" in cols:
            meta = {
                "description": "Chat general con web search",
                "capabilities": {
                    "vision": False,
                    "file_upload": True,
                    "web_search": True,
                    "image_generation": False,
                    "code_interpreter": False,
                    "citations": True,
                },
                "suggestion_prompts": [],
            }
            params = {
                "system": prompt,
                "stream_response": True,
                "function_calling": "native",
                "temperature": 0.5,
                "max_tokens": 2048,
            }
            mid = "synaptika-chat-auto"
            existing = cur.execute("SELECT id FROM model WHERE id=?", (mid,)).fetchone()
            # base_model_id MUST be NULL for in-place override of connection model
            payload = {
                "id": mid,
                "user_id": "",
                "base_model_id": None,
                "name": "Synaptika Chat Auto",
                "meta": json.dumps(meta),
                "params": json.dumps(params),
                "is_active": True,
            }
            meta["defaultFeatureIds"] = ["web_search"]
            payload["meta"] = json.dumps(meta)
            # Adapt to available columns
            if existing:
                cur.execute(
                    """
                    UPDATE model
                    SET name=?, base_model_id=NULL, meta=?, params=?, is_active=1
                    WHERE id=?
                    """,
                    (
                        payload["name"],
                        payload["meta"],
                        payload["params"],
                        mid,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO model (id, user_id, base_model_id, name, meta, params, is_active, updated_at, created_at)
                    VALUES (?, '', NULL, ?, ?, ?, 1, strftime('%s','now'), strftime('%s','now'))
                    """,
                    (mid, payload["name"], payload["meta"], payload["params"]),
                )
            print("model_prompt_set", mid, "base_model_id=NULL")

    # Also stash on user settings as system prompt default if key exists
    users = cur.execute("SELECT id, settings FROM user").fetchall()
    for uid, settings in users:
        try:
            s = json.loads(settings) if settings else {}
        except json.JSONDecodeError:
            s = {}
        if not isinstance(s, dict):
            s = {}
        ui = s.setdefault("ui", {})
        if isinstance(ui, dict):
            ui["webSearch"] = True
            ui["system"] = prompt
        cur.execute("UPDATE user SET settings=? WHERE id=?", (json.dumps(s), uid))

    con.commit()
    print("done")


if __name__ == "__main__":
    main()
