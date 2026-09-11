#!/usr/bin/env python3
"""Shared Telegram notify prefs for Synaptika (Vibe + FB + Hermes Agent + Messenger Sales)."""

from __future__ import annotations

import hashlib
import json
import os
import time
import re
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("VIBE_TRADING_HOME", "/root/.vibe-trading"))
ENV_PATH = HOME / ".env"
PREFS_PATH = HOME / "telegram_notify_prefs.json"
DEDUPE_PATH = HOME / "telegram_send_dedupe.json"

# notify filter mode: vibe | fb | all | hermes (avisos)
DEFAULT_MODE = "all"
VALID_MODES = frozenset({"vibe", "scalp15", "scalper", "fb", "all", "both", "hermes"})
DEDUPE_WINDOW_SEC = 90

# Chat modes (mutually exclusive interactive sessions)
CHAT_CONTROL = "control"
CHAT_HERMES = "hermes"
CHAT_SALES = "sales"

BTN_HERMES_AGENT = "Hermes Agent"
BTN_MESSENGER_SALES = "Messenger Sales"
BTN_EXIT_CHAT = "Salir al menú"

BTN_VIBE = "Solo Binance / Vibe"  # legacy; trading offline — not shown on keyboard
BTN_FB = "Solo clientes FB"
BTN_ALL = "Todos los avisos"
BTN_NOTIFY_HERMES = "Solo avisos Hermes"

# Legacy button labels (still recognized)
BTN_HERMES = BTN_HERMES_AGENT
BTN_HERMES_LEGACY = "Modo Hermes (research)"
BTN_HERMES_INTEL_LEGACY = "OpenBB → Vibe (intel)"
BTN_SALES_TEST = BTN_MESSENGER_SALES
BTN_SALES_EXIT = BTN_EXIT_CHAT
BTN_SALES_TEST_LEGACY = "Probar Messenger sales"
BTN_SALES_EXIT_LEGACY = "Salir Messenger sales"
BTN_SCALP15 = "Solo Alpaca scalp15"
BTN_SCALPER = BTN_SCALP15
BTN_BOTH_LEGACY = "Ambas (Vibe + FB)"
BTN_SCALPER_LEGACY = "Solo Scalper"
BTN_ALL_LEGACY_A = "Todos (Vibe + FB + OpenBB)"
BTN_ALL_LEGACY_B = "Todos (Binance+Alpaca15+FB)"
BTN_ALL_LEGACY_C = "Todos (Vibe + FB + Hermes)"

BUTTON_TO_MODE = {
    BTN_VIBE: "vibe",
    BTN_NOTIFY_HERMES: "hermes",
    BTN_HERMES_INTEL_LEGACY: "hermes",
    BTN_HERMES_LEGACY: "hermes",
    BTN_SCALP15: "vibe",
    BTN_SCALPER_LEGACY: "vibe",
    "Solo Vibe trading": "vibe",
    "Solo Binance v6": "vibe",
    "Solo Scalper": "vibe",
    "Todos (Vibe+Scalper+FB)": "all",
    BTN_ALL_LEGACY_A: "all",
    BTN_ALL_LEGACY_B: "all",
    BTN_ALL_LEGACY_C: "all",
    BTN_FB: "fb",
    BTN_ALL: "all",
    "Todos (Vibe + FB)": "all",
    BTN_BOTH_LEGACY: "all",
    "vibe": "vibe",
    "hermes": "hermes",
    "research": "hermes",
    "openbb": "hermes",
    "scalper": "vibe",
    "scalp15": "vibe",
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
    if mode in ("scalper", "scalp15"):
        return "vibe"
    if mode in ("vibe", "fb", "all", "hermes"):
        return mode
    return DEFAULT_MODE


def _normalize_chat_mode(mode: str) -> str:
    m = (mode or CHAT_CONTROL).lower().strip()
    if m in ("sales", "sales_test", "messenger", "fb_sales"):
        return CHAT_SALES
    if m in ("hermes", "hermes_agent", "agent"):
        return CHAT_HERMES
    return CHAT_CONTROL


def load_prefs() -> dict:
    if PREFS_PATH.exists():
        try:
            doc = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            mode = _normalize_mode(str(doc.get("mode") or DEFAULT_MODE))
            # Migrate legacy sales_test / hermes_agent booleans → chat_mode
            chat_mode = doc.get("chat_mode")
            if not chat_mode:
                if doc.get("sales_test"):
                    chat_mode = CHAT_SALES
                elif doc.get("hermes_agent"):
                    chat_mode = CHAT_HERMES
                else:
                    chat_mode = CHAT_CONTROL
            chat_mode = _normalize_chat_mode(str(chat_mode))
            return {
                "mode": mode,
                "chat_mode": chat_mode,
                # legacy mirrors for older readers
                "sales_test": chat_mode == CHAT_SALES,
                "hermes_agent": chat_mode == CHAT_HERMES,
                "updated_ts": doc.get("updated_ts"),
            }
        except json.JSONDecodeError:
            pass
    return {
        "mode": DEFAULT_MODE,
        "chat_mode": CHAT_CONTROL,
        "sales_test": False,
        "hermes_agent": False,
    }


def _write_prefs(doc: dict) -> dict:
    chat_mode = _normalize_chat_mode(str(doc.get("chat_mode") or CHAT_CONTROL))
    out = {
        "mode": _normalize_mode(str(doc.get("mode") or DEFAULT_MODE)),
        "chat_mode": chat_mode,
        "sales_test": chat_mode == CHAT_SALES,
        "hermes_agent": chat_mode == CHAT_HERMES,
        "updated_ts": int(time.time()),
    }
    PREFS_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def save_prefs(mode: str) -> dict:
    doc = load_prefs()
    doc["mode"] = _normalize_mode(mode)
    return _write_prefs(doc)


def get_chat_mode() -> str:
    return _normalize_chat_mode(str(load_prefs().get("chat_mode") or CHAT_CONTROL))


def set_chat_mode(mode: str) -> dict:
    doc = load_prefs()
    doc["chat_mode"] = _normalize_chat_mode(mode)
    return _write_prefs(doc)


def is_sales_test() -> bool:
    return get_chat_mode() == CHAT_SALES


def is_hermes_agent() -> bool:
    return get_chat_mode() == CHAT_HERMES


def set_sales_test(enabled: bool) -> dict:
    return set_chat_mode(CHAT_SALES if enabled else CHAT_CONTROL)


def set_hermes_agent(enabled: bool) -> dict:
    return set_chat_mode(CHAT_HERMES if enabled else CHAT_CONTROL)


def should_notify(channel: str) -> bool:
    """channel: 'vibe' | 'fb' | 'hermes' (+ legacy scalp15/alpaca → vibe)."""
    ch = (channel or "").lower().strip()
    if ch in ("scalper", "scalp15", "alpaca"):
        ch = "vibe"
    # Interactive sessions hush trading digests
    cm = get_chat_mode()
    if cm in (CHAT_SALES, CHAT_HERMES) and ch in ("vibe", "hermes", "fb"):
        # Still allow FB appointment pushes? hush all while in interactive mode
        print("TG_CHAT_MODE_SKIP channel=", channel, "chat_mode=", cm)
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



def fold_btn(text: str) -> str:
    """Normalize button text for reliable Telegram matching (accents/case)."""
    s = unicodedata.normalize("NFKD", (text or "").strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s


def is_exit_chat_button(text: str) -> bool:
    f = fold_btn(text)
    aliases = {
        fold_btn(BTN_EXIT_CHAT),
        fold_btn(BTN_SALES_EXIT_LEGACY),
        "salir al menu",
        "salir menu",
        "/menu",
        "/trading",
        "menu",
        "salir sales",
        "salir messenger sales",
    }
    if f in aliases:
        return True
    if f.startswith("salir al menu") or f.startswith("salir menu"):
        return True
    return False


def is_hermes_button(text: str) -> bool:
    f = fold_btn(text)
    return f in {
        fold_btn(BTN_HERMES_AGENT),
        fold_btn(BTN_HERMES_LEGACY),
        fold_btn(BTN_HERMES_INTEL_LEGACY),
        "hermes",
        "hermes agent",
        "/hermes",
        "modo hermes",
    }


def is_sales_button(text: str) -> bool:
    f = fold_btn(text)
    return f in {
        fold_btn(BTN_MESSENGER_SALES),
        fold_btn(BTN_SALES_TEST_LEGACY),
        "sales",
        "messenger sales",
        "/sales",
        "probar sales",
        "probar messenger sales",
    }


def filter_keyboard() -> dict:
    cm = get_chat_mode()
    if cm == CHAT_SALES:
        return {
            "keyboard": [
                [{"text": BTN_EXIT_CHAT}],
                [{"text": "Agendar cita"}, {"text": "Ver ejemplos"}],
                [{"text": "Hablar con humano"}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
            "input_field_placeholder": "Messenger Sales · Salir al menú para volver",
        }
    if cm == CHAT_HERMES:
        return {
            "keyboard": [
                [{"text": BTN_EXIT_CHAT}],
            ],
            "resize_keyboard": True,
            "is_persistent": True,
            "input_field_placeholder": "Hermes Agent · Salir al menú para volver",
        }
    return {
        "keyboard": [
            [{"text": BTN_HERMES_AGENT}, {"text": BTN_MESSENGER_SALES}],
            [{"text": BTN_FB}, {"text": BTN_ALL}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "input_field_placeholder": "Menú Synaptika",
    }


def mode_label(mode: str) -> str:
    mode = _normalize_mode(mode)
    return {
        "vibe": "Solo avisos Binance / Vibe",
        "hermes": "Solo avisos Hermes / OpenBB",
        "scalp15": "Solo avisos Binance / Vibe",
        "scalper": "Solo avisos Binance / Vibe",
        "fb": "Solo avisos de clientes FB / citas",
        "all": "Todos (Vibe + FB)",
        "both": "Todos (Vibe + FB)",
    }.get(mode, mode)


def chat_mode_label(mode: str | None = None) -> str:
    cm = _normalize_chat_mode(mode or get_chat_mode())
    return {
        CHAT_CONTROL: "Menú (filtro / trading)",
        CHAT_HERMES: "Hermes Agent (OmniRoute)",
        CHAT_SALES: "Messenger Sales (página FB Synaptika)",
    }.get(cm, cm)
