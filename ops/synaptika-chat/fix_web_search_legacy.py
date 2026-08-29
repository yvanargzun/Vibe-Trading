#!/usr/bin/env python3
"""Fix model override so Web Search actually runs for free models.

- created_at must be int (was NULL → ModelModel skipped)
- base_model_id NULL = in-place override
- function_calling=legacy forces OWUI to search BEFORE calling the LLM
  (native mode waits for tool calls that free models never make)
"""
import json
import sqlite3
import time
from pathlib import Path

DB = "/app/backend/data/webui.db"
PROMPT_FILE = Path("/srv/SYSTEM_PROMPT.md")
MID = "synaptika-chat-auto"


def main() -> None:
    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip() if PROMPT_FILE.exists() else ""
    now = int(time.time())
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
        "defaultFeatureIds": [],
        "suggestion_prompts": [],
    }
    params = {
        "system": prompt,
        "stream_response": True,
        # CRITICAL for free models: legacy forces server-side web search
        "function_calling": "legacy",
        "temperature": 0.5,
        "max_tokens": 4096,
    }

    con = sqlite3.connect(DB)
    cur = con.cursor()
    existing = cur.execute(
        "SELECT created_at FROM model WHERE id=?", (MID,)
    ).fetchone()
    created = int(existing[0]) if existing and existing[0] else now

    cur.execute("DELETE FROM model WHERE id=?", (MID,))
    cur.execute(
        """
        INSERT INTO model (
            id, user_id, base_model_id, name, meta, params,
            is_active, updated_at, created_at
        ) VALUES (?, '', NULL, ?, ?, ?, 1, ?, ?)
        """,
        (
            MID,
            "Synaptika Chat Auto",
            json.dumps(meta),
            json.dumps(params),
            now,
            created,
        ),
    )

    # Validate like Open WebUI would
    try:
        from open_webui.models.models import ModelModel

        row = dict(
            zip(
                [r[1] for r in cur.execute("PRAGMA table_info(model)")],
                cur.execute("SELECT * FROM model WHERE id=?", (MID,)).fetchone(),
            )
        )
        # meta/params are JSON columns in ORM but TEXT in sqlite raw
        row["meta"] = json.loads(row["meta"])
        row["params"] = json.loads(row["params"])
        ModelModel.model_validate(row)
        print("ModelModel_OK")
    except Exception as e:
        print("ModelModel_FAIL", e)

    for uid, settings in cur.execute("SELECT id, settings FROM user"):
        try:
            s = json.loads(settings) if settings else {}
        except json.JSONDecodeError:
            s = {}
        if not isinstance(s, dict):
            s = {}
        ui = s.setdefault("ui", {})
        if isinstance(ui, dict):
            ui["webSearch"] = False
            ui["streamResponse"] = True
            params_ui = ui.setdefault("params", {})
            if isinstance(params_ui, dict):
                params_ui["function_calling"] = "legacy"
                params_ui["stream_response"] = True
                params_ui["max_tokens"] = 4096
            if prompt:
                ui["system"] = prompt
        cur.execute("UPDATE user SET settings=? WHERE id=?", (json.dumps(s), uid))

    def upsert(key, value):
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

    upsert("web.search.enable", True)
    upsert("web.search.engine", "duckduckgo")
    upsert("web.search.bypass_embedding_and_retrieval", True)
    upsert("web.search.bypass_web_loader", True)
    # Avoid LLM rewriting queries to "... 13 agosto 2026" (junk DDG hits)
    upsert("task.query.search.enable", False)
    upsert(
        "models.default_metadata",
        {
            "capabilities": {"web_search": True, "citations": True, "file_upload": True},
            "defaultFeatureIds": [],
        },
    )

    con.commit()
    print("fixed_legacy_web_search", MID, "created_at", created)


if __name__ == "__main__":
    main()
