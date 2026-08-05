#!/usr/bin/env python3
"""Shared trade/skip event log + equity markers for Telegram charts."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

HOME = Path("/root/.vibe-trading")
TRADE_EVENTS = HOME / "trade_events.jsonl"
SKIP_EVENTS = HOME / "skip_events.jsonl"
EQUITY_HISTORY = HOME / "equity_history.json"
SCALP_EQUITY_HISTORY = HOME / "eth_scalp_equity_history.json"

MAX_TRADE_LINES = 2000
MAX_SKIP_LINES = 200
MAX_CHART_MARKERS = 40


def _append_jsonl(path: Path, row: dict[str, Any], *, max_lines: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_lines:
            path.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def record_skip(
    reason: str,
    *,
    bot: str,
    detail: str = "",
    mode: str | None = None,
    extra: dict | None = None,
) -> None:
    row = {
        "ts": time.time(),
        "bot": bot,
        "reason": reason,
        "detail": detail[:200],
        "mode": mode,
    }
    if extra:
        row.update(extra)
    _append_jsonl(SKIP_EVENTS, row, max_lines=MAX_SKIP_LINES)
    print(f"SKIP_LOG {bot} {reason} {detail[:80]}", flush=True)


def record_trade_event(
    *,
    bot: str,
    side: str,
    symbol: str,
    price: float,
    usd: float,
    mode: str | None = None,
    regime: str | None = None,
    pnl_pct: float | None = None,
    result: str | None = None,
    equity: float | None = None,
    reason: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Persist fill and mirror a chart marker."""
    if result is None and pnl_pct is not None:
        if pnl_pct > 0.0005:
            result = "win"
        elif pnl_pct < -0.0005:
            result = "loss"
        else:
            result = "flat"
    row = {
        "ts": time.time(),
        "bot": bot,
        "side": side,
        "symbol": symbol,
        "price": round(float(price), 8),
        "usd": round(float(usd), 4),
        "pnl_pct": None if pnl_pct is None else round(float(pnl_pct), 6),
        "result": result,
        "mode": mode,
        "regime": regime,
        "equity": None if equity is None else round(float(equity), 4),
    }
    if reason:
        row["reason"] = str(reason)[:120]
    if kind:
        row["kind"] = str(kind)[:40]
    _append_jsonl(TRADE_EVENTS, row, max_lines=MAX_TRADE_LINES)

    # Marker kind for charts
    if side == "buy":
        kind_mark = "buy"
        label = f"BUY {symbol}"
    elif result == "win":
        kind_mark = "win"
        label = f"WIN {symbol}"
    elif result == "loss":
        kind_mark = "loss"
        label = f"LOSS {symbol}"
    else:
        kind_mark = "sell"
        label = f"SELL {symbol}"

    hist = SCALP_EQUITY_HISTORY if bot == "scalper" else EQUITY_HISTORY
    eq = float(equity) if equity is not None else float(price)
    try:
        import equity_chart as ec

        ec.record_trade_marker(
            hist,
            kind=kind_mark,
            equity=eq,
            label=label,
            price=float(price),
            bot=bot,
            symbol=symbol,
        )
        # Also mirror onto main book chart for scalper visibility
        if bot == "scalper" and hist != EQUITY_HISTORY:
            ec.record_trade_marker(
                EQUITY_HISTORY,
                kind=kind_mark,
                equity=eq,
                label=label,
                price=float(price),
                bot=bot,
                symbol=symbol,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"TRADE_MARK_FAIL {exc}", flush=True)
    print(f"TRADE_MARK {bot} {kind_mark} {symbol} usd={usd:.2f}", flush=True)

    # Ops/Telegram retro — only on closes when a recommendation is warranted
    if side == "sell" and bot in ("v6", "binance", None, ""):
        try:
            import strategy_feedback as sf

            feats: dict[str, Any] = {}
            try:
                mode_doc = json.loads(
                    (HOME / "strategy_mode.json").read_text(encoding="utf-8")
                )
                feats = dict(mode_doc.get("features") or {})
                feats["orch_reason"] = mode_doc.get("reason")
            except Exception:
                pass
            sf.record_close_feedback(
                bot=bot or "v6",
                symbol=symbol,
                result=result,
                pnl_pct=pnl_pct if pnl_pct is not None else row.get("pnl_pct"),
                usd=usd,
                mode=mode,
                reason=reason,
                kind=kind,
                equity=equity,
                features=feats,
                notify=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"FEEDBACK_FAIL {exc}", flush=True)

    return row


def record_mode_change(
    mode: str,
    *,
    reason: str,
    equity: float | None = None,
) -> None:
    row = {
        "ts": time.time(),
        "bot": "orch",
        "side": "mode",
        "symbol": mode,
        "price": 0.0,
        "usd": 0.0,
        "pnl_pct": None,
        "result": None,
        "mode": mode,
        "regime": None,
        "equity": equity,
        "reason": reason[:200],
    }
    _append_jsonl(TRADE_EVENTS, row, max_lines=MAX_TRADE_LINES)
    try:
        import equity_chart as ec

        ec.record_trade_marker(
            EQUITY_HISTORY,
            kind="mode_change",
            equity=float(equity or 0),
            label=mode.upper()[:8],
            bot="orch",
            symbol=mode,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"MODE_MARK_FAIL {exc}", flush=True)


def recent_trade_events(limit: int = 40) -> list[dict]:
    if not TRADE_EVENTS.exists():
        return []
    try:
        lines = TRADE_EVENTS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def notional_traded_today() -> float:
    """Sum abs usd of buys/sells since UTC midnight."""
    from datetime import datetime, timezone

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    total = 0.0
    for ev in recent_trade_events(500):
        if float(ev.get("ts") or 0) < start:
            continue
        if ev.get("side") in ("buy", "sell"):
            total += abs(float(ev.get("usd") or 0))
    return total


def consecutive_losses(limit: int = 10) -> int:
    n = 0
    for ev in reversed(recent_trade_events(80)):
        if ev.get("side") != "sell":
            continue
        res = ev.get("result")
        if res == "loss":
            n += 1
        elif res == "win":
            break
    return n


def _utc_day_start() -> float:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )


def closes_today(*, bot: str | None = "v6") -> list[dict]:
    """Sell events since UTC midnight (optionally filtered by bot)."""
    start = _utc_day_start()
    out: list[dict] = []
    for ev in recent_trade_events(500):
        if float(ev.get("ts") or 0) < start:
            continue
        if ev.get("side") != "sell":
            continue
        if bot is not None and ev.get("bot") not in (None, bot):
            continue
        out.append(ev)
    return out


def win_rate_today(*, bot: str | None = "v6") -> tuple[float | None, int, int, int]:
    """Return (win_rate or None, wins, losses, closes_with_result)."""
    wins = losses = rated = 0
    for ev in closes_today(bot=bot):
        res = ev.get("result")
        if res == "win":
            wins += 1
            rated += 1
        elif res == "loss":
            losses += 1
            rated += 1
        elif res == "flat":
            rated += 1
    if rated <= 0:
        return None, wins, losses, rated
    return wins / rated, wins, losses, rated
