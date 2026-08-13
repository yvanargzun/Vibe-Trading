#!/usr/bin/env python3
"""Configure Open WebUI OpenAI connections: llm-proxy + OpenRouter + Ollama Cloud."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

DB = os.environ.get("WEBUI_DB", "/app/backend/data/webui.db")
FALLBACK_PATH = os.environ.get(
    "FREE_MODELS_FILE", "/srv/webui/free_models.json"
)
PROXY_BASE = os.environ.get("LLM_PROXY_BASE_URL", "http://llm-proxy:4000/v1").rstrip("/")
PROXY_MODEL = os.environ.get("LLM_PROXY_MODEL", "synaptika-auto")
DEFAULT_MODEL = os.environ.get("DEFAULT_MODELS", PROXY_MODEL)
COPILOT_MAX_TOKENS = int(os.environ.get("COPILOT_MAX_TOKENS", "3072"))
OPENROUTER_BASE = os.environ.get(
    "OPENROUTER_BASE_URL",
    os.environ.get("OPENAI_UPSTREAM_BASE_URL", "https://openrouter.ai/api/v1"),
).rstrip("/")
OPENROUTER_KEY = (
    os.environ.get("OPENROUTER_API_KEY", "").strip()
    or os.environ.get("OPENAI_UPSTREAM_API_KEY", "").strip()
    or os.environ.get("OPENAI_API_KEY", "").strip()
)
if OPENROUTER_KEY in ("synaptika-proxy", "unused", "proxy"):
    OPENROUTER_KEY = (
        os.environ.get("OPENROUTER_API_KEY", "").strip()
        or os.environ.get("OPENAI_UPSTREAM_API_KEY", "").strip()
    )
OLLAMA_BASE = os.environ.get(
    "OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1"
).rstrip("/")
OLLAMA_KEY = (
    os.environ.get("OLLAMA_API_KEY", "").strip()
    or os.environ.get("OLLAMA_CLOUD_API_KEY", "").strip()
)

OLLAMA_PREFERRED = [
    "deepseek-v4-flash",
    "gpt-oss:20b",
    "gpt-oss:120b",
    "gemma4:31b",
    "minimax-m2.7",
    "minimax-m3",
    "qwen3.5:397b",
    "kimi-k2.6",
    "glm-5.1",
    "glm-5.2",
    "deepseek-v4-pro",
    "nemotron-3-nano:30b",
    "nemotron-3-super",
]


def _get_json(url: str, key: str) -> dict:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_free_models() -> list[str]:
    if not OPENROUTER_KEY:
        return []
    try:
        data = _get_json(f"{OPENROUTER_BASE}/models", OPENROUTER_KEY)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print(f"openrouter models fail: {exc}", file=sys.stderr)
        return []
    return sorted(
        m["id"]
        for m in data.get("data", [])
        if isinstance(m.get("id"), str) and m["id"].endswith(":free")
    )


def fetch_ollama_models() -> list[str]:
    if not OLLAMA_KEY:
        return []
    try:
        data = _get_json(f"{OLLAMA_BASE}/models", OLLAMA_KEY)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print(f"ollama cloud models fail: {exc}", file=sys.stderr)
        return []
    ids = [
        m["id"]
        for m in data.get("data", [])
        if isinstance(m.get("id"), str) and m["id"].strip()
    ]
    preferred = [m for m in OLLAMA_PREFERRED if m in ids]
    rest = sorted(m for m in ids if m not in preferred)
    return preferred + rest


def load_fallback() -> list[str]:
    try:
        with open(FALLBACK_PATH, encoding="utf-8") as f:
            models = json.load(f)
        return [m for m in models if isinstance(m, str) and m.endswith(":free")]
    except Exception as exc:  # noqa: BLE001
        print(f"fallback load failed: {exc}", file=sys.stderr)
        return ["google/gemma-4-31b-it:free", "inclusionai/ling-3.0-flash:free"]


def upsert(cur: sqlite3.Cursor, key: str, value) -> None:
    payload = json.dumps(value)
    now = int(time.time())
    cur.execute(
        "INSERT INTO config(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, payload, now),
    )


def main() -> int:
    free = fetch_free_models() or load_fallback()
    ollama = fetch_ollama_models()

    api_configs: dict = {
        "0": {
            "enable": True,
            "url": PROXY_BASE,
            "key": "synaptika-proxy",
            "model_ids": [PROXY_MODEL],
            "connection_type": "external",
            "prefix_id": "",
            "tags": ["auto", "failover"],
            "provider": "synaptika-proxy",
        }
    }
    n = 1
    if OPENROUTER_KEY:
        api_configs[str(n)] = {
            "enable": True,
            "url": OPENROUTER_BASE,
            "key": OPENROUTER_KEY,
            "model_ids": free,
            "connection_type": "external",
            "prefix_id": "",
            "tags": ["free", "openrouter"],
            "provider": "openrouter",
        }
        n += 1
    if OLLAMA_KEY:
        api_configs[str(n)] = {
            "enable": True,
            "url": OLLAMA_BASE,
            "key": OLLAMA_KEY,
            "model_ids": ollama,
            "connection_type": "external",
            "prefix_id": "",
            "tags": ["ollama-cloud"],
            "provider": "ollama-cloud",
        }

    default_params = {
        "stream_response": False,
        "max_tokens": COPILOT_MAX_TOKENS,
        "temperature": 0.4,
    }
    order = [PROXY_MODEL] + list(free) + list(ollama)

    con = sqlite3.connect(DB, timeout=60)
    try:
        cur = con.cursor()
        upsert(cur, "ollama.enable", False)
        upsert(cur, "openai.enable", True)
        upsert(cur, "openai.api_configs", api_configs)
        bases = [PROXY_BASE]
        keys = ["synaptika-proxy"]
        if OPENROUTER_KEY:
            bases.append(OPENROUTER_BASE)
            keys.append(OPENROUTER_KEY)
        if OLLAMA_KEY:
            bases.append(OLLAMA_BASE)
            keys.append(OLLAMA_KEY)
        upsert(cur, "openai.api_base_urls", bases)
        upsert(cur, "openai.api_keys", keys)
        upsert(cur, "ui.default_models", DEFAULT_MODEL)
        upsert(cur, "ui.model_order_list", order)
        upsert(cur, "models.default_params", default_params)
        upsert(cur, "evaluation.arena.enable", False)
        con.commit()
    finally:
        con.close()

    print(
        f"ok proxy={PROXY_MODEL} free={len(free)} ollama_cloud={len(ollama)} "
        f"default={DEFAULT_MODEL} openrouter_key={'yes' if OPENROUTER_KEY else 'no'} "
        f"ollama_key={'yes' if OLLAMA_KEY else 'no'}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
