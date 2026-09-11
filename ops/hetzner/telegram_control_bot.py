#!/usr/bin/env python3
"""Telegram control: filter keyboard + on-demand strategy charts.

Commands (natural language or slash; ignore notify filter):
  binance / v6           → chart Binance smart-fast-v6
  alpaca / paper         → chart Alpaca core (canonical_v2)
  scalp15 / 15m          → chart Alpaca scalp15 momentum
  estado                 → charts Binance + Alpaca + scalp15
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from telegram_notify_prefs import (
    BTN_ALL,
    BTN_EXIT_CHAT,
    BTN_FB,
    BTN_HERMES_AGENT,
    BTN_MESSENGER_SALES,
    BTN_VIBE,
    BUTTON_TO_MODE,
    CHAT_CONTROL,
    CHAT_HERMES,
    CHAT_SALES,
    chat_mode_label,
    filter_keyboard,
    get_chat_mode,
    is_exit_chat_button,
    is_hermes_agent,
    is_hermes_button,
    is_sales_button,
    is_sales_test,
    load_env,
    load_prefs,
    mode_label,
    save_prefs,
    set_chat_mode,
    should_notify,
    tg_api,
)

HOME = Path("/root/.vibe-trading")
ALPACA = Path("/root/.alpaca-paper")
ALPACA_SCALP15 = Path("/root/.alpaca-scalp15")
PREFS_PORT = 8897
DEFAULT_MESSENGER_URL = "https://synaptika-messenger.duckdns.org"
HERMES_API = "http://127.0.0.1:8642/v1/chat/completions"
HERMES_HISTORY = HOME / "hermes_chat_history.json"
# Stable session so Hermes keeps context without us resending huge history.
HERMES_SESSION_ID = "synaptika-tg-owner"
# Prefer OmniRoute "fast" lane for snappier Telegram replies (tools still available).
HERMES_MODEL = "auto/fast"
HERMES_HISTORY_MAX = 12
HERMES_MAX_TOKENS = 2048
HELP_TEXT = (
    "Modos interactivos:\n"
    f"• {BTN_HERMES_AGENT} — Hermes + OmniRoute\n"
    f"• {BTN_MESSENGER_SALES} — bot de ventas = página FB Synaptika\n"
    f"• {BTN_EXIT_CHAT} — vuelve al menú\n\n"
    "Filtro de avisos:\n"
    f"• {BTN_FB}\n"
    f"• {BTN_ALL}\n\n"
    "(Trading apagado en el VPS — sin gráficas ni bots de trade.)"
)


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        # legacy equity_history list → chart format
        pts = []
        for p in raw:
            if isinstance(p, dict) and "equity" in p:
                pts.append({"ts": float(p.get("ts") or 0), "equity": float(p.get("equity") or 0)})
        return {"points": pts, "markers": [], "start_equity": pts[0]["equity"] if pts else None}
    return {}


def _norm(text: str) -> str:
    t = (text or "").lower().strip()
    t = t.split("@", 1)[0]
    t = re.sub(r"[^a-z0-9\s/+_-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def detect_intent(text: str) -> str | None:
    """Return vibe|alpaca|scalp15|estado|help|filtro or None."""
    n = _norm(text)
    if not n:
        return None
    bare = n[1:] if n.startswith("/") else n

    if bare in ("ayuda", "help") or bare.startswith("ayuda ") or bare.startswith("help "):
        return "help"
    if bare in ("filtro", "menu", "start") or bare.startswith("filtro"):
        return "filtro"

    if bare in ("estado", "status", "all", "todo", "todas") or bare.startswith(
        ("estado ", "status ")
    ):
        return "estado"

    # scalp15 before bare "alpaca" / "scalp"
    if any(
        k in bare
        for k in (
            "scalp15",
            "scalp 15",
            "15m",
            "15 m",
            "momentum 15",
            "/scalp15",
        )
    ) or bare in ("s15", "sc15"):
        return "scalp15"

    if any(
        k in bare
        for k in (
            "alpaca",
            "paper trading",
            "alpaca paper",
            "canonical",
        )
    ) and "scalp15" not in bare and "15m" not in bare:
        return "alpaca"

    if any(
        k in bare
        for k in (
            "binance",
            "smart fast",
            "smart-fast",
            "v6",
            "vibe",
            "/binance",
            "/vibe",
        )
    ) or bare in ("binance", "vibe", "trading"):
        return "vibe"

    # retired eth scalper → redirect tip via help-ish
    if any(k in bare for k in ("eth scalp", "/eth", "scalper eth")) or bare in (
        "eth",
        "scalper",
        "scalp",
        "/scalper",
        "/scalp",
    ):
        return "retired_eth"

    return None


def _pct(a: float, b: float) -> float:
    if b <= 0:
        return 0.0
    return (a - b) / b * 100.0


def _regime_txt(regime: str) -> str:
    return {
        "bull": "mercado alcista (subiendo en general)",
        "bear": "mercado bajista (bajando en general)",
        "chop": "mercado lateral (sin tendencia clara)",
        "trend": "tendencia (momentum)",
        "range": "rango (mean-reversion)",
        "dead": "mercado muerto / sin edge",
    }.get(str(regime or ""), "sin dato de mercado aun")


def build_vibe_digest() -> str:
    st = _read_json(HOME / "autotrade_state.json")
    snap = _read_json(HOME / "telegram_portfolio_snap.json")
    g = st.get("goals") or {}
    eq, day_open, week_open, daily_target, weekly_target = _vibe_equity()
    prev = float(snap.get("total") or eq)
    # chg vs last snap if different point recorded in history
    hist = _read_json(HOME / "equity_history.json")
    pts = list(hist.get("points") or [])
    prev_eq = float(pts[-2]["equity"]) if len(pts) >= 2 else prev
    chg = _pct(eq, prev_eq)
    book_pnl = eq - day_open if day_open else 0.0
    book_pct = _pct(eq, day_open) if day_open else 0.0
    pos = st.get("positions") or {}
    pos_lines: list[str] = []
    if isinstance(pos, dict):
        for asset, meta in pos.items():
            usd = float((meta or {}).get("usd") or 0)
            if usd >= 0.4:
                pos_lines.append(f"  - {asset}: unos ${usd:.2f}")
    trades = st.get("trades_done")
    buys = st.get("buys_today")
    daily_cap = g.get("daily_trade_cap") or st.get("daily_cap") or "?"
    baseline = float(st.get("double_baseline") or 0)
    sign = "+" if chg >= 0 else ""
    book_sign = "+" if book_pnl >= 0 else ""
    book_pct_sign = "+" if book_pct >= 0 else ""

    lines = [
        "[Binance] Resumen · smart-fast-v6",
        "Pedido ahora (datos mark reales de la billetera)",
        "",
        "Tu dinero ahora",
        f"- Billetera total: ${eq:.2f}",
        f"- Cambio vs punto anterior: {sign}{chg:.2f}%",
        f"- Cambio del dia (mark vs apertura): {book_sign}${book_pnl:.2f} ({book_pct_sign}{book_pct:.2f}%)",
    ]
    # Strategy mode (Ops / Hermes visibility)
    try:
        mode_doc = _read_json(HOME / "strategy_mode.json")
        m = str(mode_doc.get("mode") or "?")
        locked = bool(mode_doc.get("locked"))
        reason = str(mode_doc.get("reason") or "")[:120]
        lock_s = "LOCKED" if locked else "auto"
        lines += [
            "",
            "Estrategia",
            f"- Modo: {m} · {lock_s} · strategy=smart-fast-v6",
            f"- Motivo: {reason or '—'}",
        ]
    except Exception:
        pass
    if daily_target > 0 or weekly_target > 0:
        lines += ["", "Metas"]
        if daily_target > 0:
            day_left = daily_target - book_pnl
            if day_left > 0:
                lines.append(
                    f"- Meta dia ${daily_target:.0f} · vas {book_sign}${book_pnl:.2f} (faltan ${day_left:.2f})"
                )
            else:
                lines.append(
                    f"- Meta dia ${daily_target:.0f} · YA: {book_sign}${book_pnl:.2f}"
                )
        if weekly_target > 0 and week_open > 0:
            week_pnl = eq - week_open
            wsign = "+" if week_pnl >= 0 else ""
            lines.append(f"- Meta semana ${weekly_target:.0f} · vas {wsign}${week_pnl:.2f}")
    elif baseline > 0:
        target = baseline * 2
        prog = max(0.0, min(100.0, (eq / target) * 100.0))
        lines += [
            "",
            "Objetivo simple",
            f"- Quieres duplicar: de ${baseline:.2f} a ~${target:.2f}",
            f"- Avance: vas al {prog:.0f}% del camino",
        ]
    lines += ["", "Que tienes comprado"]
    if pos_lines:
        lines.extend(pos_lines)
    else:
        lines.append("- Nada abierto ahora (o solo dust)")
    lines += [
        "",
        "Actividad de hoy",
        f"- Trades/compras: {trades}/{buys} (cap {daily_cap})",
        f"- Regimen: {_regime_txt(st.get('regime'))}",
        f"- Ultimo simbolo: {st.get('last_symbol') or '—'}",
        f"- Filtro avisos: {mode_label(load_prefs().get('mode', 'all'))}",
    ]
    text = "\n".join(lines)
    return text[:3397] + "..." if len(text) > 3400 else text


def build_scalp15_digest() -> str:
    st = _read_json(ALPACA_SCALP15 / "state.json")
    eq, day_open, week_open, daily_target, weekly_target = _scalp15_equity()
    hist = _read_json(ALPACA_SCALP15 / "equity_history.json")
    pts = list(hist.get("points") or [])
    prev_eq = float(pts[-2]["equity"]) if len(pts) >= 2 else eq
    chg = _pct(eq, prev_eq) if prev_eq else 0.0
    book_pnl = eq - day_open if day_open else 0.0
    book_pct = _pct(eq, day_open) if day_open else 0.0
    pos = st.get("positions") or {}
    pos_lines: list[str] = []
    if isinstance(pos, dict):
        for asset, meta in pos.items():
            usd = float((meta or {}).get("usd") or 0)
            if usd >= 0.4:
                pos_lines.append(f"  - {asset}: unos ${usd:.2f}")
    sign = "+" if chg >= 0 else ""
    book_sign = "+" if book_pnl >= 0 else ""
    book_pct_sign = "+" if book_pct >= 0 else ""
    lines = [
        "[Alpaca · scalp15] Resumen · momentum 15m",
        "Pedido ahora (cuenta paper aparte)",
        "",
        "Tu dinero ahora",
        f"- Equity: ${eq:.2f}",
        f"- Cambio vs punto anterior: {sign}{chg:.2f}%",
        f"- Cambio del dia: {book_sign}${book_pnl:.2f} ({book_pct_sign}{book_pct:.2f}%)",
        "",
        "Que tienes comprado",
    ]
    lines.extend(pos_lines or ["- Nada abierto (solo cash)"])
    lines += [
        "",
        "Actividad de hoy",
        f"- Buys: {st.get('buys_today') or 0} · trades: {st.get('trades_today') or 0}",
        f"- Roundtrips: {st.get('roundtrips_today') or 0}",
        f"- Mercado: {_regime_txt(st.get('regime'))}",
        f"- Ultimo simbolo: {st.get('last_symbol') or '—'}",
        f"- Halt: {'SI' if (ALPACA_SCALP15 / 'HALT').exists() else 'no'}",
        f"- Filtro avisos: {mode_label(load_prefs().get('mode', 'all'))}",
    ]
    if daily_target > 0:
        lines.append(f"- Meta dia: ${daily_target:.2f}")
    text = "\n".join(lines)
    return text[:3397] + "..." if len(text) > 3400 else text


def build_alpaca_digest() -> str:
    st = _read_json(ALPACA / "state.json")
    snap = _read_json(ALPACA / "telegram_portfolio_snap.json")
    eq, day_open, week_open, daily_target, weekly_target = _alpaca_equity()
    cash = float(snap.get("cash") or 0)
    hist = _read_json(ALPACA / "equity_history.json")
    pts = list(hist.get("points") or [])
    prev_eq = float(pts[-2]["equity"]) if len(pts) >= 2 else float(snap.get("total") or eq)
    chg = _pct(eq, prev_eq)
    book_pnl = eq - day_open if day_open else 0.0
    book_pct = _pct(eq, day_open) if day_open else 0.0
    baseline = float(st.get("double_baseline") or 0)
    regime = str(st.get("regime") or "")
    pos_lines: list[str] = []
    trades_used = 0
    trades_cap = 0
    sleeves = st.get("sleeves") or {}
    pairs = [("core", "core", 8), ("burst", "burst", 12)]
    if "core" not in sleeves and "base" in sleeves:
        pairs = [("base", "base", 8), ("fast", "burst", 12)]
    for key, label, default_cap in pairs:
        book = sleeves.get(key) or {}
        used = int(book.get("trades_today") or 0)
        trades_used += used
        trades_cap += default_cap
        for asset, meta in (book.get("positions") or {}).items():
            usd = float((meta or {}).get("usd") or 0)
            if usd >= 1:
                pos_lines.append(f"  - [{label}] {asset}: unos ${usd:.2f}")
    if not pos_lines:
        pos_lines = ["- Nada abierto ahora (solo cash paper)"]
    left = ""
    try:
        left = f" (te quedan {int(trades_cap) - int(trades_used)} de {trades_cap})"
    except Exception:
        left = ""
    sign = "+" if chg >= 0 else ""
    book_sign = "+" if book_pnl >= 0 else ""
    book_pct_sign = "+" if book_pct >= 0 else ""

    lines = [
        "[Alpaca] Resumen paper · core+burst",
        "Pedido ahora (mark real de la cuenta paper)",
        "",
        "Tu dinero ahora",
        f"- Billetera total: ${eq:.2f}",
        f"- Cash libre: ${cash:.2f}" if cash else f"- Cash libre: n/d",
        f"- Cambio vs punto anterior: {sign}{chg:.2f}%",
        f"- Cambio del dia (mark vs apertura): {book_sign}${book_pnl:.2f} ({book_pct_sign}{book_pct:.2f}%)",
    ]
    if daily_target > 0 or weekly_target > 0:
        lines += ["", "Metas"]
        if daily_target > 0:
            day_left = daily_target - book_pnl
            if day_left > 0:
                lines.append(
                    f"- Meta dia ${daily_target:.0f} · vas {book_sign}${book_pnl:.2f} (faltan ${day_left:.2f})"
                )
            else:
                lines.append(
                    f"- Meta dia ${daily_target:.0f} · YA: {book_sign}${book_pnl:.2f}"
                )
        if weekly_target > 0 and week_open > 0:
            week_pnl = eq - week_open
            wsign = "+" if week_pnl >= 0 else ""
            lines.append(f"- Meta semana ${weekly_target:.0f} · vas {wsign}${week_pnl:.2f}")
    elif baseline > 0:
        target = baseline * 2
        prog = max(0.0, min(100.0, (eq / target) * 100.0))
        lines += [
            "",
            "Objetivo simple",
            f"- Quieres duplicar: de ${baseline:.2f} a ~${target:.2f}",
            f"- Avance: vas al {prog:.0f}% del camino",
        ]
    lines += ["", "Que tienes comprado"]
    lines.extend(pos_lines)
    lines += [
        "",
        "Actividad de hoy (ambas mangas)",
        f"- Trades usados: {trades_used}/{trades_cap or 20}{left}",
        f"- Mercado (lectura del bot): {_regime_txt(regime)}",
        "",
        "Mangas: [Alpaca · core] + [Alpaca · burst] · Capital Deploy v1",
        f"- Filtro avisos: {mode_label(load_prefs().get('mode', 'all'))}",
    ]
    text = "\n".join(lines)
    return text[:3397] + "..." if len(text) > 3400 else text


# Back-compat aliases used by older call sites
def build_vibe_caption() -> str:
    return build_vibe_digest()


def build_scalp15_caption() -> str:
    return build_scalp15_digest()


def build_alpaca_caption() -> str:
    return build_alpaca_digest()


def _vibe_equity() -> tuple[float, float, float, float, float]:
    st = _read_json(HOME / "autotrade_state.json")
    snap = _read_json(HOME / "telegram_portfolio_snap.json")
    g = st.get("goals") or {}
    eq = float(snap.get("total") or st.get("last_equity") or g.get("day_open_equity") or 0)
    day_open = float(g.get("day_open_equity") or eq)
    week_open = float(g.get("week_open_equity") or eq)
    daily_target = float(g.get("daily_target_usd") or 0)
    weekly_target = float(g.get("weekly_target_usd") or 0)
    return eq, day_open, week_open, daily_target, weekly_target


def _scalp15_equity() -> tuple[float, float, float, float, float]:
    st = _read_json(ALPACA_SCALP15 / "state.json")
    hist = _read_json(ALPACA_SCALP15 / "equity_history.json")
    g = st.get("goals") or {}
    pts = list(hist.get("points") or [])
    eq = float(st.get("equity") or 0)
    if eq <= 0 and pts:
        eq = float(pts[-1].get("equity") or 0)
    day_open = float(g.get("day_open_equity") or eq)
    week_open = float(g.get("week_open_equity") or eq)
    daily_target = float(g.get("daily_target_usd") or 0)
    weekly_target = float(g.get("weekly_target_usd") or 0)
    return eq, day_open, week_open, daily_target, weekly_target


def _alpaca_equity() -> tuple[float, float, float, float, float]:
    st = _read_json(ALPACA / "state.json")
    snap = _read_json(ALPACA / "telegram_portfolio_snap.json")
    g = st.get("goals") or {}
    sg = st.get("goal_snap") or {}
    eq = float(snap.get("total") or st.get("equity") or g.get("day_open_equity") or 0)
    day_open = float(g.get("day_open_equity") or sg.get("day_open") or eq)
    week_open = float(g.get("week_open_equity") or sg.get("week_open") or eq)
    daily_target = float(g.get("daily_target_usd") or sg.get("daily_target") or 0)
    weekly_target = float(g.get("weekly_target_usd") or sg.get("weekly_target") or 0)
    return eq, day_open, week_open, daily_target, weekly_target


def send_owner(chat_id: str, text: str, *, with_keyboard: bool = True) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:3500],
        "disable_web_page_preview": True,
    }
    if with_keyboard:
        payload["reply_markup"] = filter_keyboard()
    tg_api("sendMessage", payload)


def force_main_menu(chat_id: str, text: str) -> None:
    """Remove sticky keyboard then show main menu (fixes Telegram sticky reply UI)."""
    set_chat_mode(CHAT_CONTROL)
    tg_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "…",
            "reply_markup": {"remove_keyboard": True},
            "disable_web_page_preview": True,
        },
    )
    time.sleep(0.2)
    send_owner(chat_id, text, with_keyboard=True)


def messenger_base_url() -> str:
    env = load_env()
    return (
        env.get("SYNAPTICA_MESSENGER_URL")
        or env.get("MESSENGER_URL")
        or env.get("BOOKING_BASE_URL")
        or DEFAULT_MESSENGER_URL
    ).rstrip("/")


def proxy_to_sales_bot(
    chat_id: str,
    *,
    text: str | None = None,
    callback_data: str | None = None,
    first_name: str | None = None,
    ensure_menu: bool = False,
) -> bool:
    """Forward owner chat to Synaptica Messenger sales (bypasses owner skip)."""
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        send_owner(chat_id, "Falta TELEGRAM_BOT_TOKEN para proxy sales.")
        return False
    body = {
        "chat_id": str(chat_id),
        "text": text or "",
        "callback_data": callback_data or "",
        "first_name": first_name or "",
        "ensure_menu": bool(ensure_menu),
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = f"{messenger_base_url()}/telegram/owner-proxy"
    req = __import__("urllib.request").request.Request(
        url,
        data=raw,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with __import__("urllib.request").request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
            ok = bool(data.get("ok"))
            print("SALES_PROXY", ok, data, flush=True)
            return ok
    except Exception as exc:  # noqa: BLE001
        print("SALES_PROXY_FAIL", exc, flush=True)
        send_owner(
            chat_id,
            f"No pude hablar con Messenger sales 😅\n{exc}\n"
            f"URL: {url}\nToca «{BTN_EXIT_CHAT}» si quieres salir.",
        )
        return False


def _load_hermes_history() -> list[dict]:
    if not HERMES_HISTORY.exists():
        return []
    try:
        doc = json.loads(HERMES_HISTORY.read_text(encoding="utf-8"))
        msgs = doc.get("messages") if isinstance(doc, dict) else doc
        if isinstance(msgs, list):
            return [m for m in msgs if isinstance(m, dict)][-HERMES_HISTORY_MAX:]
    except Exception:
        pass
    return []


def _save_hermes_history(messages: list[dict]) -> None:
    HERMES_HISTORY.write_text(
        json.dumps(
            {"messages": messages[-HERMES_HISTORY_MAX:], "updated_ts": int(time.time())},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _clear_hermes_history() -> None:
    try:
        if HERMES_HISTORY.exists():
            HERMES_HISTORY.unlink()
    except Exception:
        pass


def _hermes_api_key() -> str:
    env = load_env()
    # Prefer key from hermes .env
    hermes_env = Path("/root/.hermes/.env")
    if hermes_env.exists():
        for line in hermes_env.read_text(encoding="utf-8").splitlines():
            if line.startswith("API_SERVER_KEY="):
                return line.split("=", 1)[1].strip()
    return env.get("HERMES_API_SERVER_KEY") or env.get("API_SERVER_KEY") or ""


def _typing_pulse(chat_id: str, stop: threading.Event) -> None:
    """Keep Telegram 'typing…' alive while Hermes thinks (action expires ~5s)."""
    while not stop.wait(4.0):
        try:
            tg_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        except Exception:
            break


def _send_owner_get_mid(chat_id: str, text: str, *, with_keyboard: bool = True) -> int | None:
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:3500],
        "disable_web_page_preview": True,
    }
    if with_keyboard:
        payload["reply_markup"] = filter_keyboard()
    res = tg_api("sendMessage", payload)
    try:
        return int(((res.get("result") or {}).get("message_id") or 0)) or None
    except Exception:
        return None


def _delete_msg(chat_id: str, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        tg_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception:
        pass


def _warm_hermes() -> None:
    """Prime Hermes/OmniRoute so the first real user message is faster."""
    key = _hermes_api_key()
    if not key:
        return
    payload = json.dumps(
        {
            "model": HERMES_MODEL,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = __import__("urllib.request").request.Request(
        HERMES_API,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {key}",
            "X-Hermes-Session-Id": HERMES_SESSION_ID + "-warm",
        },
        method="POST",
    )
    try:
        t0 = time.time()
        with __import__("urllib.request").request.urlopen(req, timeout=60) as r:
            r.read()
        print(f"HERMES_WARM ok {time.time() - t0:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("HERMES_WARM_FAIL", exc, flush=True)


def proxy_to_hermes_agent(chat_id: str, text: str, *, thinking_mid: int | None = None) -> bool:
    """Forward owner chat to Hermes API server (OmniRoute-backed agent)."""
    key = _hermes_api_key()
    if not key:
        _delete_msg(chat_id, thinking_mid)
        send_owner(chat_id, "Falta API_SERVER_KEY de Hermes en /root/.hermes/.env")
        return False
    history = _load_hermes_history()
    history.append({"role": "user", "content": text})
    # With session header Hermes keeps server-side context; send a short tail only.
    payload = {
        "model": HERMES_MODEL,
        "messages": history[-HERMES_HISTORY_MAX:],
        "max_tokens": HERMES_MAX_TOKENS,
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = __import__("urllib.request").request.Request(
        HERMES_API,
        data=raw,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {key}",
            "X-Hermes-Session-Id": HERMES_SESSION_ID,
        },
        method="POST",
    )
    stop = threading.Event()
    pulse = threading.Thread(target=_typing_pulse, args=(chat_id, stop), daemon=True)
    try:
        tg_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        pulse.start()
        t0 = time.time()
        with __import__("urllib.request").request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode("utf-8") or "{}")
        latency = time.time() - t0
        msg = ((data.get("choices") or [{}])[0].get("message") or {})
        content = (msg.get("content") or "").strip()
        if not content:
            content = (msg.get("reasoning_content") or "").strip() or "(sin respuesta)"
        history.append({"role": "assistant", "content": content})
        _save_hermes_history(history)
        still_hermes = get_chat_mode() == CHAT_HERMES
        _delete_msg(chat_id, thinking_mid)
        chunk = content
        first = True
        while chunk:
            send_owner(
                chat_id,
                chunk[:3500],
                with_keyboard=(first and still_hermes),
            )
            first = False
            chunk = chunk[3500:]
        print(
            "HERMES_PROXY ok chars=",
            len(content),
            f"latency={latency:.1f}s",
            "mode_ok=",
            still_hermes,
            "model=",
            data.get("model") or HERMES_MODEL,
            flush=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print("HERMES_PROXY_FAIL", exc, flush=True)
        _delete_msg(chat_id, thinking_mid)
        if get_chat_mode() == CHAT_HERMES:
            send_owner(
                chat_id,
                f"Hermes no respondió 😅\n{exc}\nToca «{BTN_EXIT_CHAT}» si quieres salir.",
            )
        return False
    finally:
        stop.set()


def enter_sales_test(chat_id: str) -> None:
    set_chat_mode(CHAT_SALES)
    send_owner(
        chat_id,
        (
            "Messenger Sales ON ✅\n"
            "Conectado al bot de ventas Synaptika (misma lógica que la página de Facebook).\n"
            "Menú: Agendar cita · Ver ejemplos · Hablar con humano\n\n"
            f"Para volver: «{BTN_EXIT_CHAT}»"
        ),
    )
    proxy_to_sales_bot(chat_id, text="hola", ensure_menu=True)
    send_owner(
        chat_id,
        f"Recuerda: «{BTN_EXIT_CHAT}» vuelve al menú principal.",
        with_keyboard=True,
    )


def enter_hermes_agent(chat_id: str) -> None:
    set_chat_mode(CHAT_HERMES)
    _clear_hermes_history()
    send_owner(
        chat_id,
        (
            "Hermes Agent ON ✅\n"
            "OmniRoute (vía rápida) + tools.\n"
            "Escribe tu pregunta — verás «pensando…» al instante.\n\n"
            f"Para volver: «{BTN_EXIT_CHAT}»"
        ),
    )
    # Warm OmniRoute/Hermes in background so first answer is snappier
    threading.Thread(target=_warm_hermes, daemon=True).start()


def exit_chat_mode(chat_id: str) -> None:
    prev = get_chat_mode()
    print("EXIT_CHAT_MODE from=", prev, flush=True)
    force_main_menu(
        chat_id,
        f"Saliste de {chat_mode_label(prev)}.\n"
        f"Menú activo · filtro avisos: {mode_label(load_prefs().get('mode', 'all'))}",
    )


def exit_sales_test(chat_id: str) -> None:
    exit_chat_mode(chat_id)


def send_strategy_chart(
    chat_id: str,
    *,
    kind: str,
    caption: str,
    chart_only: bool = False,
) -> bool:
    """Send equity chart. Default = detailed digest text above + chart (same as alerts)."""
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        send_owner(chat_id, caption)
        return False
    try:
        import equity_chart  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        print("EQUITY_CHART_IMPORT", exc, flush=True)
        send_owner(chat_id, caption)
        return False

    if kind == "vibe":
        history = HOME / "equity_history.json"
        chart_path = HOME / "equity_cmd_binance.png"
        venue = "Binance"
        channel = "vibe"
        eq, day_open, week_open, daily_target, weekly_target = _vibe_equity()
        text = caption if caption.strip() else build_vibe_digest()
    elif kind == "scalp15":
        history = ALPACA_SCALP15 / "equity_history.json"
        chart_path = ALPACA_SCALP15 / "equity_cmd_scalp15.png"
        venue = "Alpaca · scalp15"
        channel = "scalp15"
        eq, day_open, week_open, daily_target, weekly_target = _scalp15_equity()
        text = caption if caption.strip() else build_scalp15_digest()
    elif kind == "alpaca":
        history = ALPACA / "equity_history.json"
        chart_path = ALPACA / "equity_cmd_alpaca.png"
        venue = "Alpaca"
        channel = "vibe"
        eq, day_open, week_open, daily_target, weekly_target = _alpaca_equity()
        text = caption if caption.strip() else build_alpaca_digest()
    else:
        send_owner(chat_id, caption)
        return False

    if eq <= 0:
        send_owner(chat_id, f"{text}\n(sin equity aun)")
        return False

    try:
        ok, _, _ = equity_chart.build_and_send(
            history_path=history,
            chart_path=chart_path,
            token=token,
            chat=chat_id,
            venue_tag=venue,
            equity=eq,
            text=text,
            day_open=day_open or eq,
            daily_target=daily_target,
            week_open=week_open or eq,
            weekly_target=weekly_target,
            force=True,
            channel=channel,
        )
        if ok:
            return True
        if chart_only:
            png = equity_chart.render_chart(
                history,
                chart_path,
                venue_tag=venue,
                equity_now=eq,
                day_open=day_open or eq,
                daily_target=daily_target,
                week_open=week_open or eq,
                weekly_target=weekly_target,
            )
            if png and equity_chart.send_photo(token, chat_id, png, caption=text[:200]):
                return True
        send_owner(chat_id, text)
        return False
    except Exception as exc:  # noqa: BLE001
        print("STATUS_PHOTO_FAIL", kind, exc, flush=True)
        send_owner(chat_id, text)
        return False


def send_estado_charts(chat_id: str) -> None:
    """Binance + Alpaca core + Alpaca scalp15."""
    send_strategy_chart(chat_id, kind="vibe", caption=build_vibe_digest())
    time.sleep(0.5)
    send_strategy_chart(chat_id, kind="alpaca", caption=build_alpaca_digest())
    time.sleep(0.5)
    send_strategy_chart(chat_id, kind="scalp15", caption=build_scalp15_digest())


def clear_old_ui(chat_id: str, *, force_send: bool = False) -> None:
    marker = HOME / "telegram_ui_cleared.flag"
    tg_api("deleteMyCommands", {})
    tg_api(
        "setMyCommands",
        {
            "commands": [
                {"command": "hermes", "description": "Entrar a Hermes Agent"},
                {"command": "sales", "description": "Messenger Sales (página FB)"},
                {"command": "menu", "description": "Salir al menú principal"},
                {"command": "filtro", "description": "Ver/cambiar filtro de avisos"},
                {"command": "ayuda", "description": "Lista de comandos"},
            ]
        },
    )
    if marker.exists() and not force_send:
        print("UI_ALREADY_CLEARED skip_send", flush=True)
        return
    send_owner(
        chat_id,
        (
            "Filtro de avisos listo.\n"
            f"Actual: {mode_label(load_prefs()['mode'])}\n\n"
            f"{HELP_TEXT}"
        ),
    )
    marker.write_text("1\n", encoding="utf-8")


def handle_text(chat_id: str, text: str, *, first_name: str | None = None) -> None:
    raw = (text or "").strip()
    low = raw.lower().strip()

    # Exit MUST win over Hermes/Sales forwarding (unicode-safe)
    if is_exit_chat_button(raw):
        if get_chat_mode() != CHAT_CONTROL:
            exit_chat_mode(chat_id)
        else:
            force_main_menu(
                chat_id,
                f"Menú principal.\nFiltro avisos: {mode_label(load_prefs().get('mode', 'all'))}",
            )
        return

    if is_hermes_button(raw):
        enter_hermes_agent(chat_id)
        return
    if is_sales_button(raw):
        enter_sales_test(chat_id)
        return

    if is_hermes_agent():
        # Instant UX: show thinking bubble before Hermes/OmniRoute work
        mid = _send_owner_get_mid(chat_id, "⏳ Pensando…", with_keyboard=True)

        def _run(msg: str = raw, thinking_mid: int | None = mid) -> None:
            proxy_to_hermes_agent(chat_id, msg, thinking_mid=thinking_mid)

        threading.Thread(target=_run, daemon=True).start()
        return

    if is_sales_test():
        def _sales(msg: str = raw, fn: str | None = first_name) -> None:
            proxy_to_sales_bot(chat_id, text=msg, first_name=fn)

        threading.Thread(target=_sales, daemon=True).start()
        return

    if raw in BUTTON_TO_MODE or low in BUTTON_TO_MODE:
        mode = BUTTON_TO_MODE.get(raw) or BUTTON_TO_MODE[low]
        # Trading filters are retired; map vibe/scalp → ignore with notice
        if mode in ("vibe", "scalp15", "scalper", "hermes"):
            send_owner(
                chat_id,
                "Trading/OpenBB apagado en el VPS.\n"
                f"Usa «{BTN_FB}» o «{BTN_ALL}» para avisos Synaptika.\n"
                f"O entra a {BTN_HERMES_AGENT} / {BTN_MESSENGER_SALES}.",
            )
            return
        save_prefs(mode)
        send_owner(
            chat_id,
            f"Filtro guardado: {mode_label(mode)}\n"
            "A partir de ahora solo te mando esa parte.",
        )
        return

    n = _norm(raw)
    bare = n[1:] if n.startswith("/") else n
    if bare in ("todos", "all", "ambas"):
        save_prefs("all")
        send_owner(chat_id, f"Filtro guardado: {mode_label('all')}")
        return
    if bare in ("solo_vibe", "filtro_vibe", "solo_scalp15", "filtro_scalp15", "solo_scalper", "filtro_scalper"):
        send_owner(
            chat_id,
            "Trading apagado — ese filtro ya no aplica.\n"
            f"Opciones: «{BTN_FB}» o «{BTN_ALL}».",
        )
        return
    if bare in ("solo_fb", "filtro_fb", "fb"):
        if bare == "fb":
            send_owner(
                chat_id,
                "Canal FB = citas/clientes (Synaptika).\n"
                f"Filtro actual: {mode_label(load_prefs().get('mode', 'all'))}\n"
                f"FB activo ahora: {'si' if should_notify('fb') else 'no'}",
            )
            return
        save_prefs("fb")
        send_owner(chat_id, f"Filtro guardado: {mode_label('fb')}")
        return

    intent = detect_intent(raw)
    if intent == "help":
        send_owner(chat_id, HELP_TEXT)
        return
    if intent == "filtro":
        force_main_menu(
            chat_id,
            f"Filtro de avisos.\nActual: {mode_label(load_prefs().get('mode', 'all'))}\n\n{HELP_TEXT}",
        )
        return
    if intent in ("estado", "vibe", "scalp15", "alpaca", "retired_eth"):
        send_owner(
            chat_id,
            "Trading apagado en el VPS (sin bots ni gráficas).\n"
            f"Menú: {BTN_HERMES_AGENT} · {BTN_MESSENGER_SALES} · {BTN_FB} · {BTN_ALL}",
        )
        return

    return


def handle_callback(chat_id: str, data: str, *, first_name: str | None = None) -> None:
    if not is_sales_test():
        return
    if not data:
        return
    proxy_to_sales_bot(chat_id, callback_data=data, first_name=first_name)


class PrefsHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] in ("/prefs", "/telegram-prefs", "/"):
            prefs = load_prefs()
            mode = prefs.get("mode") or "all"
            api_mode = "both" if mode == "all" else mode
            body = json.dumps(
                {
                    "mode": api_mode,
                    "filter": mode,
                    "sales_test": bool(prefs.get("sales_test")),
                    "hermes_agent": bool(prefs.get("hermes_agent")),
                    "chat_mode": prefs.get("chat_mode") or "control",
                    "fb": should_notify("fb"),
                    "vibe": should_notify("vibe"),
                    "scalp15": should_notify("scalp15"),
                    "scalper": should_notify("scalp15"),  # legacy alias
                    "updated_ts": prefs.get("updated_ts"),
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def start_prefs_http() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PREFS_PORT), PrefsHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"PREFS_HTTP :{PREFS_PORT}", flush=True)


def external_webhook_allowed() -> bool:
    """Legacy flag. Control bot owns Telegram; Hermes uses API :8642 only."""
    return False


def clear_webhook(*, reason: str = "") -> None:
    if external_webhook_allowed():
        print("WEBHOOK_CLEAR_SKIPPED", reason or "manual", "external_webhook=1", flush=True)
        return
    res = tg_api("deleteWebhook", {"drop_pending_updates": False})
    print(
        "WEBHOOK_CLEARED",
        reason or "manual",
        "ok=",
        res.get("ok"),
        res.get("description") or "",
        flush=True,
    )


def poll_loop() -> None:
    env = load_env()
    chat = str(env.get("TELEGRAM_CHAT_ID") or "")
    offset = 0
    last_preventive_clear = 0.0
    if chat:
        # Don't block first poll on command registration
        threading.Thread(target=clear_old_ui, args=(chat,), daemon=True).start()
    while True:
        if external_webhook_allowed():
            # Prefs HTTP stays up; Telegram commands pause until a dedicated trade bot exists.
            time.sleep(60)
            continue

        now = time.time()
        if now - last_preventive_clear >= 60:
            last_preventive_clear = now
            threading.Thread(
                target=clear_webhook,
                kwargs={"reason": "preventive_60s"},
                daemon=True,
            ).start()

        res = tg_api(
            "getUpdates",
            {
                "timeout": 25,
                "offset": offset,
                "allowed_updates": ["message", "callback_query"],
            },
        )
        if not res.get("ok"):
            desc = str(res.get("description") or "")
            print("GET_UPDATES_FAIL", desc, flush=True)
            low = desc.lower()
            if "webhook" in low or "conflict" in low:
                clear_webhook(reason="conflict")
                last_preventive_clear = time.time()
                time.sleep(1)
            else:
                time.sleep(5)
            continue
        got = False
        for upd in res.get("result") or []:
            got = True
            offset = max(offset, int(upd.get("update_id", 0)) + 1)
            cq = upd.get("callback_query") or {}
            if cq:
                from_chat = str(((cq.get("message") or {}).get("chat") or {}).get("id") or "")
                if chat and from_chat and from_chat != chat:
                    continue
                data = str(cq.get("data") or "")
                fn = str((cq.get("from") or {}).get("first_name") or "") or None
                cq_id = str(cq.get("id") or "")
                if cq_id:
                    tg_api("answerCallbackQuery", {"callback_query_id": cq_id})
                print("CB", data[:80], flush=True)
                handle_callback(from_chat or chat, data, first_name=fn)
                continue
            msg = upd.get("message") or {}
            from_chat = str((msg.get("chat") or {}).get("id") or "")
            if chat and from_chat != chat:
                continue
            text = msg.get("text") or ""
            fn = str((msg.get("from") or {}).get("first_name") or "") or None
            if text:
                print("MSG", text[:80], flush=True)
                handle_text(from_chat or chat, text, first_name=fn)
        if not got:
            time.sleep(0.15)


def main() -> None:
    print("TELEGRAM_CONTROL_START", flush=True)
    if external_webhook_allowed():
        print("EXTERNAL_WEBHOOK_MODE", "polling disabled; prefs HTTP only", flush=True)
    else:
        clear_webhook(reason="startup")
    info = tg_api("getWebhookInfo", {})
    print("WEBHOOK_INFO", info.get("result") or info, flush=True)
    start_prefs_http()
    poll_loop()


if __name__ == "__main__":
    main()
