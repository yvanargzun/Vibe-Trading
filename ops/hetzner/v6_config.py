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
MIN_EXIT_USD = 5.0
MAX_BUYS_PER_DAY = 2
MAX_OPEN_LEGS = 1
MAX_MAJOR_BUYS_DAY = 1
COOLDOWN_HOURS = 4.0
MIN_USDT = 5.0
MIN_BUY_SCORE = 3.50
MIN_BUY_SCORE_BEAR = 4.00
DUST_USD = 0.50
DAY_LOSS_HALT_PCT = -3.0
# ETH scalper retired — no USDT haircut for a parallel sleeve.
SCALP_USDT_RESERVE = 0.0

# --- Poll cadence (seconds) ---
POLL_OPEN_SEC = 180
POLL_HUNT_SEC = 900
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


def knobs_summary() -> str:
    return (
        f"ORDER={ORDER_USD} TP={TP} SL={SL} TRAIL={TRAIL_ACT}/{TRAIL_GB} "
        f"TIME={TIME_STOP_HOURS}h MAX_BUYS={MAX_BUYS_PER_DAY} LEGS={MAX_OPEN_LEGS} "
        f"SCORE>={MIN_BUY_SCORE} BEAR>={MIN_BUY_SCORE_BEAR} "
        f"DAY_HALT={DAY_LOSS_HALT_PCT}% prompt={PROMPT_VERSION} "
        f"reserve={SCALP_USDT_RESERVE}"
    )
