#!/usr/bin/env python3
"""Canonical knobs for smart-fast-v6 (single source of truth).

Matches PROMPT_V6.md. Change here, not scattered in the loop.
"""

from __future__ import annotations

import os

STRATEGY_TAG = "smart-fast-v6"
VENUE_TAG = "Binance"
PROMPT_VERSION = "v2"

# --- Size / risk ---
ORDER_USD = float(os.environ.get("AUTOTRADE_ORDER_USD", "5.5"))
TP = 0.030
SL = 0.018
TRAIL_ACT = 0.020
TRAIL_GB = 0.010
TIME_STOP_HOURS = 4.0
TIME_MIN_PNL = 0.015
MIN_EXIT_USD = 5.0
MAX_BUYS_PER_DAY = 4
TARGET_BUYS_DAY = 2  # informative only — not a quota
MAX_OPEN_LEGS = 1
MAX_MAJOR_BUYS_DAY = 1
COOLDOWN_HOURS = 4.0
MIN_USDT = 5.0
MIN_BUY_SCORE = 3.50
MIN_BUY_SCORE_BEAR = 4.00
# Micro / need_recharge: lower bar so grace clips can fire
MIN_BUY_SCORE_GRACE = 2.90
DUST_USD = 0.50
# Baseline when equity unknown; prefer day_loss_halt_pct(equity)
DAY_LOSS_HALT_PCT = -3.0
DAY_LOSS_HALT_PCT_SMALL = -5.0
SMALL_EQUITY_USD = 100.0
MIN_EQUITY_RECHARGE = 50.0
# After this many closes, win-rate gate can block new buys
MIN_CLOSES_FOR_WINRATE = 2
MIN_WIN_RATE_CONTINUE = 0.34
# Fee notional / equity
FEE_NOTIONAL_FRAC = 0.55
FEE_NOTIONAL_FRAC_SMALL = 0.80
FEE_GRACE2_HEADROOM = 0.08
# Young leg age before last-resort fund sell may touch it
YOUNG_LEG_SEC = 45 * 60
# Grace: max buys/day while equity < MIN_EQUITY_RECHARGE
GRACE_MAX_BUYS = 1
GRACE_MAX_BUYS_GREEN = 2
# ETH scalper retired — no USDT haircut for a parallel sleeve.
SCALP_USDT_RESERVE = 0.0

# Micro exits (equity < MIN_EQUITY_RECHARGE or grace*)
SL_MICRO = 0.012
TIME_STOP_HOURS_MICRO = 2.5
TIME_MIN_PNL_MICRO = 0.008
EARLY_EXIT_H = 0.75
EARLY_EXIT_PNL = -0.004

# --- Poll cadence (seconds) ---
POLL_OPEN_SEC = 180
POLL_HUNT_SEC = 900
POLL_HUNT_GRACE_SEC = 300
POLL_IDLE_SEC = 900

MAJORS = frozenset({"BTC", "BNB", "ETH"})
FUND_ASSETS = ("ADA", "GALA", "POL", "DOGE", "S", "MANA", "SAND", "XRP", "LINK", "SOL")
UNIVERSE = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "LINKUSDT",
]
STABLES = frozenset({"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USD"})


def day_loss_halt_pct(equity: float | None = None) -> float:
    """Dynamic day-loss: looser for micro accounts."""
    eq = float(equity or 0)
    if eq > 0 and eq < SMALL_EQUITY_USD:
        return DAY_LOSS_HALT_PCT_SMALL
    return DAY_LOSS_HALT_PCT


def fee_notional_limit(equity: float | None = None) -> float:
    """Max buy+sell notional / equity before fee pressure."""
    eq = float(equity or 0)
    if eq > 0 and eq < SMALL_EQUITY_USD:
        return FEE_NOTIONAL_FRAC_SMALL
    return FEE_NOTIONAL_FRAC


def knobs_summary() -> str:
    return (
        f"ORDER={ORDER_USD} TP={TP} SL={SL}/{SL_MICRO} TRAIL={TRAIL_ACT}/{TRAIL_GB} "
        f"TIME={TIME_STOP_HOURS}/{TIME_STOP_HOURS_MICRO}h MAX_BUYS={MAX_BUYS_PER_DAY} "
        f"LEGS={MAX_OPEN_LEGS} SCORE>={MIN_BUY_SCORE} GRACE>={MIN_BUY_SCORE_GRACE} "
        f"DAY_HALT={DAY_LOSS_HALT_PCT}/{DAY_LOSS_HALT_PCT_SMALL}% "
        f"REECHARGE<{MIN_EQUITY_RECHARGE} FEE={FEE_NOTIONAL_FRAC}/{FEE_NOTIONAL_FRAC_SMALL} "
        f"prompt={PROMPT_VERSION} reserve={SCALP_USDT_RESERVE}"
    )


def apply_overlay(path: str | None = None) -> dict:
    """Apply Ops Copiloto runtime knobs from v6_knobs_overlay.json into this module."""
    import json
    from pathlib import Path

    global ORDER_USD, TP, SL, TRAIL_ACT, TRAIL_GB, TIME_STOP_HOURS, TIME_MIN_PNL
    global MAX_BUYS_PER_DAY, MAX_OPEN_LEGS, MIN_BUY_SCORE, MIN_BUY_SCORE_BEAR
    global MIN_BUY_SCORE_GRACE, DAY_LOSS_HALT_PCT, COOLDOWN_HOURS
    global SL_MICRO, TIME_STOP_HOURS_MICRO, TIME_MIN_PNL_MICRO
    global EARLY_EXIT_H, EARLY_EXIT_PNL, GRACE_MAX_BUYS, GRACE_MAX_BUYS_GREEN

    p = Path(path or os.environ.get("VIBE_TRADING_HOME", "/root/.vibe-trading")) / "v6_knobs_overlay.json"
    if not p.exists():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    knobs = dict(doc.get("knobs") or {})
    mapping = {
        "ORDER_USD": "ORDER_USD",
        "TP": "TP",
        "SL": "SL",
        "TRAIL_ACT": "TRAIL_ACT",
        "TRAIL_GB": "TRAIL_GB",
        "TIME_STOP_HOURS": "TIME_STOP_HOURS",
        "TIME_MIN_PNL": "TIME_MIN_PNL",
        "MAX_BUYS_PER_DAY": "MAX_BUYS_PER_DAY",
        "MAX_OPEN_LEGS": "MAX_OPEN_LEGS",
        "MIN_BUY_SCORE": "MIN_BUY_SCORE",
        "MIN_BUY_SCORE_BEAR": "MIN_BUY_SCORE_BEAR",
        "MIN_BUY_SCORE_GRACE": "MIN_BUY_SCORE_GRACE",
        "DAY_LOSS_HALT_PCT": "DAY_LOSS_HALT_PCT",
        "COOLDOWN_HOURS": "COOLDOWN_HOURS",
        "SL_MICRO": "SL_MICRO",
        "TIME_STOP_HOURS_MICRO": "TIME_STOP_HOURS_MICRO",
        "TIME_MIN_PNL_MICRO": "TIME_MIN_PNL_MICRO",
        "EARLY_EXIT_H": "EARLY_EXIT_H",
        "EARLY_EXIT_PNL": "EARLY_EXIT_PNL",
        "GRACE_MAX_BUYS": "GRACE_MAX_BUYS",
        "GRACE_MAX_BUYS_GREEN": "GRACE_MAX_BUYS_GREEN",
    }
    g = globals()
    applied = {}
    for src, dst in mapping.items():
        if src in knobs:
            try:
                g[dst] = type(g[dst])(knobs[src])
                applied[dst] = g[dst]
            except (TypeError, ValueError):
                continue
    return applied
