#!/usr/bin/env python3
"""Rule-based market orchestrator for smart-fast-v6 (no LLM).

ETH scalper retired — modes no longer route to scalp_primary.
"""

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

# scalp_primary kept only for migration off disk state
MODES = ("recap", "standby", "defensive", "v6_primary", "scalp_primary")

# --- timings (agent-owned) ---
MIN_HOLD_SEC = 45 * 60
STANDBY_HOLD_SEC = 90 * 60
RECAP_HOLD_SEC = 120 * 60
FLIP_COOLDOWN_SEC = 20 * 60
MAX_FLIPS_DAY = 4
MODE_TG_COOLDOWN_SEC = 6 * 3600

# --- thresholds (capital floors; day-loss/fee from v6_config helpers) ---
RECAP_USDT = 4.5
STANDBY_BTC_1H = -2.5
V6_USDT = 5.5
# Legacy constant — prefer v6_config.fee_notional_limit(equity)
FEE_NOTIONAL_FRAC = 0.55


def _day_loss_thr(equity: float) -> float:
    try:
        import v6_config as v6c

        return float(v6c.day_loss_halt_pct(equity))
    except Exception:
        return -3.0


def _fee_limit(equity: float) -> float:
    try:
        import v6_config as v6c

        return float(v6c.fee_notional_limit(equity))
    except Exception:
        return FEE_NOTIONAL_FRAC


def _min_usdt() -> float:
    try:
        import v6_config as v6c

        return float(v6c.MIN_USDT)
    except Exception:
        return 5.0


def _min_equity_recharge() -> float:
    try:
        import v6_config as v6c

        return float(v6c.MIN_EQUITY_RECHARGE)
    except Exception:
        return 50.0

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
    """ETH scalper retired — never allow new scalper entries."""
    return False


def v6_exits_only(mode: str | None = None) -> bool:
    m = mode or current_mode()
    # scalp_primary treated as exits-only until migrated away
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
    """Approx USDT v6 can use: free Spot after salvage (no scalp haircut)."""
    usdt = float(eth_m.get("eth_usdt") or 0)
    try:
        import binance_wallets as bw

        bw.salvage_usdt_to_spot(force=False)
        usdt = max(usdt, float(bw.free_spot_usdt()))
    except Exception:
        pass
    return max(0.0, usdt)


def propose_mode(features: dict[str, Any]) -> tuple[str, str]:
    usdt = float(features["usable_usdt"])
    day_pnl = float(features["day_pnl_pct"])
    btc_1h = float(features["btc_chg1h"])
    losses = int(features["loss_streak"])
    notional_frac = float(features["notional_frac"])
    btc_reg = str(features["btc_regime"])
    equity = float(features.get("equity") or 0)
    day_thr = float(features.get("day_loss_thr") or _day_loss_thr(equity))
    fee_lim = float(features.get("fee_limit") or _fee_limit(equity))
    min_u = _min_usdt()
    recharge = _min_equity_recharge()

    # Win-rate tilt brake (exits-only style via orch mode)
    if features.get("day_edge_fail"):
        return "recap", "day_edge_fail win_rate_below_min"

    # Micro book: explicit recharge; one defensive grace clip if dry powder exists
    if equity > 0 and equity < recharge:
        if usdt >= min_u:
            return (
                "defensive",
                f"need_recharge equity={equity:.2f}<{recharge:.0f} grace_1clip usdt={usdt:.2f}",
            )
        return (
            "recap",
            f"need_recharge equity={equity:.2f}<{recharge:.0f} usdt={usdt:.2f}",
        )

    # Capital thin — no ETH AND (scalper retired)
    if usdt < RECAP_USDT:
        return "recap", f"capital bajo usdt={usdt:.2f}"

    if day_pnl <= day_thr:
        return "standby", f"day_pnl={day_pnl:.2f}% thr={day_thr:.1f}%"
    if losses >= 3:
        return "standby", f"loss_streak={losses}"
    if btc_1h <= STANDBY_BTC_1H:
        return "standby", f"btc_1h={btc_1h:.2f}%"

    # Fee soft: don't freeze forever — allow one defensive clip if powder exists
    if notional_frac >= fee_lim:
        if usdt >= min_u and day_pnl > day_thr:
            return (
                "defensive",
                f"fee_budget_soft notional_frac={notional_frac:.2f}>={fee_lim:.2f} allow_one_clip",
            )
        return "standby", f"fee_budget notional_frac={notional_frac:.2f}"

    # BTC + dry powder decides aggression (never v6_primary under recharge — handled above)
    if btc_reg in ("bull", "trend") and usdt >= V6_USDT:
        return "v6_primary", f"btc={btc_reg} usdt={usdt:.2f}"
    if usdt >= V6_USDT:
        return "defensive", f"default btc={btc_reg} usdt={usdt:.2f}"
    return "defensive", f"thin usdt={usdt:.2f} btc={btc_reg}"


def _min_hold_for(mode: str, *, reason: str = "") -> float:
    if mode == "standby":
        if "fee_budget" in (reason or ""):
            return 15 * 60  # shorter trap for fee-only standbys
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

    # One-shot migration off retired scalp_primary
    if str(doc.get("mode") or "") == "scalp_primary":
        doc["mode"] = "defensive"
        doc["reason"] = "migrate_retire_scalper"
        doc["since_ts"] = now
        print("ORCH_MIGRATE scalp_primary->defensive", flush=True)

    btc = _btc_metrics()
    eth = _eth_metrics()
    day_pnl, eq, day_open = _book_day_pnl_pct()

    # Persist capital-inject rebase so orch + loop share the same day_open
    try:
        st = _load_json(AUTOTRADE_STATE)
        g = dict(st.get("goals") or {})
        open_eq = float(g.get("day_open_equity") or 0)
        if eq > 0 and open_eq > 0 and eq >= open_eq * 1.12:
            g["day_open_equity"] = round(eq, 4)
            g["capital_rebase_ts"] = now
            st["goals"] = g
            st["equity"] = round(eq, 4)
            AUTOTRADE_STATE.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
            day_open = eq
            day_pnl = 0.0
            print(f"ORCH_DAY_OPEN_REBASE {open_eq:.4f}->{eq:.4f}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ORCH_REBASE_FAIL {exc}", flush=True)

    usable = _usable_usdt(eth)
    notional = te.notional_traded_today()
    # Use live equity when larger so capital injects don't freeze fee_budget
    denom = max(float(day_open or 0), float(eq or 0), 1.0)
    notional_frac = notional / denom
    losses = te.consecutive_losses()
    day_thr = _day_loss_thr(eq)
    fee_lim = _fee_limit(eq)

    wr, wins, losses_d, rated = te.win_rate_today(bot="v6")
    day_edge_fail = False
    try:
        import v6_config as v6c

        min_closes = int(v6c.MIN_CLOSES_FOR_WINRATE)
        min_wr = float(v6c.MIN_WIN_RATE_CONTINUE)
    except Exception:
        min_closes, min_wr = 2, 0.34
    if rated >= min_closes and wr is not None and wr < min_wr:
        day_edge_fail = True

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
        "eth_dead_sec": 0.0,
        "day_loss_thr": day_thr,
        "fee_limit": fee_lim,
        "day_edge_fail": day_edge_fail,
        "win_rate_today": wr,
        "closes_rated_today": rated,
        "wins_today": wins,
        "losses_today": losses_d,
    }
    target, reason = propose_mode(features)
    cur = str(doc.get("mode") or "defensive")
    since = float(doc.get("since_ts") or now)
    held = now - since
    last_flip = float(doc.get("last_flip_ts") or 0)
    flips = int(doc.get("flips_today") or 0)
    prev_reason = str(doc.get("reason") or "")

    soft_fee_ok = (
        usable >= _min_usdt()
        and day_pnl > day_thr
        and losses < 3
        and float(features.get("btc_chg1h") or 0) > STANDBY_BTC_1H
    )
    # Capital / soft-fee recovered: allow leaving standby even after max flips
    recovery_ok = (
        usable >= V6_USDT
        and notional_frac < fee_lim
        and day_pnl > day_thr
        and losses < 3
        and float(features.get("btc_chg1h") or 0) > STANDBY_BTC_1H
    ) or (
        soft_fee_ok
        and ("fee_budget" in prev_reason or "fee_budget" in reason)
        and target in ("defensive", "v6_primary")
    )

    # hard cap flips — do not trap forever after inject/fee soft recovery
    if flips >= MAX_FLIPS_DAY and target != cur:
        if cur == "standby" and target in ("defensive", "v6_primary") and recovery_ok:
            pass
        elif target != "standby":
            target, reason = "standby", f"max_flips={flips} force_standby"
            if cur == "standby":
                target = cur
                reason = doc.get("reason") or reason

    allow = False
    if target == cur:
        allow = False  # no change
    elif _is_emergency(target):
        allow = True
    elif held >= _min_hold_for(cur, reason=prev_reason) and (now - last_flip) >= FLIP_COOLDOWN_SEC:
        allow = True
    elif cur in ("standby", "recap") and target in ("defensive", "v6_primary"):
        # Exit idle modes early when dry powder / soft-fee recovered
        if recovery_ok and held >= 5 * 60:
            allow = True
        elif cur == "recap" and usable >= _min_usdt():
            allow = held >= 30 * 60
        elif "fee_budget" in prev_reason and soft_fee_ok and held >= 5 * 60:
            allow = True
        elif held >= _min_hold_for(cur, reason=prev_reason):
            allow = True

    changed = False
    if allow and target != cur:
        # recovery flip does not burn another flip slot into permanent standby
        bump = 0 if (cur == "standby" and recovery_ok and flips >= MAX_FLIPS_DAY) else 1
        doc["mode"] = target
        doc["since_ts"] = now
        doc["last_flip_ts"] = now
        doc["flips_today"] = flips + bump
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

    # Persist situations snapshot for Ops
    try:
        import strategy_feedback as sf

        last_skip = None
        try:
            skip_path = HOME / "skip_events.jsonl"
            if skip_path.exists():
                for line in skip_path.read_text(encoding="utf-8").splitlines()[-8:]:
                    try:
                        sj = json.loads(line)
                        if sj.get("bot") in ("v6", None, "binance"):
                            last_skip = f"{sj.get('reason')}: {sj.get('detail') or ''}"
                    except Exception:
                        pass
        except Exception:
            pass
        st_doc = _load_json(AUTOTRADE_STATE)
        st_buys = int(st_doc.get("buys_today") or 0)
        open_legs = sum(
            1
            for _a, meta in (st_doc.get("positions") or {}).items()
            if float((meta or {}).get("usd") or 0) >= 0.5
        )
        sits = sf.build_situations(
            mode=str(doc.get("mode") or ""),
            reason=str(doc.get("reason") or ""),
            features=features,
            equity=eq,
            usable=usable,
            buys_today=st_buys,
            open_legs=open_legs,
            last_skip=last_skip,
        )
        sf.persist_situations(
            sits,
            extra={"mode": doc.get("mode"), "reason": doc.get("reason"), "equity": eq},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"SITUATIONS_FAIL {exc}", flush=True)

    print(
        f"ORCH mode={doc['mode']} want={target} held={held/60:.1f}m "
        f"flips={doc['flips_today']} reason={doc['reason'][:80]}",
        flush=True,
    )
    return doc


def _maybe_tg_mode(doc: dict, mode: str, reason: str, eq: float) -> None:
    now = time.time()
    last = float(doc.get("last_mode_tg_ts") or 0)
    # Recharge notices: allow sooner (2h)
    cooldown = MODE_TG_COOLDOWN_SEC
    if "need_recharge" in reason:
        cooldown = 2 * 3600
    if now - last < cooldown:
        return
    try:
        from telegram_notify_prefs import tg_api, load_env, filter_keyboard

        chat = load_env().get("TELEGRAM_CHAT_ID")
        if not chat:
            return
        extra = ""
        if "need_recharge" in reason:
            extra = "\nDeposita USDT hasta equity ≥ $50 para salir de modo recarga."
        tg_api(
            "sendMessage",
            {
                "chat_id": chat,
                "text": (
                    f"[Orquestador] Modo → {mode}\n"
                    f"Por que: {reason}\n"
                    f"Equity libro ~ ${eq:.2f}"
                    f"{extra}\n"
                    "Gates activos; sin IA."
                ),
                "reply_markup": filter_keyboard(),
            },
        )
        doc["last_mode_tg_ts"] = now
    except Exception as exc:  # noqa: BLE001
        print(f"ORCH_TG_FAIL {exc}", flush=True)
