#!/usr/bin/env python3
"""Shared Telegram notify prefs for Synaptika (Vibe + Scalper + FB + sales-test)."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("VIBE_TRADING_HOME", "/root/.vibe-trading"))
ENV_PATH = HOME / ".env"
PREFS_PATH = HOME / "telegram_notify_prefs.json"
DEDUPE_PATH = HOME / "telegram_send_dedupe.json"

# mode: vibe | scalp15 | fb | all
# "scalper" kept as alias → scalp15 (ETH scalper removed)
DEFAULT_MODE = "all"
VALID_MODES = frozenset({"vibe", "scalp15", "scalper", "fb", "all", "both"})
DEDUPE_WINDOW_SEC = 90

BTN_VIBE = "Solo Binance v6"
BTN_SCALP15 = "Solo Alpaca scalp15"
BTN_SCALPER = BTN_SCALP15  # legacy alias
BTN_FB = "Solo clientes FB"
BTN_ALL = "Todos (Binance+Alpaca15+FB)"
BTN_SALES_TEST = "Probar Messenger sales"
BTN_SALES_EXIT = "Salir Messenger sales"
# legacy button text still accepted
BTN_BOTH_LEGACY = "Ambas (Vibe + FB)"
BTN_SCALPER_LEGACY = "Solo Scalper"

BUTTON_TO_MODE = {
    BTN_VIBE: "vibe",
    BTN_SCALP15: "scalp15",
    BTN_SCALPER_LEGACY: "scalp15",
    "Solo Vibe trading": "vibe",
    "Solo Scalper": "scalp15",
    "Todos (Vibe+Scalper+FB)": "all",
    BTN_FB: "fb",
    BTN_ALL: "all",
    BTN_BOTH_LEGACY: "all",
    "vibe": "vibe",
    "scalper": "scalp15",
    "scalp15": "scalp15",
    "fb": "fb",
    "ambas": "all",
    "both": "all",
    "all": "all",
    "todos": "all",
}


def load_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    if not ENV_PATH.exists():
        return vals
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _normalize_mode(mode: str) -> str:
    mode = (mode or DEFAULT_MODE).lower().strip()
    if mode == "both":
        return "all"
    if mode == "scalper":
        return "scalp15"
    if mode in ("vibe", "scalp15", "fb", "all"):
        return mode
    return DEFAULT_MODE


def load_prefs() -> dict:
    if PREFS_PATH.exists():
        try:
            doc = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            mode = _normalize_mode(str(doc.get("mode") or DEFAULT_MODE))
            return {
                "mode": mode,
                "sales_test": bool(doc.get("sales_test")),
                "updated_ts": doc.get("updated_ts"),
            }
        except json.JSONDecodeError:
            pass
    return {"mode": DEFAULT_MODE, "sales_test": False}


def _write_prefs(doc: dict) -> dict:
    out = {
        "mode": _normalize_mode(str(doc.get("mode") or DEFAULT_MODE)),
        "sales_test": bool(doc.get("sales_test")),
        "updated_ts": int(time.time()),
    }
    PREFS_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def save_prefs(mode: str) -> dict:
    doc = load_prefs()
    doc["mode"] = _normalize_mode(mode)
    return _write_prefs(doc)


def is_sales_test() -> bool:
    return bool(load_prefs().get("sales_test"))


def set_sales_test(enabled: bool) -> dict:
    doc = load_prefs()
    doc["sales_test"] = bool(enabled)
    return _write_prefs(doc)


def should_notify(channel: str) -> bool:
    """channel: 'vibe' | 'scalp15' | 'alpaca' | 'fb' (+ legacy 'scalper'→scalp15).

    - vibe: Binance smart-fast-v6 fills + digests
    - scalp15: Alpaca 15m momentum scalper
    - alpaca: treated as vibe (core Alpaca paper) unless filtered separately later
    - fb: Synaptica client appointments
    """
    ch = (channel or "").lower().strip()
    if ch == "scalper":
        ch = "scalp15"
    if ch == "alpaca":
        ch = "vibe"
    # While owner is testing Messenger sales in this chat, hush trading digests.
    if is_sales_test() and ch in ("vibe", "scalp15"):
        print("TG_SALES_TEST_SKIP channel=", channel)
        return False
    mode = load_prefs().get("mode") or DEFAULT_MODE
    if mode in ("all", "both"):
        return True
    return mode == ch


def tg_api(method: str, payload: dict) -> dict:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "description": "no token"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"ok": False, "description": str(e)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "description": str(exc)}


def _digest_key(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


def was_recently_sent(text: str, window_sec: int = DEDUPE_WINDOW_SEC) -> bool:
    now = time.time()
    doc: dict = {}
    if DEDUPE_PATH.exists():
        try:
            doc = json.loads(DEDUPE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            doc = {}
    key = _digest_key(text)
    keys = dict(doc.get("keys") or {})
    kind = "digest" if text.strip().startswith(("Resumen Vibe", "[Binance]")) else key
    prev_kind = float(keys.get(f"kind:{kind}") or 0)
    prev = float(keys.get(key) or 0)
    if (prev and (now - prev) < window_sec) or (prev_kind and (now - prev_kind) < window_sec):
        return True
    keys = {k: v for k, v in keys.items() if now - float(v) < window_sec * 3}
    keys[key] = now
    keys[f"kind:{kind}"] = now
    DEDUPE_PATH.write_text(json.dumps({"keys": keys}, indent=2) + "\n", encoding="utf-8")
    return False


def send_text(
    text: str,
    *,
    channel: str = "vibe",
    reply_markup: dict | None = None,
    dedupe: bool = True,
    force: bool = False,
) -> bool:
    if not force and not should_notify(channel):
        print(f"TG_FILTER_SKIP channel={channel} mode={load_prefs().get('mode')}")
        return False
    if dedupe and was_recently_sent(text):
        print("TG_DEDUPE_SKIP", _digest_key(text))
        return False
    env = load_env()
    chat = env.get("TELEGRAM_CHAT_ID", "")
    if not chat:
        return False
    payload: dict = {
        "chat_id": chat,
        "text": text[:3500],
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return bool(tg_api("sendMessage", payload).get("ok"))


def filter_keyboard() -> dict:
    if is_sales_test():
        # Igual que menú persistente de Messenger FB (+ salir solo owner)
        return {
            "keyboard": [
                [{"text": "Agendar cita"}, {"text": "Ver ejemplos"}],
                [{"text": "Hablar con humano"}],
                [{"text": BTN_SALES_EXIT}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
        }
    return {
        "keyboard": [
            [{"text": BTN_VIBE}],
            [{"text": BTN_SCALP15}],
            [{"text": BTN_FB}],
            [{"text": BTN_ALL}],
            [{"text": BTN_SALES_TEST}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def mode_label(mode: str) -> str:
    mode = _normalize_mode(mode)
    return {
        "vibe": "Solo avisos Binance v6 (+ Alpaca core digests)",
        "scalp15": "Solo avisos Alpaca scalp15 (15m)",
        "scalper": "Solo avisos Alpaca scalp15 (15m)",
        "fb": "Solo avisos de clientes FB / citas",
        "all": "Todos (Binance + Alpaca scalp15 + FB)",
        "both": "Todos (Binance + Alpaca scalp15 + FB)",
    }.get(mode, mode)
