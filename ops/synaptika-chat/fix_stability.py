#!/usr/bin/env python3
"""Stabilize Open WebUI chat UI against flicker / disappearing replies.

- Force a single OpenAI connection (OmniRoute)
- Prefer a short stable model list in user UI settings
- Keep web search OFF by default
- Raise stream-friendly params
"""
import json
import sqlite3
import time

DB = "/app/backend/data/webui.db"
now = int(time.time())
STABLE = ["auto/chat", "auto/fast", "auto/best-free", "auto/coding:free"]


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


def main() -> None:
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # Single connection list in persisted config (belt + suspenders with env)
    upsert(cur, "openai.api_base_urls", ["http://omniroute:20128/v1"])
    upsert(cur, "openai.api_keys", ["omniroute"])
    upsert(cur, "models.base_models_cache", True)
    upsert(cur, "ui.default_pinned_models", "auto/chat;auto/fast;auto/best-free")
    upsert(cur, "ui.model_order_list", STABLE)
    upsert(
        cur,
        "models.default_metadata",
        {
            "capabilities": {"web_search": True, "citations": True, "file_upload": True},
            "defaultFeatureIds": [],
        },
    )

    for mid, meta_raw, params_raw in cur.execute("SELECT id, meta, params FROM model"):
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
        except json.JSONDecodeError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["defaultFeatureIds"] = []
        caps = meta.get("capabilities") if isinstance(meta.get("capabilities"), dict) else {}
        caps["web_search"] = True
        meta["capabilities"] = caps
        try:
            params = json.loads(params_raw) if params_raw else {}
        except json.JSONDecodeError:
            params = {}
        if not isinstance(params, dict):
            params = {}
        params["stream_response"] = True
        params["function_calling"] = "legacy"
        params["max_tokens"] = 2048
        params["temperature"] = 0.4
        cur.execute(
            "UPDATE model SET meta=?, params=?, updated_at=? WHERE id=?",
            (json.dumps(meta), json.dumps(params), now, mid),
        )
        print("model", mid)

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
            ui["models"] = ["auto/chat"]
            ui["pinnedModels"] = STABLE[:3]
            params_ui = ui.setdefault("params", {})
            if isinstance(params_ui, dict):
                params_ui["function_calling"] = "legacy"
                params_ui["stream_response"] = True
                params_ui["max_tokens"] = 2048
                params_ui["temperature"] = 0.4
        cur.execute("UPDATE user SET settings=? WHERE id=?", (json.dumps(s), uid))
        print("user", uid)

    con.commit()
    print("ok_stability")


if __name__ == "__main__":
    main()
