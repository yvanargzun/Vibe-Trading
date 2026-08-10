#!/usr/bin/env python3
"""Configure Synaptika Copiloto: Ops tools + on-topic system prompt."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DB = os.environ.get("WEBUI_DB", "/app/backend/data/webui.db")
PROMPT_PATH = Path(os.environ.get("SYSTEM_PROMPT_FILE", "/srv/webui/SYSTEM_PROMPT.md"))
FREE_PATH = Path(os.environ.get("FREE_MODELS_FILE", "/srv/webui/free_models.json"))
DEFAULT_BASE = os.environ.get("DEFAULT_MODELS", "synaptika-auto")
COPILOT_ID = "synaptika-copiloto"
OLLAMA_COPILOT_ID = "synaptika-ollama"
OLLAMA_DEFAULT_BASE = os.environ.get("OLLAMA_DEFAULT_MODEL", "gemma4:31b")
PROXY_MODEL = os.environ.get("LLM_PROXY_MODEL", "synaptika-auto")
OPS_URL = os.environ.get("OPS_TOOL_URL", "http://ops:8787")
OPS_OPENAPI_PATH = os.environ.get("OPS_TOOL_OPENAPI_PATH", "/ops/api/openapi.json")
OPS_API_KEY = os.environ.get("OPS_API_KEY", "").strip()
FILTER_ID = "synaptika_ops_context"
FILTER_PATH = Path(
    os.environ.get("OPS_FILTER_FILE", "/srv/webui/ops_context_filter.py")
)
OLLAMA_BASE = os.environ.get(
    "OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1"
).rstrip("/")
OLLAMA_KEY = (
    os.environ.get("OLLAMA_API_KEY", "").strip()
    or os.environ.get("OLLAMA_CLOUD_API_KEY", "").strip()
)

SUGGESTIONS = [
    {
        "title": ["Estado + restricciones", "Binance y Alpaca"],
        "content": "Con el brief live: resume equity, modo, day PnL, HALT/day-loss y si cada bot puede comprar ahora.",
    },
    {
        "title": ["Wins/Losses", "qué muestran los cierres"],
        "content": "Analiza wins/losses de Binance y Alpaca (totales y hoy) y qué patrones ves en los últimos cierres.",
    },
    {
        "title": ["Cambiar estrategia", "ejecutar ya"],
        "content": "Revisa get_control_status y aplica el cambio de modo o knobs que haga falta con las write tools (sin pedirme confirmación). Luego reporta el resultado real.",
    },
    {
        "title": ["Por qué standby", "Binance"],
        "content": "¿Por qué Binance está en standby y qué condiciones harían que salga? Usa flips, day PnL y usable del brief.",
    },
]


def upsert_config(cur: sqlite3.Cursor, key: str, value) -> None:
    cur.execute(
        "INSERT INTO config(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value), int(time.time())),
    )


def load_prompt() -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"empty prompt: {PROMPT_PATH}")
    return text


def load_free_models() -> list[str]:
    try:
        models = json.loads(FREE_PATH.read_text(encoding="utf-8"))
        return [m for m in models if isinstance(m, str) and m.endswith(":free")]
    except Exception:
        return [DEFAULT_BASE]


def admin_user_id(cur: sqlite3.Cursor) -> str:
    row = cur.execute(
        "SELECT id FROM user WHERE role='admin' ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    if not row:
        raise SystemExit("no admin user in webui.db")
    return row[0]


def upsert_model(
    cur: sqlite3.Cursor,
    *,
    model_id: str,
    user_id: str,
    base_model_id: str | None,
    name: str,
    prompt: str,
) -> None:
    now = int(time.time())
    params = {
        "system": prompt,
        # Digest arrives via global filter. Keep native tools for write Ops,
        # but stream so the UI does not look frozen while the proxy fails over.
        # llm-proxy wraps the final completion as SSE when stream=true.
        "stream_response": True,
        "max_tokens": 2048,
        "temperature": 0.4,
        "function_calling": "native",
    }
    meta = {
        "description": "Copiloto Synaptika Trade — bots VPS + control (modo/HALT/knobs/órdenes); ejecuta al pedir el usuario.",
        "filterIds": [FILTER_ID],
        # Open WebUI OpenAPI tool server id "0" (Synaptika Ops).
        "toolIds": ["server:0"],
        "capabilities": {
            "vision": False,
            "file_upload": False,
            "web_search": False,
            "image_generation": False,
            "code_interpreter": False,
            "citations": True,
            "usage": True,
        },
        "suggestion_prompts": [s["content"] for s in SUGGESTIONS],
    }
    existing = cur.execute("SELECT id FROM model WHERE id=?", (model_id,)).fetchone()
    if existing:
        cur.execute(
            "UPDATE model SET user_id=?, base_model_id=?, name=?, params=?, meta=?, "
            "updated_at=?, is_active=1 WHERE id=?",
            (
                user_id,
                base_model_id,
                name,
                json.dumps(params),
                json.dumps(meta),
                now,
                model_id,
            ),
        )
    else:
        cur.execute(
            "INSERT INTO model(id, user_id, base_model_id, name, params, meta, "
            "updated_at, created_at, is_active) VALUES(?,?,?,?,?,?,?,?,1)",
            (
                model_id,
                user_id,
                base_model_id,
                name,
                json.dumps(params),
                json.dumps(meta),
                now,
                now,
            ),
        )


def upsert_filter(cur: sqlite3.Cursor, user_id: str) -> None:
    now = int(time.time())
    content = FILTER_PATH.read_text(encoding="utf-8")
    meta = {
        "description": "Inyecta digest live de Ops y guarda historial de cada turno.",
        "manifest": {"title": "Synaptika Ops Context", "version": "0.4.0"},
    }
    valves = {
        "copilot_url": f"{OPS_URL.rstrip('/')}/ops/api/copilot",
        "digest_url": f"{OPS_URL.rstrip('/')}/ops/api/digest",
        "winloss_url": f"{OPS_URL.rstrip('/')}/ops/api/winloss",
        "activity_url": f"{OPS_URL.rstrip('/')}/ops/api/activity?limit=40",
        "status_url": f"{OPS_URL.rstrip('/')}/ops/api/status",
        "ops_api_key": OPS_API_KEY,
        "timeout_sec": 12,
        "history_dir": "/data/chat_history",
        "save_history": True,
    }
    existing = cur.execute(
        "SELECT id FROM function WHERE id=?", (FILTER_ID,)
    ).fetchone()
    if existing:
        cur.execute(
            "UPDATE function SET user_id=?, name=?, type=?, content=?, meta=?, valves=?, "
            "is_active=1, is_global=1, updated_at=? WHERE id=?",
            (
                user_id,
                "Synaptika Ops Context",
                "filter",
                content,
                json.dumps(meta),
                json.dumps(valves),
                now,
                FILTER_ID,
            ),
        )
    else:
        cur.execute(
            "INSERT INTO function(id, user_id, name, type, content, meta, valves, "
            "is_active, is_global, updated_at, created_at) "
            "VALUES(?,?,?,?,?,?,?,1,1,?,?)",
            (
                FILTER_ID,
                user_id,
                "Synaptika Ops Context",
                "filter",
                content,
                json.dumps(meta),
                json.dumps(valves),
                now,
                now,
            ),
        )


def load_ollama_models() -> list[str]:
    if not OLLAMA_KEY:
        return []
    try:
        import urllib.request

        req = urllib.request.Request(
            f"{OLLAMA_BASE}/models",
            headers={
                "Authorization": f"Bearer {OLLAMA_KEY}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [
            m["id"]
            for m in data.get("data", [])
            if isinstance(m.get("id"), str) and m["id"].strip()
        ]
    except Exception as exc:  # noqa: BLE001
        print(f"WARN ollama models: {exc}", file=sys.stderr)
        return []


def main() -> int:
    if not OPS_API_KEY:
        print("WARN: OPS_API_KEY empty — tools/filter may 401", file=sys.stderr)

    prompt = load_prompt()
    free = load_free_models()
    # Copiloto always routes through failover proxy model
    copiloto_base = PROXY_MODEL if PROXY_MODEL else DEFAULT_BASE
    if DEFAULT_BASE not in free and not str(DEFAULT_BASE).startswith("synaptika"):
        free = [DEFAULT_BASE] + free
    ollama = load_ollama_models()
    ollama_base = OLLAMA_DEFAULT_BASE if OLLAMA_DEFAULT_BASE in ollama else (
        ollama[0] if ollama else None
    )

    connections = [
        {
            "url": OPS_URL,
            "path": OPS_OPENAPI_PATH,
            "type": "openapi",
            "auth_type": "bearer",
            "key": OPS_API_KEY,
            "config": {"enable": True},
            "info": {
                "id": "0",
                "name": "Synaptika Ops",
                "description": (
                    "API Ops: lectura + control (halt/mode/knobs/intents) para "
                    "Binance y Alpaca paper. Write tools se ejecutan al pedir el usuario (sin OK extra)."
                ),
            },
        }
    ]

    con = sqlite3.connect(DB, timeout=60)
    try:
        cur = con.cursor()
        uid = admin_user_id(cur)

        upsert_config(cur, "tool_server.connections", connections)
        upsert_config(cur, "ui.default_models", COPILOT_ID)
        upsert_config(cur, "ui.prompt_suggestions", SUGGESTIONS)
        upsert_config(
            cur,
            "models.default_params",
            {
                "system": prompt,
                "stream_response": True,
                "max_tokens": 2048,
                "temperature": 0.4,
                "function_calling": "native",
            },
        )
        upsert_filter(cur, uid)

        upsert_model(
            cur,
            model_id=COPILOT_ID,
            user_id=uid,
            base_model_id=copiloto_base,
            name="Synaptika Copiloto",
            prompt=prompt,
        )

        if ollama_base:
            upsert_model(
                cur,
                model_id=OLLAMA_COPILOT_ID,
                user_id=uid,
                base_model_id=ollama_base,
                name="Synaptika · Ollama Cloud",
                prompt=prompt,
            )

        for mid in free:
            short = mid.split("/")[-1].replace(":free", "")
            upsert_model(
                cur,
                model_id=mid,
                user_id=uid,
                base_model_id=None,
                name=f"Synaptika · {short}",
                prompt=prompt,
            )

        for mid in ollama:
            short = mid.replace(":", "·")
            upsert_model(
                cur,
                model_id=mid,
                user_id=uid,
                base_model_id=None,
                name=f"Ollama Cloud · {short}",
                prompt=prompt,
            )

        con.commit()
    finally:
        con.close()

    print(
        f"ok copiloto id={COPILOT_ID} base={copiloto_base} "
        f"ollama_copilot={OLLAMA_COPILOT_ID if ollama_base else 'off'} "
        f"base_ollama={ollama_base} free={len(free)} ollama={len(ollama)}"
    )
    print(f"tools -> {OPS_URL}{OPS_OPENAPI_PATH} bearer={'yes' if OPS_API_KEY else 'no'}")
    print(f"filter -> {FILTER_ID} global=yes")

    # Full export of existing chats into shared historial folder
    try:
        from export_chat_history import export_all  # type: ignore

        export_all()
    except Exception as exc:
        # Prefer sibling module path inside container
        try:
            import importlib.util

            path = Path("/srv/webui/export_chat_history.py")
            spec = importlib.util.spec_from_file_location("export_chat_history", path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.export_all()
            else:
                print(f"WARN historial export skipped: {exc}", file=sys.stderr)
        except Exception as exc2:
            print(f"WARN historial export skipped: {exc2}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
