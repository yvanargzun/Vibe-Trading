#!/usr/bin/env python3
"""Post-close strategy feedback for Ops + Telegram (rule-based, optional).

Only emits a recommendation when the close + context justify a change or an
explicit "mantener". Silent otherwise.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HOME = Path("/root/.vibe-trading")
FEEDBACK_PATH = HOME / "strategy_feedback.jsonl"
SITUATIONS_PATH = HOME / "strategy_situations.json"
MAX_LINES = 800
CDMX = ZoneInfo("America/Mexico_City")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _append(row: dict[str, Any]) -> None:
    FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    try:
        lines = FEEDBACK_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES:
            FEEDBACK_PATH.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def recent_feedback(limit: int = 40) -> list[dict]:
    if not FEEDBACK_PATH.exists():
        return []
    try:
        lines = FEEDBACK_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-max(limit, 1) :]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


def build_situations(
    *,
    mode: str,
    reason: str,
    features: dict[str, Any] | None = None,
    equity: float | None = None,
    usable: float | None = None,
    buys_today: int | None = None,
    open_legs: int = 0,
    last_skip: str | None = None,
) -> list[dict[str, str]]:
    """Human-readable live situations for Ops (not all require action)."""
    feats = features or {}
    reason = str(reason or "")
    mode = str(mode or "?")
    eq = float(equity if equity is not None else feats.get("equity") or 0)
    usdt = float(usable if usable is not None else feats.get("usable_usdt") or 0)
    situations: list[dict[str, str]] = []

    def add(level: str, code: str, text: str) -> None:
        situations.append({"level": level, "code": code, "text": text})

    if "need_recharge" in reason:
        if "grace_1clip" in reason:
            add(
                "warn",
                "need_recharge_grace",
                f"Equity ${eq:.2f} < $50 · recarga recomendada · grace 1 clip activo (usable ${usdt:.2f})",
            )
        else:
            add(
                "warn",
                "need_recharge",
                f"Equity ${eq:.2f} < $50 · sin polvo útil (usable ${usdt:.2f}) · espera depósito o unlock",
            )
    if "fee_budget_soft" in reason or "allow_one_clip" in reason:
        frac = feats.get("notional_frac")
        lim = feats.get("fee_limit")
        add(
            "info",
            "fee_budget_soft",
            f"Fee soft: notional_frac={frac} lim={lim} · defensive 1 clip (no standby eterno)",
        )
    if "fee_budget" in reason and "soft" not in reason:
        add("warn", "fee_budget", f"Fee budget duro: {reason[:120]}")
    if "day_edge_fail" in reason:
        add(
            "warn",
            "day_edge_fail",
            f"Win-rate del día bajo · solo exits · wr={feats.get('win_rate_today')}",
        )
    if mode == "standby":
        add("warn", "standby", f"Standby: {reason[:140]}")
    if mode == "recap" and "need_recharge" not in reason:
        add("info", "recap", f"Recap: {reason[:140]}")
    thr = feats.get("day_loss_thr")
    day_pnl = feats.get("day_pnl_pct")
    try:
        if thr is not None and day_pnl is not None and float(day_pnl) <= float(thr):
            add(
                "warn",
                "day_loss",
                f"Day-loss activo: {float(day_pnl):+.2f}% ≤ thr {float(thr):.1f}%",
            )
    except (TypeError, ValueError):
        pass
    try:
        ls = int(feats.get("loss_streak") or 0)
        if ls >= 2:
            add("warn", "loss_streak", f"Racha de pérdidas: {ls}")
    except (TypeError, ValueError):
        pass
    if usdt < 1.0 and eq >= 5.0:
        add(
            "warn",
            "usable_zero",
            f"Usable≈${usdt:.2f} con equity ${eq:.2f} · capital posiblemente atrapado",
        )
    if open_legs:
        add("ok", "open_leg", f"Piernas abiertas: {open_legs} · vigilando exits")
    if buys_today is not None:
        add("info", "buys_today", f"Buys hoy: {buys_today}")
    wr = feats.get("win_rate_today")
    rated = feats.get("closes_rated_today")
    if wr is not None and rated:
        add("info", "win_rate_today", f"Win-rate hoy: {float(wr)*100:.0f}% ({rated} cierres)")
    if last_skip:
        add("info", "last_skip", f"Último skip: {last_skip[:140]}")
    if not situations:
        add("ok", "clear", "Sin flags especiales · modo normal")
    return situations


def persist_situations(situations: list[dict[str, str]], *, extra: dict | None = None) -> None:
    doc = {
        "ts": time.time(),
        "ts_iso": datetime.now(timezone.utc).isoformat(),
        "ts_cdmx": datetime.now(CDMX).strftime("%Y-%m-%d %H:%M:%S"),
        "situations": situations,
    }
    if extra:
        doc.update(extra)
    SITUATIONS_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def analyze_close(
    *,
    bot: str,
    symbol: str,
    result: str | None,
    pnl_pct: float | None,
    usd: float | None,
    mode: str | None,
    reason: str | None,
    kind: str | None,
    equity: float | None = None,
    features: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return a feedback row or None if no recommendation is warranted."""
    feats = features or {}
    orch_reason = str(feats.get("orch_reason") or "")
    res = (result or "").lower() or None
    pnl = float(pnl_pct) if pnl_pct is not None else None
    usd_v = float(usd or 0)
    kind_s = str(kind or reason or "").lower()
    action = "mantener"
    title = ""
    detail = ""
    priority = "low"

    # Clear loss patterns
    if res == "loss" and pnl is not None and pnl <= -0.012:
        if "sl" in kind_s or "stop" in kind_s:
            action = "ajustar"
            title = "SL tocado — subir selectividad"
            detail = (
                f"Pérdida {pnl*100:.2f}% en {symbol} por stop. "
                "Mantén clip; exige score más alto en el próximo régimen chop/bear "
                "o evita majors si el setup fue solo 'bull_dip' débil."
            )
            priority = "med"
        elif "time" in kind_s:
            action = "ajustar"
            title = "Time-stop en rojo — menos paciencia en chop"
            detail = (
                f"Cierre TIME en pérdida ({pnl*100:.2f}%). "
                "Si el régimen era chop, prioriza no entrar; si era bull, mantener time-stop."
            )
            priority = "med"
        else:
            action = "ajustar"
            title = "Pérdida material — revisar entrada"
            detail = (
                f"{symbol} cerró {pnl*100:.2f}%. "
                "No subas clip. Si loss_streak≥2, dejar defensive/grace y no forzar buys."
            )
            priority = "med"
    elif res == "loss" and pnl is not None and -0.012 < pnl <= -0.0005:
        # Tiny loss / fee-like — only speak if fee pressure high
        try:
            frac = float(feats.get("notional_frac") or 0)
            lim = float(feats.get("fee_limit") or 0.55)
        except (TypeError, ValueError):
            frac, lim = 0.0, 0.55
        if frac >= lim * 0.85:
            action = "ajustar"
            title = "Micro-loss con fee pressure"
            detail = (
                f"Cierre casi flat/negativo ({pnl*100:.2f}%) con notional_frac={frac:.2f}. "
                "Mantén fee_budget_soft (1 clip); no abrás segundo giro el mismo día."
            )
            priority = "low"
        else:
            return None
    elif res == "win" and pnl is not None and pnl >= 0.015:
        action = "mantener"
        title = "Win sólido — mantener estrategia"
        detail = (
            f"{symbol} +{pnl*100:.2f}% ({kind or reason or 'exit'}). "
            "Conserva TP/trail/SL actuales; no aumentes clip con equity < $50."
        )
        priority = "low"
    elif res == "win" and pnl is not None and 0.0005 < pnl < 0.01:
        # Tiny win — recommend maintain unless recharge needed
        if float(equity or feats.get("equity") or 0) < 50:
            action = "ajustar"
            title = "Win pequeño — prioriza recarga de capital"
            detail = (
                f"Win +{pnl*100:.2f}% pero equity micro. "
                "El edge de fees mejora más depositando a ≥$50 que rotando más clips."
            )
            priority = "med"
        else:
            return None
    elif res in ("flat", "unknown"):
        if usd_v >= 5 and (kind_s.startswith("fund") or "sync" in kind_s):
            action = "ajustar"
            title = "Cierre de funding/unlock — revisar polvo USDT"
            detail = (
                f"{symbol} salió por {kind or reason} sin edge claro. "
                "OK para liberar USDT; evita last-resort sobre legs jóvenes."
            )
            priority = "low"
        else:
            return None
    else:
        # Win mid-range or unclassified: silence unless orchestrator flags
        if "need_recharge" in orch_reason and res == "win":
            action = "ajustar"
            title = "Tras win: aún need_recharge"
            detail = (
                "Cerraste en verde pero equity < $50. "
                "Deja grace (máx 1 buy/día) y deposita; no pases a v6_primary."
            )
            priority = "med"
        else:
            return None

    now = time.time()
    row = {
        "ts": now,
        "ts_iso": datetime.now(timezone.utc).isoformat(),
        "ts_cdmx": datetime.now(CDMX).strftime("%Y-%m-%d %H:%M:%S"),
        "bot": bot,
        "symbol": symbol,
        "result": res,
        "pnl_pct": None if pnl is None else round(pnl, 6),
        "usd": round(usd_v, 4),
        "mode": mode,
        "exit_reason": reason,
        "exit_kind": kind,
        "action": action,
        "title": title,
        "detail": detail,
        "priority": priority,
        "equity": None if equity is None else round(float(equity), 4),
        "source": "close_feedback",
    }
    return row


def notify_telegram(row: dict[str, Any]) -> None:
    try:
        from telegram_notify_prefs import tg_api, load_env, filter_keyboard

        chat = load_env().get("TELEGRAM_CHAT_ID")
        if not chat:
            return
        sign = ""
        pnl = row.get("pnl_pct")
        if isinstance(pnl, (int, float)):
            sign = f" · PnL {pnl*100:+.2f}%"
        verb = "MANTENER" if row.get("action") == "mantener" else "AJUSTAR"
        text = (
            f"[Retro · {row.get('bot')}] {verb}\n"
            f"{row.get('ts_cdmx')} CDMX\n"
            f"{row.get('symbol')} {row.get('result')}{sign}\n"
            f"{row.get('title')}\n"
            f"{row.get('detail')}"
        )
        tg_api(
            "sendMessage",
            {
                "chat_id": chat,
                "text": text[:3500],
                "reply_markup": filter_keyboard(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"FEEDBACK_TG_FAIL {exc}", flush=True)


def record_close_feedback(
    *,
    bot: str,
    symbol: str,
    result: str | None,
    pnl_pct: float | None,
    usd: float | None,
    mode: str | None = None,
    reason: str | None = None,
    kind: str | None = None,
    equity: float | None = None,
    features: dict[str, Any] | None = None,
    notify: bool = True,
) -> dict[str, Any] | None:
    row = analyze_close(
        bot=bot,
        symbol=symbol,
        result=result,
        pnl_pct=pnl_pct,
        usd=usd,
        mode=mode,
        reason=reason,
        kind=kind,
        equity=equity,
        features=features,
    )
    if not row:
        return None
    _append(row)
    print(
        f"FEEDBACK {row.get('action')} {symbol} {row.get('title')}",
        flush=True,
    )
    if notify:
        notify_telegram(row)
    return row
