#!/usr/bin/env python3
"""Telegram control: filter keyboard + on-demand strategy charts.

Commands (natural language or slash; ignore notify filter):
  binance v6 smart fast  → chart Binance
  eth scalping           → chart ETH scalper
  alpaca paper scalping  → chart Alpaca paper
  estado                 → charts only (Binance + ETH + Alpaca)
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from telegram_notify_prefs import (
    BTN_ALL,
    BTN_FB,
    BTN_SALES_EXIT,
    BTN_SALES_TEST,
    BTN_SCALPER,
    BTN_VIBE,
    BUTTON_TO_MODE,
    filter_keyboard,
    is_sales_test,
    load_env,
    load_prefs,
    mode_label,
    save_prefs,
    set_sales_test,
    should_notify,
    tg_api,
)

HOME = Path("/root/.vibe-trading")
ALPACA = Path("/root/.alpaca-paper")
PREFS_PORT = 8897
DEFAULT_MESSENGER_URL = "https://synaptika-messengerfb.onrender.com"
HELP_TEXT = (
    "Comandos (resumen detallado + grafica):\n"
    "• binance v6 smart fast  — panorama Binance\n"
    "• eth scalping           — panorama Scalper ETH\n"
    "• alpaca paper scalping  — panorama Alpaca paper\n"
    "• estado                 — los 3 panoramas juntos\n\n"
    "Atajos: /binance  /eth  /alpaca  /estado\n"
    "Filtro de avisos automaticos: /filtro\n\n"
    "Probar ventas:\n"
    f"• {BTN_SALES_TEST} — chat con Messenger sales (bypass owner skip)\n"
    f"• {BTN_SALES_EXIT} — vuelve a trading/comandos\n\n"
    "Botones filtro:\n"
    f"• {BTN_VIBE}\n"
    f"• {BTN_SCALPER}\n"
    f"• {BTN_FB}\n"
    f"• {BTN_ALL}"
)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _norm(text: str) -> str:
    t = (text or "").lower().strip()
    t = t.split("@", 1)[0]
    t = re.sub(r"[^a-z0-9\s/+_-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def detect_intent(text: str) -> str | None:
    """Return vibe|scalper|alpaca|estado|help|filtro or None."""
    n = _norm(text)
    if not n:
        return None
    # strip leading slash for matching
    bare = n[1:] if n.startswith("/") else n

    if bare in ("ayuda", "help") or bare.startswith("ayuda ") or bare.startswith("help "):
        return "help"
    if bare in ("filtro", "menu", "start") or bare.startswith("filtro"):
        return "filtro"

    # estado / status first (exact-ish)
    if bare in ("estado", "status", "all", "todo", "todas") or bare.startswith(
        ("estado ", "status ")
    ):
        return "estado"

    # alpaca before generic "scalping"
    if any(
        k in bare
        for k in (
            "alpaca",
            "paper scalp",
            "paper trading",
            "alpaca paper",
        )
    ):
        return "alpaca"

    # eth scalper
    if any(
        k in bare
        for k in (
            "eth scalp",
            "eth scalping",
            "scalper eth",
            "scalping eth",
            "/eth",
            "eth ",
        )
    ) or bare in ("scalper", "scalp", "/scalper", "/scalp", "eth"):
        return "scalper"

    # binance / vibe / smart fast v6
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


def build_scalper_digest() -> str:
    st = _read_json(HOME / "eth_scalp_state.json")
    eq, day_open, _, _, _ = _scalper_equity()
    hist = _read_json(HOME / "eth_scalp_equity_history.json")
    pts = list(hist.get("points") or [])
    prev_eq = float(pts[-2]["equity"]) if len(pts) >= 2 else eq
    chg = _pct(eq, prev_eq) if prev_eq else 0.0
    realized = float(st.get("realized_pnl_today") or 0)
    # Mark change vs day open only if day_open is sane (<= 3x equity)
    mark_pnl = 0.0
    mark_pct = 0.0
    if day_open > 0 and eq > 0 and day_open <= eq * 3 and eq <= day_open * 3:
        mark_pnl = eq - day_open
        mark_pct = _pct(eq, day_open)
    pos = st.get("position") or {}
    reserved = float(st.get("reserved_usdt") or 0)
    bank = float(st.get("bankroll_usdt") or 0)
    fills = st.get("fills_today") or 0
    rounds = st.get("roundtrips_today") or 0
    sign = "+" if chg >= 0 else ""
    rsign = "+" if realized >= 0 else ""
    msign = "+" if mark_pnl >= 0 else ""
    mpct_sign = "+" if mark_pct >= 0 else ""

    lines = [
        "[ETH scalping] Resumen · hybrid scalper",
        "Pedido ahora (solo capital del scalper + PnL realizado)",
        "",
        "Capital scalper (real)",
        f"- Book actual: ${eq:.2f}",
        f"- Bankroll: ${bank:.2f}" if bank else f"- Bankroll: n/d",
        f"- Reserved USDT: ${reserved:.2f}",
        f"- Apertura dia (book): ${day_open:.2f}" if day_open else "- Apertura dia: n/d",
        f"- Cambio vs punto anterior: {sign}{chg:.2f}%",
        "",
        "PnL",
        f"- Realizado hoy (suma de trades cerrados): {rsign}${realized:.2f}",
    ]
    if day_open > 0 and (abs(mark_pnl) > 1e-9 or abs(eq - day_open) > 1e-9):
        lines.append(
            f"- Mark vs apertura (no es ganancia cerrada): {msign}${mark_pnl:.2f} ({mpct_sign}{mark_pct:.2f}%)"
        )
    lines += [
        "",
        "Posicion",
    ]
    if isinstance(pos, dict) and pos.get("side"):
        lines.append(
            f"- {pos.get('side')} @ {pos.get('entry')} (~${float(pos.get('usd') or 0):.2f})"
        )
    else:
        lines.append("- flat (sin trade abierto)")
    lines += [
        "",
        "Actividad de hoy",
        f"- Fills: {fills} | roundtrips: {rounds}",
        f"- Regimen: {_regime_txt(st.get('last_regime'))}",
        f"- Futures: {st.get('futures_enabled')}",
        f"- Kill: {'SI' if st.get('killed') else 'no'} | racha perdidas: {st.get('loss_streak', 0)}",
        f"- Float activo: {st.get('active_float')}",
    ]
    if st.get("pause_until"):
        lines.append(f"- Pausa hasta: {st.get('pause_until')}")
    lines.append(f"- Filtro avisos: {mode_label(load_prefs().get('mode', 'all'))}")
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


def build_scalper_caption() -> str:
    return build_scalper_digest()


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


def _scalper_equity() -> tuple[float, float, float, float, float]:
    st = _read_json(HOME / "eth_scalp_state.json")
    hist = _read_json(HOME / "eth_scalp_equity_history.json")
    pts = list(hist.get("points") or [])
    day_open = float(st.get("day_open_equity") or 0)
    bank = float(st.get("bankroll_usdt") or 0)
    reserved = float(st.get("reserved_usdt") or 0)
    last = float(st.get("last_equity") or 0)
    eq = 0.0
    if pts:
        eq = float(pts[-1].get("equity") or 0)
    if eq <= 0:
        eq = max(bank, reserved, last, day_open, 0.0)
    # Reject obviously contaminated history (old whole-wallet ETH ~$13 vs day_open ~$5)
    if day_open > 0 and eq > day_open * 2.5 and bank > 0:
        eq = max(bank, reserved, day_open)
    if day_open <= 0:
        day_open = eq if eq > 0 else max(bank, reserved, 0.01)
    return eq, day_open, day_open, 0.0, 0.0


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
            f"URL: {url}\nToca «{BTN_SALES_EXIT}» si quieres salir.",
        )
        return False


def enter_sales_test(chat_id: str) -> None:
    set_sales_test(True)
    send_owner(
        chat_id,
        (
            "Modo Messenger sales ON ✅\n"
            "Teclado = mismo menú que Facebook Messenger:\n"
            "• Agendar cita · Ver ejemplos · Hablar con humano\n"
            "También verás botones de rubro, fechas/horarios y links de demo.\n\n"
            f"Para volver a trading: «{BTN_SALES_EXIT}»"
        ),
    )
    # Arranca el flujo de ventas (hola → rubros + demos/links)
    proxy_to_sales_bot(chat_id, text="hola", ensure_menu=True)


def exit_sales_test(chat_id: str) -> None:
    set_sales_test(False)
    send_owner(
        chat_id,
        (
            "Modo Messenger sales OFF.\n"
            "Vuelves a comandos de trading / filtro.\n"
            f"Filtro: {mode_label(load_prefs().get('mode', 'all'))}"
        ),
    )


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
    elif kind == "scalper":
        history = HOME / "eth_scalp_equity_history.json"
        chart_path = HOME / "equity_cmd_eth.png"
        venue = "ETH scalping"
        channel = "scalper"
        eq, day_open, week_open, daily_target, weekly_target = _scalper_equity()
        text = caption if caption.strip() else build_scalper_digest()
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
    """All three strategies: detailed digest + equity chart each."""
    send_strategy_chart(chat_id, kind="vibe", caption=build_vibe_digest())
    time.sleep(0.5)
    send_strategy_chart(chat_id, kind="scalper", caption=build_scalper_digest())
    time.sleep(0.5)
    send_strategy_chart(chat_id, kind="alpaca", caption=build_alpaca_digest())


def clear_old_ui(chat_id: str, *, force_send: bool = False) -> None:
    marker = HOME / "telegram_ui_cleared.flag"
    tg_api("deleteMyCommands", {})
    tg_api(
        "setMyCommands",
        {
            "commands": [
                {"command": "estado", "description": "3 graficas: Binance + ETH + Alpaca"},
                {"command": "binance", "description": "Grafica Binance v6 smart fast"},
                {"command": "eth", "description": "Grafica ETH scalping"},
                {"command": "alpaca", "description": "Grafica Alpaca paper scalping"},
                {"command": "sales", "description": "Probar Messenger sales en este chat"},
                {"command": "trading", "description": "Salir de Messenger sales"},
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

    # Sales-test enter/exit (always available)
    if raw == BTN_SALES_TEST or low in ("/sales", "sales", "probar sales", "probar messenger sales"):
        enter_sales_test(chat_id)
        return
    if raw == BTN_SALES_EXIT or low in (
        "/trading",
        "trading",
        "salir sales",
        "salir messenger sales",
    ):
        exit_sales_test(chat_id)
        return

    # While testing sales: forward everything else to Synaptica
    if is_sales_test():
        proxy_to_sales_bot(chat_id, text=raw, first_name=first_name)
        return

    if raw in BUTTON_TO_MODE or low in BUTTON_TO_MODE:
        mode = BUTTON_TO_MODE.get(raw) or BUTTON_TO_MODE[low]
        save_prefs(mode)
        send_owner(
            chat_id,
            f"Filtro guardado: {mode_label(mode)}\n"
            "A partir de ahora solo te mando esa parte.\n"
            "(Comandos binance / eth / alpaca / estado siguen siempre.)",
        )
        return

    # Filter slash shortcuts
    n = _norm(raw)
    bare = n[1:] if n.startswith("/") else n
    if bare in ("todos", "all", "ambas"):
        save_prefs("all")
        send_owner(chat_id, f"Filtro guardado: {mode_label('all')}")
        return
    if bare in ("solo_vibe", "filtro_vibe"):
        save_prefs("vibe")
        send_owner(chat_id, f"Filtro guardado: {mode_label('vibe')}")
        return
    if bare in ("solo_scalper", "filtro_scalper"):
        save_prefs("scalper")
        send_owner(chat_id, f"Filtro guardado: {mode_label('scalper')}")
        return
    if bare in ("solo_fb", "filtro_fb", "fb"):
        if bare == "fb":
            send_owner(
                chat_id,
                "Canal FB = citas/clientes (Synaptica).\n"
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
        clear_old_ui(chat_id, force_send=True)
        return
    if intent == "estado":
        send_estado_charts(chat_id)
        return
    if intent == "vibe":
        send_strategy_chart(chat_id, kind="vibe", caption=build_vibe_digest())
        return
    if intent == "scalper":
        send_strategy_chart(chat_id, kind="scalper", caption=build_scalper_digest())
        return
    if intent == "alpaca":
        send_strategy_chart(chat_id, kind="alpaca", caption=build_alpaca_digest())
        return

    # Ignore other chatter
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
                    "fb": should_notify("fb"),
                    "vibe": should_notify("vibe"),
                    "scalper": should_notify("scalper"),
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


def clear_webhook(*, reason: str = "") -> None:
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
        clear_old_ui(chat)
    while True:
        now = time.time()
        if now - last_preventive_clear >= 60:
            clear_webhook(reason="preventive_60s")
            last_preventive_clear = now

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
        for upd in res.get("result") or []:
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
        time.sleep(0.5)


def main() -> None:
    print("TELEGRAM_CONTROL_START", flush=True)
    clear_webhook(reason="startup")
    info = tg_api("getWebhookInfo", {})
    print("WEBHOOK_INFO", info.get("result") or info, flush=True)
    start_prefs_http()
    poll_loop()


if __name__ == "__main__":
    main()
