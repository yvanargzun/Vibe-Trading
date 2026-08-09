#!/usr/bin/env python3
"""Snapshots from Binance/Alpaca state dirs for Ops + Open WebUI tools."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BRIEFS_PATH = Path(__file__).with_name("strategy_briefs.json")


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_jsonl_tail(path: Path, n: int = 40) -> list[dict]:
    if not path.exists() or n <= 0:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-max(n * 3, n) :]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-n:]


def strategy_briefs() -> dict:
    return read_json(BRIEFS_PATH)


def _day_pnl(eq: float, day_open: float) -> tuple[float, float]:
    if not day_open:
        return 0.0, 0.0
    pnl = eq - day_open
    return round(pnl, 2), round(pnl / day_open * 100.0, 2)


def _leg_from_meta(asset: str, meta: dict, *, sleeve: str | None = None) -> dict | None:
    m = meta or {}
    try:
        usd = float(m.get("usd") or 0)
    except (TypeError, ValueError):
        usd = 0.0
    if usd < 0.4:
        return None
    try:
        entry = float(m.get("entry") or m.get("avg_entry") or m.get("entry_price") or 0) or None
    except (TypeError, ValueError):
        entry = None
    try:
        qty = float(m.get("qty") or m.get("amount") or m.get("qty_available") or 0) or None
    except (TypeError, ValueError):
        qty = None
    try:
        peak = float(m.get("peak") or 0) or None
    except (TypeError, ValueError):
        peak = None
    mark = None
    if qty and qty > 0 and usd > 0:
        mark = usd / qty
    pnl_pct = m.get("pnl_pct") or m.get("unrealized_pnl_pct")
    try:
        if pnl_pct is not None:
            pnl_pct = float(pnl_pct)
            # if looks like percent units (>|1.5| rare for fraction), leave; else keep fraction
            if abs(pnl_pct) > 1.5:
                pnl_pct = pnl_pct / 100.0
        elif entry and mark and entry > 0:
            pnl_pct = (mark / entry) - 1.0
    except (TypeError, ValueError):
        pnl_pct = None
    unreal_usd = None
    if entry and qty and mark:
        unreal_usd = (mark - entry) * qty
    return {
        "asset": asset,
        "sleeve": sleeve,
        "usd": round(usd, 4),
        "qty": None if qty is None else round(qty, 8),
        "entry": None if entry is None else round(entry, 8),
        "mark": None if mark is None else round(mark, 8),
        "peak": None if peak is None else round(peak, 8),
        "pnl_pct": None if pnl_pct is None else round(float(pnl_pct), 6),
        "unreal_usd": None if unreal_usd is None else round(unreal_usd, 4),
        "opened_ts": m.get("opened_ts"),
        "score": m.get("score"),
        "regime": m.get("regime"),
    }


def binance_snapshot(vibe: Path) -> dict:
    st = read_json(vibe / "autotrade_state.json")
    mode = read_json(vibe / "strategy_mode.json")
    g = st.get("goals") or {}
    eq = float(st.get("equity") or 0)
    day_open = float(g.get("day_open_equity") or eq or 0)
    week_open = float(g.get("week_open_equity") or 0)
    day_pnl, day_pnl_pct = _day_pnl(eq, day_open)
    week_pnl, week_pnl_pct = _day_pnl(eq, week_open) if week_open else (0.0, 0.0)
    pos = st.get("positions") or {}
    legs = []
    for a, m in pos.items():
        leg = _leg_from_meta(a, m or {})
        if leg:
            legs.append(leg)
    feats = mode.get("features") or {}
    earn = float(feats.get("earn_locked_usd") or 0)
    usable = round(float(feats.get("usable_usdt") or 0), 2)
    return {
        "venue": "Binance",
        "equity": round(eq, 2),
        "usable": usable,
        "earn_locked": round(earn, 2),
        "equity_usable_gap": round(
            float(feats.get("equity_usable_gap") or max(0.0, eq - usable)), 2
        ),
        "mode": mode.get("mode") or "?",
        "locked": bool(mode.get("locked")),
        "locked_by": mode.get("locked_by"),
        "reason": str(mode.get("reason") or "")[:240],
        "since_ts": mode.get("since_ts"),
        "flips_today": mode.get("flips_today"),
        "regime": st.get("regime") or feats.get("btc_regime") or "?",
        "day_open": round(day_open, 2) if day_open else None,
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl_pct,
        "week_pnl": week_pnl,
        "week_pnl_pct": week_pnl_pct,
        "daily_target_usd": g.get("daily_target_usd"),
        "weekly_target_usd": g.get("weekly_target_usd"),
        "buys_today": st.get("buys_today"),
        "trades_done": st.get("trades_done"),
        "last_symbol": st.get("last_symbol"),
        "legs": legs,
        "halt": (vibe / "HALT").exists(),
        "strategy": st.get("strategy") or "smart-fast-v6",
        "active": str(mode.get("mode") or "") in ("v6_primary", "defensive"),
        "features": {
            "usable_usdt": feats.get("usable_usdt"),
            "earn_locked_usd": feats.get("earn_locked_usd"),
            "equity_usable_gap": feats.get("equity_usable_gap"),
            "btc_regime": feats.get("btc_regime"),
            "btc_chg24": feats.get("btc_chg24"),
            "btc_chg1h": feats.get("btc_chg1h"),
            "day_pnl_pct": feats.get("day_pnl_pct"),
            "loss_streak": feats.get("loss_streak"),
            "notional_frac": feats.get("notional_frac"),
            "fee_limit": feats.get("fee_limit"),
            "day_loss_thr": feats.get("day_loss_thr"),
            "win_rate_today": feats.get("win_rate_today"),
            "closes_rated_today": feats.get("closes_rated_today"),
            "equity": feats.get("equity"),
        },
    }


def alpaca_snapshot(alpaca: Path) -> dict:
    st = read_json(alpaca / "state.json")
    g = st.get("goals") or {}
    eq = float(st.get("equity") or st.get("last_equity") or 0)
    day_open = float(g.get("day_open_equity") or eq or 0)
    week_open = float(g.get("week_open_equity") or 0)
    day_pnl, day_pnl_pct = _day_pnl(eq, day_open)
    week_pnl, week_pnl_pct = _day_pnl(eq, week_open) if week_open else (0.0, 0.0)
    sleeves = st.get("sleeves") or {}
    legs = []
    cash = None
    for sk, book in sleeves.items():
        b = book or {}
        if cash is None and b.get("cash") is not None:
            try:
                cash = round(float(b.get("cash") or 0), 2)
            except (TypeError, ValueError):
                cash = None
        for a, m in (b.get("positions") or {}).items():
            leg = _leg_from_meta(a, m or {}, sleeve=sk)
            if leg:
                legs.append(leg)
    if cash is None and st.get("cash") is not None:
        try:
            cash = round(float(st.get("cash") or 0), 2)
        except (TypeError, ValueError):
            cash = None
    return {
        "venue": "Alpaca",
        "equity": round(eq, 2),
        "cash": cash,
        "mode": st.get("active_mode") or "?",
        "title": st.get("mode_title") or "",
        "regime": st.get("regime") or "?",
        "day_open": round(day_open, 2) if day_open else None,
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl_pct,
        "week_pnl": week_pnl,
        "week_pnl_pct": week_pnl_pct,
        "daily_target_usd": g.get("daily_target_usd"),
        "weekly_target_usd": g.get("weekly_target_usd"),
        "buys_today": st.get("buys_today"),
        "trades_done": st.get("trades_done"),
        "legs": legs,
        "halt": (alpaca / "HALT").exists(),
        "strategy": st.get("strategy") or "canonical_v2",
    }


def equity_series(home: Path, limit: int = 400) -> dict:
    path = home / "equity_history.json"
    hist: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                hist = raw
            elif isinstance(raw, list):
                hist = {"points": raw, "markers": [], "start_equity": None}
        except (OSError, json.JSONDecodeError, TypeError):
            hist = {}
    points = list(hist.get("points") or [])[-limit:]
    markers = list(hist.get("markers") or [])[-80:]
    clean_pts = []
    for p in points:
        try:
            clean_pts.append({"ts": float(p["ts"]), "equity": float(p["equity"])})
        except (KeyError, TypeError, ValueError):
            continue
    clean_mk = []
    for m in markers:
        try:
            clean_mk.append(
                {
                    "ts": float(m["ts"]),
                    "kind": m.get("kind") or "mark",
                    "equity": float(m.get("equity") or 0),
                    "label": str(m.get("label") or "")[:80],
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    start = hist.get("start_equity")
    if start is None and clean_pts:
        start = clean_pts[0]["equity"]
    return {
        "start_equity": start,
        "points": clean_pts,
        "markers": clean_mk,
    }


def recent_cycles(vibe: Path, n: int = 12) -> list[dict]:
    rows = read_jsonl_tail(vibe / "v6_cycles.jsonl", max(n * 20, 80))
    ends = [r for r in rows if r.get("kind") in ("CYCLE_END", "CYCLE_ERROR", "DECISION")]
    return ends[-n:]


def recent_trades(home: Path, n: int = 30) -> list[dict]:
    return read_jsonl_tail(home / "trade_events.jsonl", n)


def recent_skips(home: Path, n: int = 30) -> list[dict]:
    """Skip events for digest; drop retired scalper noise by default."""
    rows = read_jsonl_tail(home / "skip_events.jsonl", max(n * 4, 80))
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        bot = str(r.get("bot") or "").lower()
        if bot == "scalper":
            continue
        out.append(r)
    return out[-n:]


def _today_cdmx() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Mexico_City")).strftime("%Y-%m-%d")
    except Exception:
        return time.strftime("%Y-%m-%d", time.gmtime())


def _ts_to_day_cdmx(ts: Any) -> str | None:
    if ts is None or ts == "":
        return None
    try:
        from zoneinfo import ZoneInfo

        z = ZoneInfo("America/Mexico_City")
        if isinstance(ts, (int, float)):
            t = float(ts)
            if t > 1e12:
                t /= 1000.0
            return datetime.fromtimestamp(t, tz=timezone.utc).astimezone(z).strftime("%Y-%m-%d")
        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(z).strftime("%Y-%m-%d")
    except Exception:
        return None


def _classify_result(result: Any, pnl_pct: Any, *, pct_is_percent: bool) -> str | None:
    r = str(result or "").strip().lower()
    if r in ("win", "loss", "flat"):
        return r
    if pnl_pct is None:
        return None
    try:
        p = float(pnl_pct)
    except (TypeError, ValueError):
        return None
    # Binance trade_events uses fraction (0.004 = 0.4%); Alpaca exits use percent (−0.77)
    thr = 0.05 if pct_is_percent else 0.0005
    if p > thr:
        return "win"
    if p < -thr:
        return "loss"
    return "flat"


def _stats_from_closes(closes: list[dict], today: str) -> dict:
    wins = losses = flat = 0
    wins_today = losses_today = flat_today = 0
    for c in closes:
        res = c.get("result")
        if res == "win":
            wins += 1
        elif res == "loss":
            losses += 1
        elif res == "flat":
            flat += 1
        else:
            continue
        if c.get("day") == today:
            if res == "win":
                wins_today += 1
            elif res == "loss":
                losses_today += 1
            else:
                flat_today += 1
    closed = wins + losses + flat
    decided = wins + losses
    return {
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "closed": closed,
        "win_rate": round(100.0 * wins / decided, 1) if decided else None,
        "wins_today": wins_today,
        "losses_today": losses_today,
        "flat_today": flat_today,
        "closed_today": wins_today + losses_today + flat_today,
        "recent": list(reversed(closes[-12:])),
    }


def binance_closes(vibe: Path, limit: int = 500) -> list[dict]:
    rows = read_jsonl_tail(vibe / "trade_events.jsonl", limit)
    out: list[dict] = []
    for r in rows:
        side = str(r.get("side") or "").lower()
        if side in ("mode", "buy"):
            continue
        res = _classify_result(r.get("result"), r.get("pnl_pct"), pct_is_percent=False)
        if not res:
            continue
        out.append(
            {
                "ts": r.get("ts"),
                "day": _ts_to_day_cdmx(r.get("ts")),
                "symbol": r.get("symbol") or "?",
                "result": res,
                "pnl_pct": r.get("pnl_pct"),
                "usd": r.get("usd"),
                "mode": r.get("mode"),
                "reason": r.get("reason"),
                "kind": r.get("kind"),
                "source": "trade_events",
            }
        )
    return out


def alpaca_closes(alpaca: Path) -> list[dict]:
    st = read_json(alpaca / "state.json")
    out: list[dict] = []
    for sk, book in (st.get("sleeves") or {}).items():
        for ex in (book or {}).get("exits") or []:
            if not isinstance(ex, dict):
                continue
            # Alpaca stores pnl_pct in percent units
            res = _classify_result(None, ex.get("pnl_pct"), pct_is_percent=True)
            if not res:
                continue
            frac = None
            try:
                frac = float(ex.get("pnl_pct")) / 100.0
            except (TypeError, ValueError):
                frac = None
            out.append(
                {
                    "ts": ex.get("ts"),
                    "day": _ts_to_day_cdmx(ex.get("ts")),
                    "symbol": ex.get("asset") or "?",
                    "result": res,
                    "pnl_pct": frac,
                    "pnl_pct_display": ex.get("pnl_pct"),
                    "reason": ex.get("reason"),
                    "kind": ex.get("kind"),
                    "sleeve": sk,
                    "source": "exits",
                }
            )

    def _key(row: dict) -> float:
        day = row.get("day") or ""
        ts = row.get("ts") or ""
        try:
            if isinstance(ts, (int, float)):
                return float(ts)
            s = str(ts).replace("Z", "+00:00")
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return 0.0

    out.sort(key=_key)
    return out


def scalp15_closes(home: Path | None = None) -> list[dict]:
    home = home or _scalp15_home()
    st = read_json(home / "state.json")
    out: list[dict] = []
    for ex in st.get("exits") or []:
        if not isinstance(ex, dict):
            continue
        res = _classify_result(None, ex.get("pnl_pct"), pct_is_percent=True)
        if not res:
            continue
        frac = None
        try:
            frac = float(ex.get("pnl_pct")) / 100.0
        except (TypeError, ValueError):
            frac = None
        out.append(
            {
                "ts": ex.get("ts"),
                "day": _ts_to_day_cdmx(ex.get("ts")),
                "symbol": ex.get("asset") or "?",
                "result": res,
                "pnl_pct": frac,
                "pnl_pct_display": ex.get("pnl_pct"),
                "reason": ex.get("reason"),
                "kind": ex.get("kind"),
                "sleeve": "scalp15",
                "source": "exits",
            }
        )

    def _key(row: dict) -> float:
        ts = row.get("ts") or ""
        try:
            if isinstance(ts, (int, float)):
                return float(ts)
            s = str(ts).replace("Z", "+00:00")
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return 0.0

    out.sort(key=_key)
    return out


def win_loss_table(vibe: Path, alpaca: Path) -> dict:
    today = _today_cdmx()
    bn = _stats_from_closes(binance_closes(vibe), today)
    ap = _stats_from_closes(alpaca_closes(alpaca), today)
    s15 = _stats_from_closes(scalp15_closes(), today)
    return {"today": today, "binance": bn, "alpaca": ap, "alpaca_scalp15": s15}


def live_situations(vibe: Path) -> dict[str, Any]:
    """Situations snapshot written by orchestrator + live fallback.

    Always overlay live mode/locked from strategy_mode.json so Ops stays
    in sync when orch early-returns on sticky lock.
    """
    bn = binance_snapshot(vibe)
    live_mode = str(bn.get("mode") or "")
    live_reason = str(bn.get("reason") or "")
    live_locked = bool(bn.get("locked"))
    snap = read_json(vibe / "strategy_situations.json")
    if snap.get("situations"):
        situations = list(snap.get("situations") or [])
        # Inject sticky-lock / active-mode banner if missing or stale
        codes = {str(s.get("code") or "") for s in situations if isinstance(s, dict)}
        if live_locked and "mode_locked" not in codes:
            situations.insert(
                0,
                {
                    "level": "ok",
                    "code": "mode_locked",
                    "text": (
                        f"Modo forzado {live_mode or '?'} (locked"
                        f"{(' por ' + str(bn.get('locked_by'))) if bn.get('locked_by') else ''})"
                    ),
                },
            )
        if live_mode in ("v6_primary", "defensive") and "mode_active" not in codes:
            situations.insert(
                0 if live_locked else 0,
                {
                    "level": "ok",
                    "code": "mode_active",
                    "text": f"Binance activo · strategy={bn.get('strategy')} · mode={live_mode}",
                },
            )
        return {
            "ts": snap.get("ts"),
            "ts_cdmx": snap.get("ts_cdmx"),
            "mode": live_mode or snap.get("mode"),
            "reason": live_reason or snap.get("reason"),
            "locked": live_locked,
            "situations": situations,
            "source": "strategy_situations.json+live_overlay",
        }
    # Fallback derive from mode + state
    feats = dict(bn.get("features") or {})
    reason = live_reason
    mode = live_mode
    situations: list[dict[str, str]] = []

    def add(level: str, code: str, text: str) -> None:
        situations.append({"level": level, "code": code, "text": text})

    eq = float(bn.get("equity") or 0)
    usdt = float(bn.get("usable") or 0)
    earn = float(bn.get("earn_locked") or feats.get("earn_locked_usd") or 0)
    if live_locked:
        add(
            "ok",
            "mode_locked",
            f"Modo forzado {mode or '?'} (locked"
            f"{(' por ' + str(bn.get('locked_by'))) if bn.get('locked_by') else ''})",
        )
    if mode in ("v6_primary", "defensive"):
        add(
            "ok",
            "mode_active",
            f"Binance activo · strategy={bn.get('strategy')} · mode={mode}",
        )
    if "need_recharge" in reason:
        grace = (
            "grace 2 clips"
            if "grace_2clip" in reason
            else ("grace 1 clip" if "grace" in reason else "")
        )
        add(
            "warn",
            "need_recharge",
            f"Equity ${eq:.2f} < $50 · recarga · usable ${usdt:.2f}"
            + (f" · {grace}" if grace else "")
            + (f" · Earn~${earn:.0f}" if earn >= 1 else ""),
        )
    if earn >= 5 and usdt + 1 < eq:
        add(
            "warn",
            "earn_trap",
            f"Flexible Earn ~${earn:.2f} infla equity; usable Spot ${usdt:.2f}",
        )
    if "fee_budget_soft" in reason:
        add("info", "fee_budget_soft", reason[:160])
    if mode == "standby":
        add("warn", "standby", reason[:160] or "standby")
    if mode == "recap":
        add("info", "recap", reason[:160] or "recap")
    if usdt < 1 and eq >= 5:
        add("warn", "usable_zero", f"Usable≈0 con equity ${eq:.2f}")
    if bn.get("legs"):
        add("ok", "open_leg", f"Piernas abiertas: {len(bn['legs'])}")
    if not situations:
        add("ok", "clear", "Sin flags especiales")
    return {
        "ts": time.time(),
        "ts_cdmx": None,
        "mode": mode,
        "reason": reason,
        "locked": live_locked,
        "situations": situations,
        "source": "derived",
    }


def feedback_history(vibe: Path, limit: int = 40) -> list[dict]:
    rows = read_jsonl_tail(vibe / "strategy_feedback.jsonl", max(limit, 1))
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "ts": r.get("ts"),
                "ts_cdmx": r.get("ts_cdmx"),
                "ts_iso": r.get("ts_iso"),
                "bot": r.get("bot") or "v6",
                "symbol": r.get("symbol"),
                "result": r.get("result"),
                "pnl_pct": r.get("pnl_pct"),
                "usd": r.get("usd"),
                "action": r.get("action"),
                "title": r.get("title"),
                "detail": r.get("detail"),
                "priority": r.get("priority"),
                "mode": r.get("mode"),
                "exit_reason": r.get("exit_reason") or r.get("reason"),
                "exit_kind": r.get("exit_kind") or r.get("kind"),
                "equity": r.get("equity"),
            }
        )
    return list(reversed(out))


def learning_history(vibe: Path, limit: int = 40) -> list[dict]:
    """Adaptive tuner journal (knob patches from feedback)."""
    rows = read_jsonl_tail(vibe / "learning_journal.jsonl", max(limit, 1))
    out: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "ts": r.get("ts"),
                "ts_cdmx": r.get("ts_cdmx"),
                "applied": bool(r.get("applied")),
                "action": r.get("action"),
                "symbol": r.get("symbol"),
                "title": r.get("title"),
                "reason": r.get("reason"),
                "patch": r.get("patch") or {},
                "before": r.get("before") or {},
                "after": r.get("after") or {},
                "note": r.get("note"),
            }
        )
    return list(reversed(out))


def knobs_overlay_snapshot(vibe: Path) -> dict:
    doc = read_json(vibe / "v6_knobs_overlay.json")
    st = read_json(vibe / "adaptive_tuner_state.json")
    return {
        "knobs": doc.get("knobs") or {},
        "updated_ts": doc.get("updated_ts"),
        "by": doc.get("by"),
        "note": doc.get("note"),
        "applies_today": st.get("applies_today"),
        "applies_day": st.get("applies_day"),
        "last_learn_ts": st.get("last_learn_ts"),
    }


def trade_ledger(vibe: Path, alpaca: Path, limit: int = 50) -> list[dict]:
    """Buy/sell ledger with detail for Ops tables (newest first)."""
    out: list[dict] = []

    def _push(venue: str, r: dict) -> None:
        side = str(r.get("side") or "").lower()
        if side not in ("buy", "sell"):
            return
        res = r.get("result")
        if side == "sell" and not res:
            res = _classify_result(None, r.get("pnl_pct"), pct_is_percent=False)
        out.append(
            {
                "ts": r.get("ts"),
                "venue": venue,
                "side": side,
                "symbol": r.get("symbol") or "?",
                "price": r.get("price"),
                "usd": r.get("usd"),
                "pnl_pct": r.get("pnl_pct"),
                "result": res,
                "mode": r.get("mode"),
                "regime": r.get("regime"),
                "reason": r.get("reason"),
                "kind": r.get("kind"),
                "equity": r.get("equity"),
                "bot": r.get("bot"),
            }
        )

    for r in recent_trades(vibe, max(limit * 2, 80)):
        _push("binance", r)
    for r in recent_trades(alpaca, max(limit, 40)):
        _push("alpaca", r)
    for r in recent_trades(_scalp15_home(), max(limit, 40)):
        _push("alpaca_scalp15", r)

    def _key(row: dict) -> float:
        try:
            t = float(row.get("ts") or 0)
            return t / 1000.0 if t > 1e12 else t
        except (TypeError, ValueError):
            return 0.0

    out.sort(key=_key, reverse=True)
    return out[: max(1, min(limit, 200))]


def open_positions_table(vibe: Path, alpaca: Path) -> list[dict]:
    """Combined open legs for Ops."""
    rows: list[dict] = []
    bn = binance_snapshot(vibe)
    for L in bn.get("legs") or []:
        rows.append({"venue": "binance", **L})
    ap = alpaca_snapshot(alpaca)
    for L in ap.get("legs") or []:
        rows.append({"venue": "alpaca", **L})
    s15 = alpaca_scalp15_snapshot(_scalp15_home())
    for L in s15.get("legs") or []:
        rows.append({"venue": "alpaca_scalp15", **L})
    return rows


def activity(vibe: Path, alpaca: Path, limit: int = 40) -> dict:
    cycles = list(reversed(recent_cycles(vibe, min(limit, 20))))
    trades_bn = recent_trades(vibe, limit)
    trades_ap = recent_trades(alpaca, limit)
    s15_home = _scalp15_home()
    trades_s15 = recent_trades(s15_home, limit)
    skips_bn = recent_skips(vibe, limit)
    skips_ap = recent_skips(alpaca, min(limit, 20))
    feed: list[dict] = []
    for t in trades_bn:
        feed.append({"source": "binance", "kind": "trade", **t})
    for t in trades_ap:
        feed.append({"source": "alpaca", "kind": "trade", **t})
    for t in trades_s15:
        feed.append({"source": "alpaca_scalp15", "kind": "trade", **t})
    for s in skips_bn:
        feed.append({"source": "binance", "kind": "skip", **s})
    for s in skips_ap:
        feed.append({"source": "alpaca", "kind": "skip", **s})
    for c in recent_cycles(vibe, limit):
        feed.append({"source": "binance", "kind": "cycle", **c})

    def _ts(row: dict) -> float:
        for k in ("ts", "ts_unix", "time"):
            v = row.get(k)
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str) and v:
                try:
                    s = v.replace("Z", "+00:00")
                    return datetime.fromisoformat(s).timestamp()
                except Exception:
                    pass
        return 0.0

    feed.sort(key=_ts, reverse=True)
    return {
        "cycles": cycles,
        "trades_binance": list(reversed(trades_bn)),
        "trades_alpaca": list(reversed(trades_ap)),
        "trades_scalp15": list(reversed(trades_s15)),
        "skips_binance": list(reversed(skips_bn)),
        "feed": feed[:limit],
    }


def alpaca_scalp15_snapshot(home: Path) -> dict:
    """Separate Alpaca paper account — 15m momentum scalper."""
    st = read_json(home / "state.json")
    g = st.get("goals") or {}
    eq = float(st.get("equity") or 0)
    day_open = float(g.get("day_open_equity") or eq or 0)
    week_open = float(g.get("week_open_equity") or 0)
    day_pnl, day_pnl_pct = _day_pnl(eq, day_open)
    week_pnl, week_pnl_pct = _day_pnl(eq, week_open) if week_open else (0.0, 0.0)
    legs = []
    for a, m in (st.get("positions") or {}).items():
        leg = _leg_from_meta(a, m or {}, sleeve="scalp15")
        if leg:
            legs.append(leg)
    return {
        "venue": "Alpaca · scalp15",
        "equity": round(eq, 2) if eq else 0,
        "mode": "scalp15",
        "title": "15m momentum",
        "regime": st.get("regime") or "?",
        "day_open": round(day_open, 2) if day_open else None,
        "day_pnl": day_pnl,
        "day_pnl_pct": day_pnl_pct,
        "week_pnl": week_pnl,
        "week_pnl_pct": week_pnl_pct,
        "daily_target_usd": g.get("daily_target_usd"),
        "buys_today": st.get("buys_today"),
        "trades_done": st.get("trades_today"),
        "last_symbol": st.get("last_symbol"),
        "legs": legs,
        "halt": (home / "HALT").exists(),
        "strategy": st.get("strategy") or "scalp15-momentum-v1",
        "present": (home / "state.json").exists() or (home / "alpaca_scalp15.py").exists(),
    }


def _scalp15_home() -> Path:
    import os

    return Path(os.environ.get("ALPACA_SCALP15_HOME", "/data/alpaca_scalp15"))


def digest_text(vibe: Path, alpaca: Path) -> str:
    bn = binance_snapshot(vibe)
    ap = alpaca_snapshot(alpaca)
    s15 = alpaca_scalp15_snapshot(_scalp15_home())
    briefs = strategy_briefs()
    lines = [
        f"# Synaptika Trade digest · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "",
        "## Binance",
        f"- Strategy: {bn.get('strategy')} · mode: {bn.get('mode')} · halt: {bn.get('halt')}",
        f"- Equity: ${bn.get('equity')} · usable: ${bn.get('usable')} · Earn: ${bn.get('earn_locked') or 0} · day: {bn.get('day_pnl_pct')}%",
        f"- Regime: {bn.get('regime')} · reason: {bn.get('reason')}",
        f"- Buys/trades today: {bn.get('buys_today')}/{bn.get('trades_done')} · last: {bn.get('last_symbol') or '—'}",
        "- Legs:",
    ]
    if bn.get("legs"):
        for L in bn["legs"]:
            lines.append(f"  - {L['asset']}: ${L['usd']}")
    else:
        lines.append("  - (none)")
    brief_bn = (briefs.get("binance") or {}).get("summary") or ""
    if brief_bn:
        lines += ["", f"Brief: {brief_bn}"]

    lines += [
        "",
        "## Alpaca",
        f"- Strategy: {ap.get('strategy')} · mode: {ap.get('mode')} · halt: {ap.get('halt')}",
        f"- Equity: ${ap.get('equity')} · cash: ${ap.get('cash')} · day: {ap.get('day_pnl_pct')}%",
        f"- Regime: {ap.get('regime')} · title: {ap.get('title') or '—'}",
        "- Legs:",
    ]
    if ap.get("legs"):
        for L in ap["legs"]:
            sleeve = L.get("sleeve") or ""
            lines.append(f"  - {L['asset']}{(' · ' + sleeve) if sleeve else ''}: ${L['usd']}")
    else:
        lines.append("  - (none)")
    brief_ap = (briefs.get("alpaca") or {}).get("summary") or ""
    if brief_ap:
        lines += ["", f"Brief: {brief_ap}"]

    if s15.get("present"):
        lines += [
            "",
            "## Alpaca scalp15 (15m)",
            f"- Strategy: {s15.get('strategy')} · halt: {s15.get('halt')} · regime: {s15.get('regime')}",
            f"- Equity: ${s15.get('equity')} · day: {s15.get('day_pnl_pct')}% · buys: {s15.get('buys_today')}",
            "- Legs:",
        ]
        if s15.get("legs"):
            for L in s15["legs"]:
                lines.append(f"  - {L['asset']}: ${L['usd']}")
        else:
            lines.append("  - (none)")
        brief_s15 = (briefs.get("alpaca_scalp15") or {}).get("summary") or ""
        if brief_s15:
            lines += ["", f"Brief: {brief_s15}"]

    act = activity(vibe, alpaca, limit=8)
    wl = win_loss_table(vibe, alpaca)
    bn_wl = wl.get("binance") or {}
    ap_wl = wl.get("alpaca") or {}
    s15_wl = wl.get("alpaca_scalp15") or {}
    lines += [
        "",
        "## Wins / Losses",
        (
            f"- Binance: {bn_wl.get('wins')}W / {bn_wl.get('losses')}L"
            f" (flat {bn_wl.get('flat')}) · winrate {bn_wl.get('win_rate')}%"
            f" · hoy {bn_wl.get('wins_today')}W/{bn_wl.get('losses_today')}L"
        ),
        (
            f"- Alpaca: {ap_wl.get('wins')}W / {ap_wl.get('losses')}L"
            f" (flat {ap_wl.get('flat')}) · winrate {ap_wl.get('win_rate')}%"
            f" · hoy {ap_wl.get('wins_today')}W/{ap_wl.get('losses_today')}L"
        ),
        (
            f"- scalp15: {s15_wl.get('wins')}W / {s15_wl.get('losses')}L"
            f" (flat {s15_wl.get('flat')}) · winrate {s15_wl.get('win_rate')}%"
            f" · hoy {s15_wl.get('wins_today')}W/{s15_wl.get('losses_today')}L"
        ),
        "",
        "## Cierres recientes Binance",
    ]
    for r in list(reversed(binance_closes(vibe, 200)[-8:])):
        lines.append(
            f"- {r.get('symbol')} {r.get('result')} {_fmt_pnl_frac(r.get('pnl_pct'))} "
            f"usd={r.get('usd')} day={r.get('day')}"
        )
    if not bn_wl.get("recent"):
        lines.append("- (ninguno)")
    lines += ["", "## Cierres recientes Alpaca"]
    for r in list(reversed(alpaca_closes(alpaca)[-8:])):
        lines.append(
            f"- {r.get('symbol')}[{r.get('sleeve')}] {r.get('result')} "
            f"{_fmt_pnl_pct_display(r)} · {str(r.get('reason') or '')[:70]}"
        )
    if not ap_wl.get("recent"):
        lines.append("- (ninguno)")
    lines += ["", "## Cierres recientes scalp15"]
    for r in list(reversed(scalp15_closes()[-8:])):
        lines.append(
            f"- {r.get('symbol')} {r.get('result')} "
            f"{_fmt_pnl_pct_display(r)} · {str(r.get('reason') or '')[:70]}"
        )
    if not s15_wl.get("recent"):
        lines.append("- (ninguno)")
    lines += ["", "## Recent activity"]
    for row in act.get("feed") or []:
        k = row.get("kind")
        src = row.get("source")
        if k == "trade":
            lines.append(
                f"- [{src}] {row.get('side') or '?'} {row.get('symbol') or '?'} "
                f"${row.get('usd') or '?'} · {row.get('result') or ''}"
            )
        elif k == "skip":
            lines.append(f"- [{src}] SKIP {row.get('symbol') or ''} · {str(row.get('reason') or row.get('result') or '')[:100]}")
        elif k == "cycle":
            dec = row.get("decision") or {}
            action = dec.get("action") or row.get("action") or row.get("kind")
            reason = dec.get("reason") or row.get("reason") or ""
            lines.append(f"- [cycle] {action} · {str(reason)[:100]}")
    return "\n".join(lines)[:12000]


def _fmt_pnl_frac(p: Any) -> str:
    try:
        return f"{float(p) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "?"


def _fmt_pnl_pct_display(row: dict) -> str:
    if row.get("pnl_pct_display") is not None:
        try:
            return f"{float(row['pnl_pct_display']):+.2f}%"
        except (TypeError, ValueError):
            pass
    if row.get("pnl_pct") is not None:
        return _fmt_pnl_frac(row.get("pnl_pct"))
    return "—"


def copilot_context_text(vibe: Path, alpaca: Path) -> str:
    """Rich brief for Open WebUI: enough trades/W-L to propose without asking the user."""
    bn = binance_snapshot(vibe)
    ap = alpaca_snapshot(alpaca)
    s15 = alpaca_scalp15_snapshot(_scalp15_home())
    briefs = strategy_briefs()
    wl = win_loss_table(vibe, alpaca)
    act = activity(vibe, alpaca, limit=60)
    bn_closes = list(reversed(binance_closes(vibe, 200)[-15:]))
    ap_closes = list(reversed(alpaca_closes(alpaca)[-15:]))
    s15_closes = list(reversed(scalp15_closes()[-15:]))
    feats = bn.get("features") or {}

    lines = [
        f"# Synaptika Copiloto BRIEF · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "Usa ESTE bloque como fuente completa. NO pidas al usuario get_bot_digest ni exports.",
        "Puedes proponer y, tras confirmación explícita del usuario, ejecutar "
        "set_strategy_mode / set_bot_halt / set_strategy_knobs / enqueue_trade_intent.",
        "",
        "## Restricciones operativas ahora",
    ]
    if bn.get("halt"):
        lines.append("- Binance: archivo HALT presente")
    if ap.get("halt"):
        lines.append("- Alpaca: archivo HALT presente")
    if s15.get("halt"):
        lines.append("- Alpaca scalp15: archivo HALT presente")
    day_pnl = float(bn.get("day_pnl_pct") or 0)
    eq_bn = float(bn.get("equity") or 0)
    reason = str(bn.get("reason") or "")
    day_thr = -5.0 if 0 < eq_bn < 100 else -3.0
    if day_pnl <= day_thr:
        lines.append(
            f"- Binance day-loss halt activo (day {day_pnl:.2f}% ≤ {day_thr:.0f}%; "
            f"dinámico: −5% si equity<$100 else −3%). No tiene sentido proponer buys nuevos hoy."
        )
    if eq_bn > 0 and eq_bn < 50:
        grace_note = (
            "grace_2clip (day verde)"
            if "grace_2clip" in reason
            else "grace_1clip defensive si usable≥clip"
        )
        lines.append(
            f"- Binance equity ${eq_bn:.2f} < $50 → modo need_recharge "
            f"({grace_note}; score≥MIN_BUY_SCORE_GRACE)."
        )
        earn = float(bn.get("earn_locked") or feats.get("earn_locked_usd") or 0)
        gap = float(bn.get("equity_usable_gap") or 0)
        if earn >= 1 or gap >= 5:
            lines.append(
                f"- Gap equity↔usable: ${gap:.2f} · Flexible Earn ~${earn:.2f} "
                f"(loop redimirá LD* en micro)."
            )
    if str(bn.get("mode") or "") == "standby":
        mode_doc = read_json(vibe / "strategy_mode.json") or {}
        lines.append(
            f"- Binance mode=standby · reason: {bn.get('reason') or '—'} · "
            f"usable=${bn.get('usable')} · flips_today={mode_doc.get('flips_today')}"
        )
    if bn.get("locked"):
        lines.append(
            f"- Binance LOCKED en mode={bn.get('mode')} "
            f"(by={bn.get('locked_by') or '—'}) · orquestador no auto-flip"
        )
    if str(bn.get("mode") or "") in ("v6_primary", "defensive"):
        lines.append(
            f"- Binance ESTRATEGIA ACTIVA: {bn.get('strategy')} · mode={bn.get('mode')} "
            f"(compras permitidas sujeto a score/caps)"
        )
    if "fee_budget_soft" in reason or "grace_" in reason or "need_recharge" in reason:
        lines.append(f"- Binance orch soft/recharge: {reason}")
    if float(bn.get("usable") or 0) < 1:
        lines.append("- Binance usable USDT ≈ 0 → sin dry powder para nuevas entradas (unlock idle USDT si hay alts)")
    if not any("restricciones" in x.lower() or x.startswith("- ") for x in lines[-8:]):
        lines.append("- Sin flags duros extra; revisar wins/losses y posiciones abajo")

    lines += [
        "",
        "## Binance live",
        f"- strategy={bn.get('strategy')} mode={bn.get('mode')} "
        f"locked={bn.get('locked')} locked_by={bn.get('locked_by')} "
        f"active={bn.get('active')} regime={bn.get('regime')} halt={bn.get('halt')}",
        f"- equity=${bn.get('equity')} usable=${bn.get('usable')} earn=${bn.get('earn_locked') or 0} "
        f"gap=${bn.get('equity_usable_gap') or 0} day_pnl={bn.get('day_pnl_pct')}% (${bn.get('day_pnl')})",
        f"- week_pnl={bn.get('week_pnl_pct')}% · buys_today={bn.get('buys_today')} trades_done={bn.get('trades_done')} last={bn.get('last_symbol')}",
        f"- orch_reason: {bn.get('reason')}",
        f"- adaptive_knobs: {knobs_overlay_snapshot(vibe).get('by')} "
        f"applies_today={knobs_overlay_snapshot(vibe).get('applies_today')} "
        f"note={knobs_overlay_snapshot(vibe).get('note')}",
        f"- features: btc_regime={feats.get('btc_regime')} btc_1h={feats.get('btc_chg1h')} "
        f"btc_24={feats.get('btc_chg24')} loss_streak={feats.get('loss_streak')} "
        f"notional_frac={feats.get('notional_frac')} fee_limit={feats.get('fee_limit')} "
        f"day_loss_thr={feats.get('day_loss_thr')} win_rate_today={feats.get('win_rate_today')}",
        "- open_legs:",
    ]
    if bn.get("legs"):
        for L in bn["legs"]:
            lines.append(
                f"  - {L.get('asset')} ${L.get('usd')} qty={L.get('qty')} entry={L.get('entry')} "
                f"pnl={L.get('pnl_pct')}"
            )
    else:
        lines.append("  - (none)")

    lines += ["", "## Aprendizaje auto (adaptive_tuner)"]
    kov = knobs_overlay_snapshot(vibe)
    learn = learning_history(vibe, limit=5)
    if kov.get("knobs"):
        kv = ", ".join(f"{k}={v}" for k, v in list((kov.get("knobs") or {}).items())[:8])
        lines.append(
            f"- overlay by={kov.get('by')} applies_today={kov.get('applies_today')} · {kv}"
        )
    if learn:
        for L in learn[:5]:
            applied = "APLICADO" if L.get("applied") else "log"
            patch = L.get("patch") or {}
            ptxt = ",".join(f"{k}={v}" for k, v in list(patch.items())[:4]) or "—"
            lines.append(
                f"- [{applied}] {L.get('symbol')} {L.get('action')}: {L.get('reason')} · {ptxt}"
            )
    else:
        lines.append("- (sin entradas de learning_journal aún)")

    lines += [
        "",
        "## Alpaca live",
        f"- strategy={ap.get('strategy')} mode={ap.get('mode')} title={ap.get('title') or '—'} regime={ap.get('regime')} halt={ap.get('halt')}",
        f"- equity=${ap.get('equity')} cash=${ap.get('cash')} day_pnl={ap.get('day_pnl_pct')}% (${ap.get('day_pnl')})",
        f"- week_pnl={ap.get('week_pnl_pct')}% · trades_done={ap.get('trades_done')}",
        "- open_legs:",
    ]
    if ap.get("legs"):
        for L in ap["legs"]:
            lines.append(
                f"  - {L.get('asset')} sleeve={L.get('sleeve')} ${L.get('usd')} "
                f"qty={L.get('qty')} entry={L.get('entry')} pnl={L.get('pnl_pct')}"
            )
    else:
        lines.append("  - (none)")

    lines += [
        "",
        "## Alpaca scalp15 live (cuenta paper aparte · 15m momentum)",
        f"- strategy={s15.get('strategy')} regime={s15.get('regime')} halt={s15.get('halt')} present={s15.get('present')}",
        f"- equity=${s15.get('equity')} day_pnl={s15.get('day_pnl_pct')}% buys_today={s15.get('buys_today')} "
        f"trades={s15.get('trades_done')} last={s15.get('last_symbol')}",
        f"- brief: {(briefs.get('alpaca_scalp15') or {}).get('summary') or '—'}",
        "- open_legs:",
    ]
    if s15.get("legs"):
        for L in s15["legs"]:
            lines.append(
                f"  - {L.get('asset')} ${L.get('usd')} qty={L.get('qty')} entry={L.get('entry')} "
                f"pnl={L.get('pnl_pct')}"
            )
    else:
        lines.append("  - (none)")

    bn_wl = wl.get("binance") or {}
    ap_wl = wl.get("alpaca") or {}
    s15_wl = wl.get("alpaca_scalp15") or {}
    lines += [
        "",
        f"## Wins/Losses (día CDMX {wl.get('today')})",
        (
            f"- Binance: {bn_wl.get('wins')}W/{bn_wl.get('losses')}L flat={bn_wl.get('flat')} "
            f"winrate={bn_wl.get('win_rate')}% · hoy {bn_wl.get('wins_today')}W/{bn_wl.get('losses_today')}L "
            f"closed={bn_wl.get('closed')}"
        ),
        (
            f"- Alpaca: {ap_wl.get('wins')}W/{ap_wl.get('losses')}L flat={ap_wl.get('flat')} "
            f"winrate={ap_wl.get('win_rate')}% · hoy {ap_wl.get('wins_today')}W/{ap_wl.get('losses_today')}L "
            f"closed={ap_wl.get('closed')}"
        ),
        (
            f"- scalp15: {s15_wl.get('wins')}W/{s15_wl.get('losses')}L flat={s15_wl.get('flat')} "
            f"winrate={s15_wl.get('win_rate')}% · hoy {s15_wl.get('wins_today')}W/{s15_wl.get('losses_today')}L "
            f"closed={s15_wl.get('closed')}"
        ),
        "",
        "## Últimos cierres Binance (sells con result)",
    ]
    if bn_closes:
        for r in bn_closes:
            lines.append(
                f"- {r.get('symbol')} {r.get('result')} pnl={_fmt_pnl_frac(r.get('pnl_pct'))} "
                f"usd={r.get('usd')} mode={r.get('mode')} day={r.get('day')}"
            )
    else:
        lines.append("- (sin cierres registrados)")

    lines += ["", "## Últimos cierres Alpaca (exits)"]
    if ap_closes:
        for r in ap_closes:
            lines.append(
                f"- {r.get('symbol')}[{r.get('sleeve')}] {r.get('result')} "
                f"pnl={_fmt_pnl_pct_display(r)} kind={r.get('kind')} "
                f"reason={str(r.get('reason') or '')[:80]} day={r.get('day')}"
            )
    else:
        lines.append("- (sin exits)")

    lines += ["", "## Últimos cierres scalp15 (exits)"]
    if s15_closes:
        for r in s15_closes:
            lines.append(
                f"- {r.get('symbol')} {r.get('result')} "
                f"pnl={_fmt_pnl_pct_display(r)} kind={r.get('kind')} "
                f"reason={str(r.get('reason') or '')[:80]} day={r.get('day')}"
            )
    else:
        lines.append("- (sin exits)")

    # Raw trade events (buys/sells/mode) for Binance
    lines += ["", "## Trade events Binance (recientes, incl. buys/mode)"]
    te_bn = [t for t in (act.get("trades_binance") or [])][:20]
    if te_bn:
        for t in te_bn:
            lines.append(
                f"- side={t.get('side')} {t.get('symbol')} usd={t.get('usd')} "
                f"result={t.get('result')} pnl_pct={t.get('pnl_pct')} "
                f"mode={t.get('mode')} reason={str(t.get('reason') or '')[:70]}"
            )
    else:
        lines.append("- (vacío)")

    lines += ["", "## Skips Binance (recientes)"]
    for s in (act.get("skips_binance") or [])[:12]:
        lines.append(
            f"- {s.get('reason') or s.get('result') or '?'} · "
            f"{str(s.get('detail') or s.get('symbol') or '')[:90]}"
        )
    if not (act.get("skips_binance") or []):
        lines.append("- (ninguno reciente)")

    lines += ["", "## Ciclos Binance (decisiones recientes)"]
    for c in (act.get("cycles") or [])[:10]:
        dec = c.get("decision") or {}
        action = dec.get("action") or c.get("action") or c.get("kind")
        reason = dec.get("reason") or c.get("reason") or ""
        lines.append(f"- {action} · {str(reason)[:110]}")
    if not (act.get("cycles") or []):
        lines.append("- (ninguno)")

    brief_bn = (briefs.get("binance") or {})
    brief_ap = (briefs.get("alpaca") or {})
    lines += [
        "",
        "## Strategy briefs",
        f"- Binance: {(brief_bn.get('summary') or '')[:280]}",
        f"  style: {brief_bn.get('style') or '—'}",
        f"- Alpaca: {(brief_ap.get('summary') or '')[:280]}",
        f"  style: {brief_ap.get('style') or '—'}",
        "",
        "## Cómo proponer",
        "- Separa siempre Binance vs Alpaca.",
        "- Si hay standby / day-loss / usable=0, dilo primero y no propongas entradas que el bot no puede tomar.",
        "- Basa propuestas en closes W/L, skips y legs abiertas (tamaños/entry), no en inventar fills.",
        "- Formato útil: (1) lectura del estado (2) 2–4 propuestas concretas (3) riesgos / qué vigilar.",
    ]

    sits = live_situations(vibe)
    lines += ["", "## Situaciones Binance (live)"]
    for s in (sits.get("situations") or [])[:12]:
        lines.append(f"- [{s.get('level')}] {s.get('text')}")
    fb = feedback_history(vibe, limit=8)
    lines += ["", "## Retroalimentación post-cierre (historial)"]
    if fb:
        for r in fb:
            pnl = r.get("pnl_pct")
            pnl_s = f"{float(pnl)*100:+.2f}%" if isinstance(pnl, (int, float)) else "—"
            lines.append(
                f"- {r.get('ts_cdmx') or r.get('ts')} · {r.get('action')} · "
                f"{r.get('symbol')} {r.get('result')} {pnl_s} · {r.get('title')}"
            )
            if r.get("detail"):
                lines.append(f"  {str(r.get('detail'))[:180]}")
    else:
        lines.append("- (sin recomendaciones aún; solo se escriben cuando un cierre lo justifica)")

    return "\n".join(lines)[:14000]


# Plain-Spanish mode labels for Ops (novice UI) + Hermes digests.
_MODE_ES = {
    "recap": "En pausa (recuperación)",
    "standby": "En espera",
    "defensive": "Defensivo (compras chicas)",
    "v6_primary": "Activo (compras normales)",
    "smart_time": "Tiempo inteligente",
    "canonical_v2": "Canónico paper",
    "scalp": "Scalp corto",
    "scalp15": "Scalp 15 minutos",
    "swing": "Swing",
}


def mode_es(mode: Any) -> str:
    m = str(mode or "").strip()
    if not m or m == "?":
        return "Desconocido"
    return _MODE_ES.get(m, m.replace("_", " "))


def _traffic(halt: bool, mode: str, day_pnl_pct: Any) -> dict[str, str]:
    """green / yellow / red + short Spanish phrase."""
    try:
        day = float(day_pnl_pct or 0)
    except (TypeError, ValueError):
        day = 0.0
    m = str(mode or "")
    if halt:
        return {
            "level": "red",
            "label": "DETENIDO",
            "plain": "El bot está parado (HALT). No compra ni vende.",
        }
    if m in ("recap", "standby"):
        return {
            "level": "yellow",
            "label": "PAUSA",
            "plain": "Está encendido pero no abre compras nuevas ahora.",
        }
    if day <= -3.0:
        return {
            "level": "yellow",
            "label": "CUIDADO",
            "plain": f"Hoy va mal ({day:+.2f}%). Puede limitar compras.",
        }
    if m in ("v6_primary", "defensive", "smart_time", "scalp15", "canonical_v2", "scalp"):
        return {
            "level": "green",
            "label": "ACTIVO",
            "plain": "Puede operar según sus reglas.",
        }
    return {
        "level": "yellow",
        "label": "REVISAR",
        "plain": f"Modo actual: {mode_es(m)}.",
    }


def _systemd_active(unit: str) -> str:
    try:
        import subprocess

        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return (r.stdout or "").strip() or "unknown"
    except Exception:
        return "unknown"


_SERVICE_CATALOG = [
    ("vibe-autotrade", "Binance live (smart-fast-v6)"),
    ("alpaca-paper-scalp15", "Alpaca paper scalp15"),
    ("alpaca-paper-telegram", "Telegram Alpaca"),
    ("vibe-telegram", "Telegram Binance"),
    ("vibe-telegram-control", "Telegram control / Modo Hermes"),
    ("freqtrade-alpaca-paper", "Freqtrade crypto paper"),
    ("freqtrade-alpaca-stocks", "Freqtrade stocks paper"),
    ("vibe-trading", "Vibe API/UI"),
]


def vps_services() -> list[dict[str, Any]]:
    """Best-effort systemd snapshot for Hermes + Ops overview.

    Inside Docker, prefer `/data/vibe/host_services.json` written by the host
    helper `synaptika-host-status-write`. Fall back to local systemctl.
    """
    cached = Path(os.environ.get("VIBE_HOME", "/data/vibe")) / "host_services.json"
    if cached.exists():
        doc = read_json(cached)
        rows = doc.get("services") if isinstance(doc, dict) else None
        if isinstance(rows, list) and rows:
            return rows

    rows = []
    for unit, title in _SERVICE_CATALOG:
        st = _systemd_active(unit)
        rows.append(
            {
                "unit": unit,
                "title": title,
                "active": st,
                "ok": st == "active",
            }
        )
    return rows


def bot_plain_cards(vibe: Path, alpaca: Path) -> list[dict[str, Any]]:
    """One novice-friendly card per trading bot."""
    bn = binance_snapshot(vibe)
    ap = alpaca_snapshot(alpaca)
    s15 = alpaca_scalp15_snapshot(_scalp15_home())
    cards = []
    for key, snap, money_label, money_val, note in [
        (
            "binance",
            bn,
            "Dinero en cuenta (equity)",
            bn.get("equity"),
            "Spot real en Binance · micro cuenta",
        ),
        (
            "alpaca",
            ap,
            "Dinero paper (equity)",
            ap.get("equity"),
            "Alpaca paper · no es dinero real",
        ),
        (
            "alpaca_scalp15",
            s15,
            "Dinero paper scalp15",
            s15.get("equity"),
            "Alpaca paper · operaciones cada ~15 minutos",
        ),
    ]:
        tr = _traffic(bool(snap.get("halt")), str(snap.get("mode") or ""), snap.get("day_pnl_pct"))
        can_buy = tr["level"] == "green" and not snap.get("halt")
        if key == "binance" and str(snap.get("mode") or "") in ("recap", "standby"):
            can_buy = False
        cards.append(
            {
                "id": key,
                "name": {
                    "binance": "Binance (real)",
                    "alpaca": "Alpaca paper",
                    "alpaca_scalp15": "Alpaca scalp15",
                }[key],
                "note": note,
                "money_label": money_label,
                "equity": money_val,
                "day_pnl": snap.get("day_pnl"),
                "day_pnl_pct": snap.get("day_pnl_pct"),
                "mode": snap.get("mode"),
                "mode_es": mode_es(snap.get("mode")),
                "regime": snap.get("regime") or "—",
                "halt": bool(snap.get("halt")),
                "legs": len(snap.get("legs") or []),
                "buys_today": snap.get("buys_today"),
                "can_buy": can_buy,
                "can_buy_es": "Sí, puede comprar" if can_buy else "No compra ahora",
                "traffic": tr,
                "reason": str(snap.get("reason") or snap.get("title") or "")[:200],
                "usable": snap.get("usable") if key == "binance" else snap.get("cash"),
            }
        )
    return cards


def novice_overview(vibe: Path, alpaca: Path) -> dict[str, Any]:
    cards = bot_plain_cards(vibe, alpaca)
    reds = [c for c in cards if c["traffic"]["level"] == "red"]
    yellows = [c for c in cards if c["traffic"]["level"] == "yellow"]
    if reds:
        headline = "Hay bot(s) detenidos (HALT). Revisa la tabla de abajo."
    elif yellows:
        names = ", ".join(c["name"] for c in yellows)
        headline = f"Algunos bots están en pausa o cuidado: {names}."
    else:
        headline = "Los tres bots principales pueden operar según sus reglas."
    return {
        "headline": headline,
        "cards": cards,
        "glossary": [
            {
                "term": "Equity",
                "mean": "Cuánto vale la cuenta ahora (dinero + posiciones).",
            },
            {
                "term": "PnL del día",
                "mean": "Ganancia o pérdida de hoy (desde la apertura del día).",
            },
            {
                "term": "Modo",
                "mean": "Cómo se comporta el bot (activo, pausa, defensivo…).",
            },
            {
                "term": "HALT",
                "mean": "Freno de emergencia: el bot no opera hasta quitarlo.",
            },
            {
                "term": "Posición / pierna",
                "mean": "Una moneda o acción que el bot tiene abierta ahora.",
            },
            {
                "term": "Paper",
                "mean": "Simulación: no usa dinero real.",
            },
            {
                "term": "Win / Loss",
                "mean": "Cierres ganadores / perdedores.",
            },
        ],
    }


def hermes_full_digest(vibe: Path, alpaca: Path) -> str:
    """Plain Spanish digest covering all Ops bots + systemd for Hermes."""
    ov = novice_overview(vibe, alpaca)
    dig = digest_text(vibe, alpaca)
    services = vps_services()
    lines = [
        f"# Synaptika VPS · vista completa · {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}",
        "",
        "## En simple",
        ov["headline"],
        "",
        "## Bots (tabla mental)",
    ]
    for c in ov["cards"]:
        lines.append(
            f"- {c['name']}: equity=${c.get('equity')} · hoy={c.get('day_pnl_pct')}% · "
            f"modo={c['mode_es']} · {c['can_buy_es']} · halt={c['halt']} · legs={c['legs']}"
        )
        if c.get("reason"):
            lines.append(f"  motivo: {c['reason'][:160]}")
    lines += ["", "## Servicios systemd"]
    for s in services:
        mark = "OK" if s.get("ok") else s.get("active")
        lines.append(f"- {s['title']} (`{s['unit']}`): {mark}")
    lines += [
        "",
        "## Rutas",
        "- Binance: /root/.vibe-trading",
        "- Alpaca paper: /root/.alpaca-paper",
        "- Alpaca scalp15: /root/.alpaca-scalp15",
        "- Portal Ops: /root/synaptika-trade",
        "- Hermes: /root/.hermes · tools /opt/hermes-tools",
        "- Freqtrade: /opt/hermes-tools/freqtrade",
        "",
        "## Digest detallado",
        dig,
    ]
    return "\n".join(lines)[:16000]


def full_status(vibe: Path, alpaca: Path) -> dict[str, Any]:
    briefs = strategy_briefs()
    bn = binance_snapshot(vibe)
    ap = alpaca_snapshot(alpaca)
    s15 = alpaca_scalp15_snapshot(_scalp15_home())
    overview = novice_overview(vibe, alpaca)
    return {
        "ts": time.time(),
        "binance": bn,
        "alpaca": ap,
        "alpaca_scalp15": s15,
        "overview": overview,
        "services": vps_services(),
        "strategy": {
            "briefs": briefs,
            "live": {
                "binance_mode": bn.get("mode"),
                "binance_locked": bn.get("locked"),
                "binance_strategy": bn.get("strategy"),
                "binance_active": bn.get("active"),
                "alpaca_mode": ap.get("mode"),
                "alpaca_scalp15": s15.get("mode"),
            },
        },
        "activity": activity(vibe, alpaca, limit=40),
        "win_loss": win_loss_table(vibe, alpaca),
        "situations": live_situations(vibe),
        "feedback": feedback_history(vibe, limit=25),
        "learning": learning_history(vibe, limit=25),
        "knobs_overlay": knobs_overlay_snapshot(vibe),
        "positions": open_positions_table(vibe, alpaca),
        "trades": trade_ledger(vibe, alpaca, limit=40),
        "equity": {
            "binance": equity_series(vibe),
            "alpaca": equity_series(alpaca),
            "alpaca_scalp15": equity_series(_scalp15_home()),
        },
    }
