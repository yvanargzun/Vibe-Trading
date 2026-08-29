#!/usr/bin/env python3
"""Stop auto-enabling web search on every turn (hangs follow-ups on low-RAM VPS).

Keep web_search capability; user toggles the globe when needed.
Also nudge UI defaults toward auto/chat for reliable multi-turn replies.
"""
import json
import sqlite3
import time

DB = "/app/backend/data/webui.db"
now = int(time.time())


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # Clear defaultFeatureIds on custom models so web search is not forced ON
    for mid, meta_raw, params_raw in cur.execute("SELECT id, meta, params FROM model"):
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except json.JSONDecodeError:
            meta = {}
        if not isinstance(meta, dict):
            continue
        caps = meta.get("capabilities") if isinstance(meta.get("capabilities"), dict) else {}
        caps["web_search"] = True
        caps["citations"] = True
        meta["capabilities"] = caps
        meta["defaultFeatureIds"] = []  # was ["web_search"] — caused follow-up hangs
        try:
            params = json.loads(params_raw) if params_raw else {}
        except json.JSONDecodeError:
            params = {}
        if isinstance(params, dict):
            params["stream_response"] = True
            params["function_calling"] = "legacy"
            params["max_tokens"] = max(int(params.get("max_tokens") or 0), 4096)
        cur.execute(
            "UPDATE model SET meta=?, params=?, updated_at=? WHERE id=?",
            (json.dumps(meta), json.dumps(params), now, mid),
        )
        print("model_updated", mid)

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
            # Prefer a chatty OmniRoute combo that emits content quickly
            ui["models"] = ["auto/chat"]
            params_ui = ui.setdefault("params", {})
            if isinstance(params_ui, dict):
                params_ui["function_calling"] = "legacy"
                params_ui["stream_response"] = True
                params_ui["max_tokens"] = 4096
        cur.execute("UPDATE user SET settings=? WHERE id=?", (json.dumps(s), uid))
        print("user_ui_updated", uid)

    # config default metadata
    payload = json.dumps(
        {
            "capabilities": {"web_search": True, "citations": True, "file_upload": True},
            "defaultFeatureIds": [],
        }
    )
    if cur.execute("SELECT 1 FROM config WHERE key=?", ("models.default_metadata",)).fetchone():
        cur.execute(
            "UPDATE config SET value=?, updated_at=? WHERE key=?",
            (payload, now, "models.default_metadata"),
        )
    else:
        cur.execute(
            "INSERT INTO config(key, value, updated_at) VALUES (?,?,?)",
            ("models.default_metadata", payload, now),
        )

    con.commit()
    print("ok_followup_defaults")


if __name__ == "__main__":
    main()
