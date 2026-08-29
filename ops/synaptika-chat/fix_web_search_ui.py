#!/usr/bin/env python3
"""Force web_search capability + defaultFeatureIds on synaptika-chat-auto.

base_model_id MUST be NULL so Open WebUI treats this row as an in-place
override of the connection model (same id). If base_model_id == id, the
override is skipped and the globe never appears.
"""
import json
import sqlite3
import time
from pathlib import Path

DB = "/app/backend/data/webui.db"
PROMPT_FILE = Path("/srv/SYSTEM_PROMPT.md")
MID = "synaptika-chat-auto"


def main() -> None:
    prompt = ""
    if PROMPT_FILE.exists():
        prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()

    meta = {
        "description": "Chat general con busqueda web para noticias y datos actuales",
        "capabilities": {
            "vision": False,
            "file_upload": True,
            "web_search": True,
            "image_generation": False,
            "code_interpreter": False,
            "citations": True,
        },
        "defaultFeatureIds": ["web_search"],
        "suggestion_prompts": [],
    }
    params = {
        "system": prompt,
        "stream_response": True,
        "function_calling": "native",
        "temperature": 0.5,
        "max_tokens": 2048,
    }

    now = int(time.time())
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(model)")}
    existing = cur.execute("SELECT id FROM model WHERE id=?", (MID,)).fetchone()

    # CRITICAL: base_model_id = NULL for in-place override
    if existing:
        cur.execute(
            """
            UPDATE model
            SET name=?,
                base_model_id=NULL,
                meta=?,
                params=?,
                is_active=1,
                updated_at=?
            WHERE id=?
            """,
            (
                "Synaptika Chat Auto",
                json.dumps(meta),
                json.dumps(params),
                now,
                MID,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO model (id, user_id, base_model_id, name, meta, params, is_active, updated_at, created_at)
            VALUES (?, '', NULL, ?, ?, ?, 1, ?, ?)
            """,
            (MID, "Synaptika Chat Auto", json.dumps(meta), json.dumps(params), now, now),
        )

    # Ensure user UI has web search preference
    for uid, settings in cur.execute("SELECT id, settings FROM user"):
        try:
            s = json.loads(settings) if settings else {}
        except json.JSONDecodeError:
            s = {}
        if not isinstance(s, dict):
            s = {}
        ui = s.setdefault("ui", {})
        if isinstance(ui, dict):
            ui["webSearch"] = True
            ui["streamResponse"] = True
            if prompt:
                ui["system"] = prompt
        cur.execute("UPDATE user SET settings=? WHERE id=?", (json.dumps(s), uid))

    # Persist web search enable keys
    def upsert(key, value):
        payload = json.dumps(value)
        row = cur.execute("SELECT key FROM config WHERE key=?", (key,)).fetchone()
        if row:
            cur.execute(
                "UPDATE config SET value=?, updated_at=? WHERE key=?",
                (payload, now, key),
            )
        else:
            cur.execute(
                "INSERT INTO config(key, value, updated_at) VALUES (?,?,?)",
                (key, payload, now),
            )

    upsert("web.search.enable", True)
    upsert("web.search.engine", "duckduckgo")
    upsert("web.search.bypass_embedding_and_retrieval", True)
    upsert(
        "models.default_metadata",
        {
            "capabilities": {"web_search": True, "citations": True, "file_upload": True},
            "defaultFeatureIds": ["web_search"],
        },
    )

    con.commit()
    row = cur.execute(
        "SELECT id, base_model_id, meta FROM model WHERE id=?", (MID,)
    ).fetchone()
    print("fixed", row[0], "base_model_id", row[1], "meta_head", str(row[2])[:180])


if __name__ == "__main__":
    main()
