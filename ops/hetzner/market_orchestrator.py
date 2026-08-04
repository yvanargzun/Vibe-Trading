#!/usr/bin/env python3
"""Rule-based market orchestrator for v6 + ETH scalper (no LLM)."""

from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path("/root/.vibe-trading")
MODE_PATH = HOME / "strategy_mode.json"
SCALP_STATE = HOME / "eth_scalp_state.json"
AUTOTRADE_STATE = HOME / "autotrade_state.json"

MODES = ("recap", "standby", "defensive", "v6_primary", "scalp_primary")

# --- timings (agent-owned) ---
MIN_HOLD_SEC = 45 * 60
STANDBY_HOLD_SEC = 90 * 60
RECAP_HOLD_SEC = 120 * 60
FLIP_COOLDOWN_SEC = 20 * 60
MAX_FLIPS_DAY = 4
MODE_TG_COOLDOWN_SEC = 6 * 3600

# --- thresholds ---
RECAP_USDT = 4.5
RECAP_ETH_USD = 5.0
STANDBY_DAY_PNL_PCT = -3.0
STANDBY_BTC_1H = -2.5
V6_USDT = 5.5
SCALP_SCORE = 2.8
SCALP_BTC_1H_FLOOR = -1.5
ETH_DEAD_NEED_SEC = 20 * 60
FEE_NOTIONAL_FRAC = 0.40


def _http_get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_mode(doc: dict) -> None:
    MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODE_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def default_mode_doc() -> dict:
    now = time.time()
    return {
        "mode": "defensive",
        "since_ts": now,
        "flips_today": 0,
        "flips_day": datetime.now(timezone.utc).date().isoformat(),
        "reason": "init",
        "features": {},
        "last_flip_ts": 0,
        "last_mode_tg_ts": 0,
        "eth_dead_since": None,
    }


def load_mode() -> dict:
    doc = _load_json(MODE_PATH)
    if not doc.get("mode"):
        doc = default_mode_doc()
        _save_mode(doc)
    return doc


def current_mode() -> str:
    return str(load_mode().get("mode") or "defensive")


def allows_v6_buys(mode: str | None = None) -> bool:
    m = mode or current_mode()
    return m in ("defensive", "v6_primary")


def allows_scalper_entries(mode: str | None = None) -> bool:
    m = mode or current_mode()
    return m in ("defensive", "scalp_primary")


def v6_exits_only(mode: str | None = None) -> bool:
    m = mode or current_mode()
    return m in ("standby", "recap", "scalp_primary")


def _btc_metrics() -> dict[str, float | str]:
    try:
        t = _http_get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT")
        chg24 = float(t["priceChangePercent"])
    except Exception:
        chg24 = 0.0
    try:
        kl = _http_get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=3")
        c0 = float(kl[-2][4])
        c1 = float(kl[-1][4])
        chg1h = (c1 / c0 - 1.0) * 100.0 if c0 else 0.0
    except Exception:
        chg1h = 0.0
    regime = "bull" if chg24 >= 0.4 else ("bear" if chg24 <= -0.8 else ("trend" if chg24 >= 0 else "chop"))
    return {"btc_chg24": chg24, "btc_chg1h": chg1h, "btc_regime": regime}


def _eth_metrics() -> dict[str, Any]:
    st = _load_json(SCALP_STATE)
    regime = str(st.get("last_regime") or "dead")
    eth_free = 0.0
    usdt = 0.0
    px = 0.0
    try:
        import sys

        sys.path.insert(0, "/opt/vibe-trade/agent")
        from src.trading.connectors.binance import sdk as bn

        cfg = bn.load_config()
        ex = bn._exchange(cfg)
        bal = ex.fetch_balance()
        eth_free = float((bal.get("free") or {}).get("ETH") or 0)
        usdt = float((bal.get("free") or {}).get("USDT") or 0)
        px = float(_http_get("https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT")["price"])
    except Exception:
        # Fallback: portfolio snap (monitor) — never invent bankroll
        snap = _load_json(HOME / "telegram_portfolio_snap.json")
        held = snap.get("held") or {}
        usdt = float(held.get("USDT") or 0)
        eth_free = float(held.get("ETH") or 0)
        try:
            px = float(_http_get("https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT")["price"])
        except Exception:
            px = 0.0
    eth_usd = eth_free * px
    has_pos = bool(st.get("position"))
    active = bool(st.get("active_float"))
    score = 1.0
    if regime == "trend":
        score = 3.2
    elif regime == "range":
        score = 2.9
    return {
        "eth_regime": regime,
        "eth_usd": eth_usd,
        "eth_usdt": usdt,
        "eth_score": score,
        "eth_has_pos": has_pos,
        "eth_active_float": active,
    }


def _book_day_pnl_pct() -> tuple[float, float, float]:
    """Returns day_pnl_pct, equity, day_open — prefer live book, not stale snap."""
    st = _load_json(AUTOTRADE_STATE)
    g = st.get("goals") or {}
    day_open = float(g.get("day_open_equity") or 0)
    eq = float(st.get("equity") or 0)

    # Live multi-wallet equity (Spot+Funding+Futures idle)
    try:
        import binance_wallets as bw

        live = float(bw.total_book_equity())
        if live > 0:
            eq = live
    except Exception:
        pass

    # Snap only if fresh (<12 min) and within 12% of live/state (anti-cliff)
    try:
        snap = _load_json(HOME / "telegram_portfolio_snap.json")
        snap_eq = float(snap.get("total") or 0)
        snap_ts = float(snap.get("ts") or 0)
        age = time.time() - snap_ts if snap_ts else 1e9
        if snap_eq > 0 and age < 12 * 60:
            base = eq if eq > 0 else snap_eq
            if base > 0 and abs(snap_eq - base) / base <= 0.12:
                eq = snap_eq
    except Exception:
        pass

    if day_open <= 0:
        day_open = eq if eq > 0 else 1.0
    pnl_pct = ((eq - day_open) / day_open * 100.0) if day_open > 0 else 0.0
    return pnl_pct, eq, day_open


def _usable_usdt(eth_m: dict) -> float:
    """Approx USDT v6 can use: free Spot after salvage, minus scalp reserve."""
    usdt = float(eth_m.get("eth_usdt") or 0)
    try:
        import binance_wallets as bw

        bw.salvage_usdt_to_spot(force=False)
        usdt = max(usdt, float(bw.free_spot_usdt()))
    except Exception:
        pass
    if eth_m.get("eth_has_pos") or eth_m.get("eth_active_float"):
        return max(0.0, usdt - 5.0)
    return usdt


def propose_mode(features: dict[str, Any]) -> tuple[str, str]:
    usdt = float(features["usable_usdt"])
    eth_usd = float(features["eth_usd"])
    day_pnl = float(features["day_pnl_pct"])
    btc_1h = float(features["btc_chg1h"])
    losses = int(features["loss_streak"])
    notional_frac = float(features["notional_frac"])
    eth_reg = str(features["eth_regime"])
    eth_dead_s = float(features.get("eth_dead_sec") or 0)
    btc_reg = str(features["btc_regime"])
    eth_score = float(features["eth_score"])

    if usdt < RECAP_USDT and eth_usd < RECAP_ETH_USD and not features.get("eth_has_pos"):
        return "recap", f"capital bajo usdt={usdt:.2f} eth_usd={eth_usd:.2f}"
    if day_pnl <= STANDBY_DAY_PNL_PCT:
        return "standby", f"day_pnl={day_pnl:.2f}%"
    if losses >= 3:
        return "standby", f"loss_streak={losses}"
    if btc_1h <= STANDBY_BTC_1H:
        return "standby", f"btc_1h={btc_1h:.2f}%"
    if notional_frac >= FEE_NOTIONAL_FRAC:
        return "standby", f"fee_budget notional_frac={notional_frac:.2f}"

    scalp_ok = (
        eth_reg in ("trend", "range")
        and eth_score >= SCALP_SCORE
        and (eth_usd >= 5.0 or usdt >= 5.0 or features.get("eth_has_pos"))
        and btc_1h > SCALP_BTC_1H_FLOOR
    )
    v6_ok = btc_reg in ("bull", "trend") and eth_reg == "dead" and eth_dead_s >= ETH_DEAD_NEED_SEC and usdt >= V6_USDT

    if scalp_ok and not v6_ok:
        return "scalp_primary", f"eth={eth_reg} score={eth_score:.2f}"
    if v6_ok and not scalp_ok:
        return "v6_primary", f"btc={btc_reg} eth_dead={eth_dead_s:.0f}s"
    if scalp_ok and v6_ok:
        # prefer defensive shared when both look alive
        return "defensive", "both_themes_active"
    return "defensive", f"default btc={btc_reg} eth={eth_reg}"


def _min_hold_for(mode: str) -> float:
    if mode == "standby":
        return STANDBY_HOLD_SEC
    if mode == "recap":
        return RECAP_HOLD_SEC
    return MIN_HOLD_SEC


def _is_emergency(target: str) -> bool:
    return target in ("standby", "recap")


def evaluate_and_update(*, notify: bool = True) -> dict:
    """Refresh strategy mode with hysteresis. Call once per bot tick."""
    import trade_events as te

    doc = load_mode()
    now = time.time()
    day = datetime.now(timezone.utc).date().isoformat()
    if doc.get("flips_day") != day:
        doc["flips_day"] = day
        doc["flips_today"] = 0

    btc = _btc_metrics()
    eth = _eth_metrics()
    day_pnl, eq, day_open = _book_day_pnl_pct()
    usable = _usable_usdt(eth)
    notional = te.notional_traded_today()
    notional_frac = (notional / day_open) if day_open > 0 else 0.0
    losses = te.consecutive_losses()

    # track eth dead duration
    if eth["eth_regime"] == "dead":
        if not doc.get("eth_dead_since"):
            doc["eth_dead_since"] = now
    else:
        doc["eth_dead_since"] = None
    eth_dead_sec = (now - float(doc["eth_dead_since"])) if doc.get("eth_dead_since") else 0.0

    features = {
        **btc,
        **eth,
        "usable_usdt": usable,
        "day_pnl_pct": day_pnl,
        "equity": eq,
        "day_open": day_open,
        "notional_today": notional,
        "notional_frac": notional_frac,
        "loss_streak": losses,
        "eth_dead_sec": eth_dead_sec,
    }
    target, reason = propose_mode(features)
    cur = str(doc.get("mode") or "defensive")
    since = float(doc.get("since_ts") or now)
    held = now - since
    last_flip = float(doc.get("last_flip_ts") or 0)
    flips = int(doc.get("flips_today") or 0)

    # hard cap flips
    if flips >= MAX_FLIPS_DAY and target != cur:
        if target != "standby":
            target, reason = "standby", f"max_flips={flips} force_standby"
        # allow only emergency standby once
        if cur == "standby":
            target = cur
            reason = doc.get("reason") or reason

    allow = False
    if target == cur:
        allow = False  # no change
    elif _is_emergency(target):
        allow = True
    elif held >= _min_hold_for(cur) and (now - last_flip) >= FLIP_COOLDOWN_SEC:
        allow = True
    elif cur in ("standby", "recap") and target == "defensive":
        # exit recap early if capital recovered
        if cur == "recap" and usable >= 5.0:
            allow = held >= 30 * 60
        elif held >= _min_hold_for(cur):
            allow = True

    changed = False
    if allow and target != cur:
        doc["mode"] = target
        doc["since_ts"] = now
        doc["last_flip_ts"] = now
        doc["flips_today"] = flips + 1
        doc["reason"] = reason
        changed = True
        te.record_mode_change(target, reason=reason, equity=eq)
        print(f"ORCH_FLIP {cur}->{target} {reason}", flush=True)
        if notify:
            _maybe_tg_mode(doc, target, reason, eq)
    else:
        doc["reason"] = reason if target == cur else f"hold_{cur}_want_{target}:{reason}"

    doc["features"] = features
    doc["updated_ts"] = now
    _save_mode(doc)
    print(
        f"ORCH mode={doc['mode']} want={target} held={held/60:.1f}m "
        f"flips={doc['flips_today']} reason={doc['reason'][:80]}",
        flush=True,
    )
    return doc


def _maybe_tg_mode(doc: dict, mode: str, reason: str, eq: float) -> None:
    now = time.time()
    last = float(doc.get("last_mode_tg_ts") or 0)
    if now - last < MODE_TG_COOLDOWN_SEC:
        return
    try:
        from telegram_notify_prefs import tg_api, load_env, filter_keyboard

        chat = load_env().get("TELEGRAM_CHAT_ID")
        if not chat:
            return
        tg_api(
            "sendMessage",
            {
                "chat_id": chat,
                "text": (
                    f"[Orquestador] Modo → {mode}\n"
                    f"Por que: {reason}\n"
                    f"Equity libro ~ ${eq:.2f}\n"
                    "Gates activos; sin IA."
                ),
                "reply_markup": filter_keyboard(),
            },
        )
        doc["last_mode_tg_ts"] = now
    except Exception as exc:  # noqa: BLE001
        print(f"ORCH_TG_FAIL {exc}", flush=True)
