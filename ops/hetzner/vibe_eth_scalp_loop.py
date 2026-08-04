#!/usr/bin/env python3
"""Binance ETH hybrid scalper (parallel to smart-fast-v6).

Regime: dead / trend→momentum / range→mean-reversion.
Futures ≤3x only on strong trend when API allows; otherwise Spot.
Capital: 100% ETH book (redeem Earn → convert as needed).
Alerts: same equity-over-time PNG as Binance/Alpaca [ETH scalping].
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AGENT = Path("/opt/vibe-trade/agent")
sys.path.insert(0, str(AGENT))

HOME = Path(os.environ.get("VIBE_TRADING_HOME", "/root/.vibe-trading"))
ENV_PATH = HOME / ".env"
STATE_PATH = HOME / "eth_scalp_state.json"
LOG_PATH = HOME / "eth_scalp_loop.log"
ORDER_LOCK_PATH = HOME / "order.lock"
CHART_DIR = HOME / "eth_scalp_charts"
EQUITY_HISTORY_PATH = HOME / "eth_scalp_equity_history.json"
EQUITY_CHART_PATH = HOME / "eth_scalp_equity_chart.png"

HEARTBEAT_SEC = 6 * 3600
ERR_TG_COOLDOWN_SEC = 30 * 60
_last_err_tg_ts = 0.0


def maybe_tg_error(msg: str) -> None:
    """At most one error Telegram every 30 minutes."""
    global _last_err_tg_ts
    now = time.time()
    if now - _last_err_tg_ts < ERR_TG_COOLDOWN_SEC:
        log(f"TG_ERR_SUPPRESSED {msg[:80]}")
        return
    _last_err_tg_ts = now
    try:
        tg_alert(format_guard("AVISO · error scalper", msg[:160], eth_book_equity()))
    except Exception:
        pass


def maybe_startup_tg(st: dict) -> None:
    """Announce start at most once per UTC day."""
    day = utc_day()
    if st.get("startup_tg_day") == day:
        return
    eq = eth_book_equity(st)
    tg_alert(
        format_guard(
            "Scalper ETH encendido",
            "Modo hibrido (trend/range) en paralelo a smart-fast-v6. Solo mueve Ethereum.",
            eq,
        )
    )
    st["startup_tg_day"] = day
    save_state(st)


def maybe_heartbeat(st: dict) -> None:
    """If no fill for 6h, send a short alive ping (max once per 6h)."""
    now = time.time()
    last_fill = float(st.get("last_fill_ts") or 0)
    last_hb = float(st.get("last_heartbeat_ts") or 0)
    # Seed last_fill on first run so we don't fire immediately
    if last_fill <= 0:
        st["last_fill_ts"] = now
        save_state(st)
        return
    if now - last_fill < HEARTBEAT_SEC:
        return
    if now - last_hb < HEARTBEAT_SEC:
        return
    eq = eth_book_equity(st)
    hours = (now - last_fill) / 3600.0
    tg_alert(
        format_guard(
            "Scalper ETH activo",
            f"Sigo cazando. Regimen: {st.get('last_regime') or '?'}. "
            f"Fills hoy: {st.get('fills_today') or 0}. Sin fill hace ~{hours:.1f}h (mercado quieto o filtrado).",
            eq,
        )
    )
    st["last_heartbeat_ts"] = now
    save_state(st)


STRATEGY_TAG = "eth-scalp-hybrid-v1"
PAIR = "ETH/USDT"
SYMBOL = "ETHUSDT"
ASSET = "ETH"

MIN_NOTIONAL = 5.0
KILL_DAY_PCT = -0.08
STREAK_LOSS_LIMIT = 3
STREAK_PAUSE_SEC = 25 * 60
POLL_ACTIVE = 10
POLL_STANDBY = 25
TIME_STOP_MOM_SEC = 8 * 60
TIME_STOP_FADE_SEC = 6 * 60
MIN_TIME_EXIT_SEC = 5 * 60  # never TIME-exit earlier
MAX_ROUNDTRIPS = 30
FUTURES_MAX_LEV = 3
FUTURES_MIN_NOTIONAL = 20.0
HIGH_SCORE = 3.2
REPATRIATE_COOLDOWN_SEC = 2 * 3600
REPATRIATE_MIN_USDT = 5.0

STABLES = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USD"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_day() -> str:
    return utc_now().date().isoformat()


def utc_ts() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    print(line, end="", flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


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


def http_get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def default_state() -> dict[str, Any]:
    return {
        "strategy": STRATEGY_TAG,
        "day": utc_day(),
        "day_open_equity": None,
        "bankroll_usdt": None,
        "realized_pnl_today": 0.0,
        "last_equity": None,
        "killed": False,
        "loss_streak": 0,
        "pause_until": None,
        "roundtrips_today": 0,
        "fills_today": 0,
        "position": None,
        "active_float": False,
        "reserved_usdt": 0.0,
        "futures_enabled": False,
        "spot_only_reason": None,
        "last_regime": "dead",
    }


def load_state() -> dict[str, Any]:
    st = default_state()
    if STATE_PATH.exists():
        try:
            st.update(json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass
    if st.get("day") != utc_day():
        eq = eth_book_equity(st)
        st["day"] = utc_day()
        st["day_open_equity"] = eq
        st["realized_pnl_today"] = 0.0
        st["killed"] = False
        st["loss_streak"] = 0
        st["pause_until"] = None
        st["roundtrips_today"] = 0
        st["fills_today"] = 0
    if st.get("day_open_equity") is None:
        st["day_open_equity"] = eth_book_equity(st)
    if st.get("bankroll_usdt") is None:
        # Seed bankroll from honest scalper equity (never whole-wallet ETH).
        st["bankroll_usdt"] = float(st.get("day_open_equity") or 0)
    return st


def save_state(st: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")


def with_order_lock(timeout: float = 45.0):
    import contextlib

    @contextlib.contextmanager
    def _cm():
        ORDER_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + timeout
        while True:
            try:
                fd = os.open(str(ORDER_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                break
            except FileExistsError:
                if time.time() >= deadline:
                    try:
                        age = time.time() - ORDER_LOCK_PATH.stat().st_mtime
                        if age > 120:
                            ORDER_LOCK_PATH.unlink(missing_ok=True)
                            continue
                    except OSError:
                        pass
                    raise TimeoutError("order.lock busy")
                time.sleep(0.25)
        try:
            yield
        finally:
            try:
                ORDER_LOCK_PATH.unlink(missing_ok=True)
            except OSError:
                pass

    return _cm()


_ex_cached = None
_markets_loaded = False


def bn_ex():
    """Return (sdk, cfg, exchange) with markets loaded (fixes 'markets not loaded')."""
    global _ex_cached, _markets_loaded
    from src.trading.connectors.binance import sdk as bn

    cfg = bn.load_config()
    if _ex_cached is None:
        _ex_cached = bn._exchange(cfg)
    ex = _ex_cached
    if not _markets_loaded or not getattr(ex, "markets", None):
        ex.load_markets()
        _markets_loaded = True
    return bn, cfg, ex


def eth_price() -> float:
    return float(http_get(f"https://api.binance.com/api/v3/ticker/price?symbol={SYMBOL}")["price"])


def free_asset(asset: str) -> float:
    _, _, ex = bn_ex()
    bal = ex.fetch_balance()
    return float((bal.get("free") or {}).get(asset) or 0)


def eth_book_equity(st: dict | None = None) -> float:
    """Capital real del ETH scalper (sin bankroll/day_open fantasmas)."""
    if st is None:
        st = {}
        if STATE_PATH.exists():
            try:
                st = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                st = {}

    px = eth_price()
    pos = st.get("position") or {}
    total = 0.0

    if isinstance(pos, dict) and pos.get("side"):
        qty = float(pos.get("qty") or 0)
        usd = float(pos.get("usd") or 0)
        if qty > 0:
            total += qty * px
        elif usd > 0:
            entry = float(pos.get("entry") or px)
            if entry > 0 and pos.get("side") == "buy":
                total += usd * (px / entry)
            else:
                total += usd
        if st.get("active_float"):
            total += free_asset("USDT")
        else:
            total += float(st.get("reserved_usdt") or 0)
        if pos.get("venue") == "futures":
            try:
                fut = futures_ex()
                if fut is not None:
                    fb = fut.fetch_balance()
                    total += float((fb.get("total") or {}).get("USDT") or 0)
            except Exception:
                pass
        return max(total, 0.0)

    # Flat: only real USDT float/reserved + any leftover ETH the scalper still holds
    eth_usd = free_asset("ETH") * px
    if st.get("active_float"):
        return max(free_asset("USDT") + eth_usd, 0.0)
    reserved = float(st.get("reserved_usdt") or 0)
    usdt = free_asset("USDT")
    float_usd = min(reserved, usdt) if reserved > 0 else 0.0
    return max(float_usd + eth_usd, 0.0)


_futures_ok: bool | None = None
_futures_reason: str | None = None


def futures_ex():
    global _futures_ok, _futures_reason
    if _futures_ok is False:
        return None
    try:
        import ccxt
        from src.trading.connectors.binance import sdk as bn

        cfg = bn.load_config()
        fut = ccxt.binanceusdm(
            {"apiKey": cfg.api_key, "secret": cfg.api_secret, "enableRateLimit": True}
        )
        fut.load_markets()
        # permission probe
        fut.fetch_balance()
        _futures_ok = True
        return fut
    except Exception as exc:  # noqa: BLE001
        _futures_ok = False
        _futures_reason = str(exc)[:160]
        return None


def redeem_asset_earn(asset: str) -> bool:
    _, _, ex = bn_ex()
    try:
        data = ex.request("simple-earn/flexible/position", "sapi", "GET", {"size": 100})
    except Exception as exc:  # noqa: BLE001
        log(f"EARN_POS_FAIL {exc}")
        return False
    rows = [r for r in (data.get("rows") or []) if str(r.get("asset") or "").upper() == asset.upper()]
    if not rows:
        return False
    row = rows[0]
    if not row.get("canRedeem", True):
        return False
    product_id = row.get("productId")
    if not product_id:
        return False
    try:
        ex.request(
            "simple-earn/flexible/redeem",
            "sapi",
            "POST",
            {"productId": product_id, "redeemAll": True},
        )
        log(f"REDEEM_OK {asset}")
        time.sleep(4)
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"REDEEM_FAIL {asset} {exc}")
        return False


def bootstrap_min_float(st: dict) -> float:
    """Ensure >= MIN_NOTIONAL USDT or ETH for Binance filters (ETH book alone can be ~$4.8)."""
    px = eth_price()
    eth_q = ensure_spot_eth()
    eth_usd = eth_q * px
    usdt = free_asset("USDT")
    if eth_usd >= MIN_NOTIONAL or usdt >= MIN_NOTIONAL:
        return max(eth_usd, usdt)
    redeem_asset_earn("USDT")
    usdt = free_asset("USDT")
    if eth_usd + usdt >= MIN_NOTIONAL:
        st["active_float"] = True
        st["reserved_usdt"] = usdt
        st["bankroll_usdt"] = round(max(float(st.get("bankroll_usdt") or 0), eth_usd + usdt), 4)
        save_state(st)
        return eth_usd + usdt
    # Seed from BNB only if vendible; cooldown dust failures (avoid log spam)
    skip_until = float(st.get("bnb_bootstrap_skip_until") or 0)
    if time.time() < skip_until:
        return eth_usd + usdt
    redeem_asset_earn("BNB")
    bnb = free_asset("BNB")
    if bnb > 0:
        _, _, ex = bn_ex()
        try:
            bnb_px = float(http_get("https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT")["price"])
            # Hard dust gate before touching precision/order APIs
            if bnb < 0.001 or bnb * bnb_px < MIN_NOTIONAL * 0.99:
                log(f"BOOTSTRAP_BNB_SKIP dust bnb={bnb} usd={bnb * bnb_px:.4f}")
                st["bnb_bootstrap_skip_until"] = time.time() + 6 * 3600
                save_state(st)
                return eth_usd + usdt
            need = max(MIN_NOTIONAL + 0.15 - usdt, MIN_NOTIONAL)
            qty = min(bnb, need / bnb_px)
            qty = float(ex.amount_to_precision("BNB/USDT", qty))
            min_amt = 0.001
            try:
                raw_min = (ex.market("BNB/USDT").get("limits") or {}).get("amount", {}).get("min")
                if raw_min is not None:
                    min_amt = max(0.001, float(raw_min))
            except Exception:
                pass
            if qty < min_amt or qty * bnb_px < MIN_NOTIONAL * 0.99:
                log(f"BOOTSTRAP_BNB_SKIP qty={qty} min_amt={min_amt} notional={qty * bnb_px:.2f}")
                st["bnb_bootstrap_skip_until"] = time.time() + 6 * 3600
                save_state(st)
            else:
                with with_order_lock():
                    ex.create_order("BNB/USDT", "market", "sell", qty)
                log(f"BOOTSTRAP_BNB_USDT qty={qty} notional={qty * bnb_px:.2f}")
                time.sleep(2)
                st["active_float"] = True
                st["reserved_usdt"] = free_asset("USDT")
                st["bankroll_usdt"] = round(
                    max(float(st.get("bankroll_usdt") or 0), float(st["reserved_usdt"] or 0)),
                    4,
                )
                save_state(st)
        except Exception as exc:  # noqa: BLE001
            log(f"BOOTSTRAP_BNB_FAIL {exc}")
            st["bnb_bootstrap_skip_until"] = time.time() + 6 * 3600
            save_state(st)
    return ensure_spot_eth() * eth_price() + free_asset("USDT")


def redeem_eth_earn() -> bool:
    return redeem_asset_earn("ETH")


def ensure_spot_eth() -> float:
    q = free_asset("ETH")
    if q * eth_price() >= MIN_NOTIONAL * 0.95:
        return q
    redeem_eth_earn()
    return free_asset("ETH")


def convert_eth_to_usdt(need_usd: float) -> float:
    """DISABLED: full-stack CONVERT churn is forbidden. Liquid USDT only."""
    log(f"CONVERT_ETH_BLOCKED need={need_usd:.2f} (use liquid USDT only)")
    try:
        import trade_events as te
        import market_orchestrator as orch

        te.record_skip(
            "SKIP_NO_BANKROLL",
            bot="scalper",
            detail=f"convert_blocked need={need_usd:.2f}",
            mode=orch.current_mode(),
        )
    except Exception:
        pass
    return free_asset("USDT")


def repatriate_to_eth(st: dict) -> None:
    """Rarely: buy ETH with leftover scalp USDT. Max 1 / 2h; modes scalp_primary|defensive; USDT>=5."""
    u = free_asset("USDT")
    if u < MIN_NOTIONAL:
        st["active_float"] = False
        st["reserved_usdt"] = 0.0
        save_state(st)
        return

    try:
        import market_orchestrator as orch

        mode = orch.current_mode()
    except Exception:
        mode = "defensive"

    last = float(st.get("last_repatriate_ts") or 0)
    now = time.time()
    if mode not in ("scalp_primary", "defensive"):
        log(f"REPATRIATE_SKIP mode={mode}")
        return
    if u < REPATRIATE_MIN_USDT:
        log(f"REPATRIATE_SKIP usdt={u:.2f}<{REPATRIATE_MIN_USDT}")
        return
    if now - last < REPATRIATE_COOLDOWN_SEC:
        log(f"REPATRIATE_SKIP cooldown {(REPATRIATE_COOLDOWN_SEC - (now - last)) / 60:.0f}m")
        return

    notional = u * 0.995
    if notional < MIN_NOTIONAL:
        st["active_float"] = False
        st["reserved_usdt"] = 0.0
        save_state(st)
        return
    place_spot_buy(notional)
    st["active_float"] = False
    st["reserved_usdt"] = 0.0
    st["last_repatriate_ts"] = now
    save_state(st)
    log(f"REPATRIATE_ETH notional={notional:.2f}")


def order_succeeded(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("denied") or result.get("breach") or result.get("status") == "blocked":
        return False
    status = str(result.get("status") or "").lower()
    if status in ("error", "rejected", "denied", "failed", "blocked"):
        return False
    if result.get("error"):
        return False
    return status in ("closed", "filled", "ok", "open") or bool(result.get("id"))


def place_gate(side: str, notional: float | None = None, quantity: float | None = None) -> dict:
    from src.trading.connectors.binance import sdk as bn
    from src.live.enforcement import OrderIntent
    from src.live.mandate.model import InstrumentType, AssetClass
    from src.live.sdk_order_gate import execute_live_order

    cfg = bn.load_config()
    n = notional
    if n is None and quantity is not None:
        n = quantity * eth_price()
    intent = OrderIntent(
        symbol=PAIR,
        side=side,
        notional_usd=float(n or 0),
        quantity=quantity,
        instrument_type=InstrumentType.CRYPTO,
        asset_class=AssetClass.CRYPTO,
    )
    kwargs: dict[str, Any] = {"symbol": PAIR, "side": side, "order_type": "market"}
    if quantity is not None:
        kwargs["quantity"] = quantity
    else:
        kwargs["notional"] = notional
    with with_order_lock():
        return execute_live_order(
            broker="binance",
            connector_module=bn,
            config=cfg,
            intent=intent,
            place_kwargs=kwargs,
            session_id="eth-scalp-hybrid",
        )


def place_spot_buy(notional: float) -> dict:
    return place_gate("buy", notional=notional)


def place_spot_sell_qty(qty: float) -> dict:
    return place_gate("sell", quantity=qty)


def place_spot_sell_raw(qty: float) -> dict:
    _, _, ex = bn_ex()
    qty = float(ex.amount_to_precision(PAIR, qty))
    with with_order_lock():
        return ex.create_order(PAIR, "market", "sell", qty)


def klines(interval: str = "1m", limit: int = 60) -> list:
    return http_get(
        f"https://api.binance.com/api/v3/klines?symbol={SYMBOL}&interval={interval}&limit={limit}"
    )


def detect_regime(kl1: list, kl5: list) -> dict[str, Any]:
    c1 = [float(x[4]) for x in kl1]
    c5 = [float(x[4]) for x in kl5]
    v1 = [float(x[5]) for x in kl1]
    last = c1[-1]
    # range / atr of last 20 1m bars
    window = c1[-20:]
    hi, lo = max(window), min(window)
    rng = (hi - lo) / max(last, 1e-9)
    avg_vol = sum(v1[-20:]) / 20.0
    last_vol = v1[-1]
    mom1 = (c1[-1] / c1[-4] - 1.0) if len(c1) >= 4 else 0.0
    mom5 = (c5[-1] / c5[-4] - 1.0) if len(c5) >= 4 else 0.0
    # dead: tiny range
    if rng < 0.0012:
        return {"regime": "dead", "score": 0.0, "rng": rng, "mom1": mom1, "mom5": mom5, "hi": hi, "lo": lo}
    aligned = mom1 * mom5 > 0 and abs(mom1) >= 0.0015
    vol_ok = last_vol > avg_vol * 1.15
    if aligned and abs(mom5) >= 0.002 and vol_ok:
        score = 2.5 + min(2.0, abs(mom1) * 400) + (0.5 if vol_ok else 0)
        return {
            "regime": "trend",
            "score": score,
            "side": "buy" if mom1 > 0 else "sell",
            "rng": rng,
            "mom1": mom1,
            "mom5": mom5,
            "hi": hi,
            "lo": lo,
        }
    # range fade near edges
    pos = (last - lo) / max(hi - lo, 1e-9)
    if pos <= 0.18:
        return {
            "regime": "range",
            "score": 2.8 + (0.18 - pos) * 5,
            "side": "buy",
            "rng": rng,
            "mom1": mom1,
            "mom5": mom5,
            "hi": hi,
            "lo": lo,
        }
    if pos >= 0.82:
        return {
            "regime": "range",
            "score": 2.8 + (pos - 0.82) * 5,
            "side": "sell",
            "rng": rng,
            "mom1": mom1,
            "mom5": mom5,
            "hi": hi,
            "lo": lo,
        }
    return {"regime": "dead", "score": 1.0, "rng": rng, "mom1": mom1, "mom5": mom5, "hi": hi, "lo": lo}


def dynamic_knobs(regime: str, atr_proxy: float) -> dict[str, float]:
    atr_proxy = max(0.001, min(0.01, atr_proxy))
    if regime == "trend":
        return {
            "tp": min(0.0055, 0.0025 + atr_proxy * 0.4),
            "sl": min(0.0035, 0.0018 + atr_proxy * 0.25),
            "trail_act": 0.0020,
            "trail_gb": 0.0010,
            "time_stop": float(TIME_STOP_MOM_SEC),
        }
    return {
        "tp": min(0.0040, 0.0025 + atr_proxy * 0.2),
        "sl": min(0.0035, 0.0020 + atr_proxy * 0.15),
        "trail_act": 0.0018,
        "trail_gb": 0.0009,
        "time_stop": float(TIME_STOP_FADE_SEC),
    }


def record_scalp_equity(eq: float | None = None, st: dict | None = None) -> float:
    """Append live ETH scalper equity (honest bankroll, not whole-wallet ETH)."""
    if eq is None:
        if st is None:
            st = load_state()
        total = float(eth_book_equity(st))
        st["last_equity"] = total
        save_state(st)
    else:
        total = float(eq)
    try:
        sys.path.insert(0, str(HOME))
        import equity_chart as ec

        ec.record_equity(EQUITY_HISTORY_PATH, total, min_interval_sec=60.0)
    except Exception as exc:  # noqa: BLE001
        log(f"EQUITY_HIST_FAIL {exc}")
    return total


def tg_alert(
    text: str,
    kl: list | None = None,
    *,
    entry: float | None = None,
    exit_px: float | None = None,
) -> None:
    """Same equity-over-time PNG format as Binance / Alpaca digests."""
    _ = kl, entry, exit_px  # kept for call-site compatibility
    try:
        sys.path.insert(0, str(HOME))
        from telegram_notify_prefs import should_notify

        if not should_notify("scalper"):
            return
    except Exception:
        pass

    env = load_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return

    st = load_state()
    eq = record_scalp_equity(st=st)
    day_open = float(st.get("day_open_equity") or eq)
    try:
        sys.path.insert(0, str(HOME))
        import equity_chart as ec

        ok, _, _ = ec.build_and_send(
            history_path=EQUITY_HISTORY_PATH,
            chart_path=EQUITY_CHART_PATH,
            token=token,
            chat=chat,
            venue_tag="ETH scalping",
            equity=eq,
            text=text,
            day_open=day_open,
            daily_target=0.0,
            week_open=day_open,
            weekly_target=0.0,
            channel="scalper",
            force=False,
        )
        if ok:
            return
        log("TG_EQUITY_CHART_FALLBACK")
    except Exception as exc:  # noqa: BLE001
        log(f"TG_EQUITY_CHART_FAIL {exc}")
    tg_send_text(text)


def tg_send_text(text: str) -> None:
    try:
        sys.path.insert(0, str(HOME))
        from telegram_notify_prefs import send_text

        send_text(text, channel="scalper")
        return
    except Exception as exc:  # noqa: BLE001
        log(f"TG_TEXT_FALLBACK {exc}")
    env = load_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    body = json.dumps(
        {"chat_id": chat, "text": text[:900], "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as exc:  # noqa: BLE001
        log(f"TG_FAIL {exc}")


def format_buy(usd: float, reason: str, st: dict, eq: float) -> str:
    realized = float(st.get("realized_pnl_today") or 0)
    rsign = "+" if realized >= 0 else ""
    return "\n".join(
        [
            "[Binance Scalper]",
            "COMPRA · Ethereum",
            f"Pague unos ${usd:.2f}",
            f"Por que: {reason}",
            f"Trades scalper hoy: {st.get('fills_today', 0)} (roundtrips {st.get('roundtrips_today', 0)}/{MAX_ROUNDTRIPS})",
            f"Book scalper (real): ${eq:.2f}",
            f"PnL realizado hoy: {rsign}${realized:.2f}",
            "Siguiente: busca salida rapida (segundos a pocos minutos).",
        ]
    )


def format_sell(usd: float, pnl_pct: float, kind: str, st: dict, eq: float) -> str:
    sign = "+" if pnl_pct >= 0 else ""
    trade_pnl = usd * pnl_pct
    realized = float(st.get("realized_pnl_today") or 0)
    rsign = "+" if realized >= 0 else ""
    return "\n".join(
        [
            "[Binance Scalper]",
            "VENTA · Ethereum",
            "Volvio dinero a USDT (o cerro futures)",
            f"Resultado trade: {sign}${trade_pnl:.2f} ({sign}{pnl_pct * 100:.2f}%) — {kind}",
            f"PnL realizado hoy (suma trades): {rsign}${realized:.2f}",
            f"Trades scalper hoy: {st.get('fills_today', 0)}",
            f"Book scalper (real): ${eq:.2f}",
            "Siguiente: el bot decide si convierte otra vez o espera el panorama.",
        ]
    )


def format_guard(title: str, detail: str, eq: float) -> str:
    return "\n".join(
        [
            "[Binance Scalper]",
            title,
            detail,
            f"Book scalper (real): ${eq:.2f}",
            "No tienes que hacer nada.",
        ]
    )


def size_usd(score: float, eth_usd: float, usdt: float) -> float:
    # Liquid USDT only — never size off illiquid ETH that would force CONVERT
    budget = usdt
    _ = eth_usd
    cap = 5.0  # micro book: never take more than ~$5 per scalper entry
    if budget < MIN_NOTIONAL:
        return 0.0
    if score >= HIGH_SCORE:
        return min(cap, budget * 0.98, budget)
    if score >= 2.5:
        return min(cap, max(MIN_NOTIONAL, budget * 0.55), budget)
    return min(cap, MIN_NOTIONAL, budget)


def maybe_futures_entry(st: dict, side: str, notional: float, score: float, regime: str) -> dict | None:
    """Futures only on strong trend with liquid USDT and notional >= $20. No CONVERT."""
    if regime != "trend" or score < HIGH_SCORE:
        return None
    if notional < FUTURES_MIN_NOTIONAL:
        log(f"FUT_SKIP notional={notional:.2f}<{FUTURES_MIN_NOTIONAL} spot_only")
        return None
    fut = futures_ex()
    if fut is None:
        st["futures_enabled"] = False
        st["spot_only_reason"] = _futures_reason or "futures unavailable"
        save_state(st)
        return None
    u = free_asset("USDT")
    margin_need = notional / FUTURES_MAX_LEV + 1.0
    if u < margin_need:
        log(f"FUT_SKIP low_liquid_usdt={u:.2f} need={margin_need:.2f}")
        return None
    # transfer spot→futures if needed (Binance sapi)
    try:
        from src.trading.connectors.binance import sdk as bn

        cfg = bn.load_config()
        ex = bn._exchange(cfg)
        transfer = min(u * 0.95, margin_need)
        ex.request(
            "asset/transfer",
            "sapi",
            "POST",
            {"type": "MAIN_UMFUTURE", "asset": "USDT", "amount": f"{transfer:.4f}"},
        )
        time.sleep(2)
    except Exception as exc:  # noqa: BLE001
        log(f"FUT_TRANSFER_FAIL {exc} — stay spot")
        return None
    try:
        market = "ETH/USDT:USDT" if "ETH/USDT:USDT" in fut.markets else "ETH/USDT"
        fut.set_leverage(FUTURES_MAX_LEV, market)
        try:
            fut.set_margin_mode("isolated", market)
        except Exception:
            pass
        px = eth_price()
        qty = float(fut.amount_to_precision(market, (notional) / px))
        if qty <= 0:
            return None
        o_side = "buy" if side == "buy" else "sell"
        with with_order_lock():
            order = fut.create_order(market, "market", o_side, qty)
        log(f"FUTURES_OPEN {o_side} qty={qty} lev={FUTURES_MAX_LEV} id={order.get('id')}")
        st["futures_enabled"] = True
        st["active_float"] = True
        st["reserved_usdt"] = transfer
        fill_px = float(order.get("average") or order.get("price") or px)
        return {
            "venue": "futures",
            "side": side,
            "qty": qty,
            "entry": fill_px,
            "peak": fill_px,
            "trough": fill_px,
            "opened_ts": utc_ts(),
            "opened_epoch": time.time(),
            "usd": notional,
            "regime": regime,
            "market": market,
        }
    except Exception as exc:  # noqa: BLE001
        log(f"FUTURES_OPEN_FAIL {exc}")
        return None


def open_spot_long(st: dict, notional: float, regime: str, reason: str) -> bool:
    # need USDT already liquid — do NOT convert entire ETH stack to "try"
    u = free_asset("USDT")
    notional = min(notional, u * 0.99)
    if notional < MIN_NOTIONAL:
        try:
            import trade_events as te
            import market_orchestrator as orch

            te.record_skip(
                "SKIP_NO_BANKROLL",
                bot="scalper",
                detail=f"usdt={u:.2f}",
                mode=orch.current_mode(),
            )
        except Exception:
            pass
        log(f"SKIP_BUY low_usdt={u} (no convert churn)")
        return False
    st["active_float"] = True
    st["reserved_usdt"] = max(float(st.get("reserved_usdt") or 0), notional)
    save_state(st)
    result = place_spot_buy(notional)
    log(f"SPOT_BUY {result.get('status')} {result.get('id')}")
    if not order_succeeded(result):
        return False
    px = float(result.get("average") or result.get("price") or eth_price())
    # prefer filled qty from result; fallback notional/px
    qty = None
    for key in ("filled", "amount", "quantity"):
        if result.get(key):
            try:
                qty = float(result[key])
                break
            except (TypeError, ValueError):
                pass
    live = result.get("live_action") or {}
    if qty is None and live.get("quantity"):
        try:
            qty = float(live["quantity"])
        except (TypeError, ValueError):
            pass
    if qty is None or qty <= 0:
        qty = notional / max(px, 1e-9)
    st["position"] = {
        "venue": "spot",
        "side": "buy",
        "qty": qty,
        "entry": px,
        "peak": px,
        "trough": px,
        "opened_ts": utc_ts(),
        "opened_epoch": time.time(),
        "usd": notional,
        "regime": regime,
    }
    st["fills_today"] = int(st.get("fills_today") or 0) + 1
    st["last_fill_ts"] = time.time()
    save_state(st)
    eq = eth_book_equity(st)
    try:
        import market_orchestrator as orch
        import trade_events as te

        te.record_trade_event(
            bot="scalper",
            side="buy",
            symbol="ETH",
            price=px,
            usd=float(notional),
            mode=orch.current_mode(),
            regime=regime,
            equity=eq,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"TRADE_EVENT_FAIL buy {exc}")
    tg_alert(format_buy(notional, reason, st, eq), entry=px)
    return True


def close_position(st: dict, kind: str, pnl_pct: float) -> None:
    pos = st.get("position") or {}
    if not pos:
        return
    venue = pos.get("venue")
    entry = float(pos.get("entry") or 0)
    usd = float(pos.get("usd") or MIN_NOTIONAL)
    exit_px = eth_price()
    if venue == "futures":
        fut = futures_ex()
        if fut is not None:
            market = pos.get("market") or "ETH/USDT:USDT"
            qty = float(pos.get("qty") or 0)
            close_side = "sell" if pos.get("side") == "buy" else "buy"
            try:
                with with_order_lock():
                    order = fut.create_order(market, "market", close_side, qty, params={"reduceOnly": True})
                exit_px = float(order.get("average") or order.get("price") or exit_px)
                log(f"FUTURES_CLOSE {close_side} {order.get('id')}")
            except Exception as exc:  # noqa: BLE001
                log(f"FUTURES_CLOSE_FAIL {exc}")
        # try transfer back (full salvage helper)
        try:
            import binance_wallets as bw

            moved = bw.salvage_usdt_to_spot(force=True)
            log(f"FUT_REPAT {moved}")
        except Exception as exc:  # noqa: BLE001
            log(f"FUT_REPAT_TRANSFER_FAIL {exc}")
            try:
                from src.trading.connectors.binance import sdk as bn

                cfg = bn.load_config()
                ex = bn._exchange(cfg)
                fut = futures_ex()
                if fut:
                    fb = fut.fetch_balance()
                    amt = float((fb.get("free") or {}).get("USDT") or 0)
                    if amt > 1:
                        ex.request(
                            "asset/transfer",
                            "sapi",
                            "POST",
                            {"type": "UMFUTURE_MAIN", "asset": "USDT", "amount": f"{amt * 0.99:.4f}"},
                        )
            except Exception as exc2:  # noqa: BLE001
                log(f"FUT_REPAT_FALLBACK_FAIL {exc2}")
    else:
        qty = float(pos.get("qty") or 0)
        free = free_asset("ETH")
        qty = min(qty, free) if qty > 0 else free
        if qty * eth_price() >= MIN_NOTIONAL * 0.9:
            try:
                result = place_spot_sell_qty(qty)
                if not order_succeeded(result):
                    place_spot_sell_raw(qty)
                else:
                    exit_px = float(result.get("average") or result.get("price") or exit_px)
            except Exception as exc:  # noqa: BLE001
                log(f"SPOT_SELL_FAIL {exc}")
                try:
                    place_spot_sell_raw(qty)
                except Exception as exc2:  # noqa: BLE001
                    log(f"SPOT_SELL_RAW_FAIL {exc2}")
                    return
        else:
            log(f"SPOT_SELL_SKIP tiny qty={qty}")
            return

    st["fills_today"] = int(st.get("fills_today") or 0) + 1
    st["roundtrips_today"] = int(st.get("roundtrips_today") or 0) + 1
    st["last_fill_ts"] = time.time()
    realized = float(usd) * float(pnl_pct)
    st["realized_pnl_today"] = round(float(st.get("realized_pnl_today") or 0) + realized, 6)
    bank = float(st.get("bankroll_usdt") or usd)
    st["bankroll_usdt"] = round(bank + realized, 4)
    if pnl_pct < 0:
        st["loss_streak"] = int(st.get("loss_streak") or 0) + 1
    else:
        st["loss_streak"] = 0
    if int(st.get("loss_streak") or 0) >= STREAK_LOSS_LIMIT:
        st["pause_until"] = time.time() + STREAK_PAUSE_SEC
    st["position"] = None
    st["last_equity"] = eth_book_equity(st)
    save_state(st)
    eq = float(st["last_equity"])
    try:
        import market_orchestrator as orch
        import trade_events as te

        te.record_trade_event(
            bot="scalper",
            side="sell",
            symbol="ETH",
            price=exit_px,
            usd=float(usd),
            mode=orch.current_mode(),
            regime=str(pos.get("regime") or ""),
            pnl_pct=float(pnl_pct),
            equity=eq,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"TRADE_EVENT_FAIL sell {exc}")
    tg_alert(format_sell(usd, pnl_pct, kind, st, eq), entry=entry, exit_px=exit_px)
    if st.get("pause_until"):
        tg_alert(
            format_guard(
                "PAUSA racha",
                f"Tres perdidas seguidas; pausa {STREAK_PAUSE_SEC // 60} min para no martillar.",
                eq,
            )
        )
    # repatriate USDT→ETH when flat (rate-limited + mode-gated)
    try:
        repatriate_to_eth(st)
    except Exception as exc:  # noqa: BLE001
        log(f"REPAT_FAIL {exc}")


def manage_open(st: dict) -> bool:
    pos = st.get("position")
    if not pos:
        return False
    px = eth_price()
    entry = float(pos.get("entry") or px)
    side = pos.get("side") or "buy"
    if side == "buy":
        pnl = px / entry - 1.0
        peak = max(float(pos.get("peak") or entry), px)
        pos["peak"] = peak
    else:
        pnl = entry / px - 1.0
        trough = min(float(pos.get("peak") or entry), px)
        pos["peak"] = trough

    kn = dynamic_knobs(str(pos.get("regime") or "trend"), float(pos.get("rng") or 0.003))
    age = time.time() - float(pos.get("opened_epoch") or time.time())
    kind = None
    if pnl <= -kn["sl"]:
        kind = f"SL {pnl*100:.2f}%"
    elif pnl >= kn["tp"]:
        kind = f"TP {pnl*100:.2f}%"
    elif peak_or_trail(pos, side, entry, px, kn):
        kind = f"TRAIL {pnl*100:.2f}%"
    elif age >= max(float(kn["time_stop"]), MIN_TIME_EXIT_SEC):
        kind = f"TIME {age/60:.1f}m"
    if pos.get("regime") == "range" and pos.get("hi") and pos.get("lo"):
        hi, lo = float(pos["hi"]), float(pos["lo"])
        if side == "buy" and px > hi * 1.0015 and pnl < kn["tp"] and age >= MIN_TIME_EXIT_SEC:
            kind = kind or "RANGE_BREAK"
        if side == "sell" and px < lo * 0.9985 and pnl < kn["tp"] and age >= MIN_TIME_EXIT_SEC:
            kind = kind or "RANGE_BREAK"

    # Never TIME-exit before 5 minutes (SL always allowed)
    if kind and str(kind).startswith("TIME") and age < MIN_TIME_EXIT_SEC:
        try:
            import trade_events as te
            import market_orchestrator as orch

            te.record_skip(
                "SKIP_HOLD",
                bot="scalper",
                detail=f"age={age:.0f}s want={kind}",
                mode=orch.current_mode(),
            )
        except Exception:
            pass
        kind = None

    st["position"] = pos
    save_state(st)
    if kind:
        close_position(st, kind, pnl)
        return True
    return False


def peak_or_trail(pos: dict, side: str, entry: float, px: float, kn: dict) -> bool:
    if side == "buy":
        peak = float(pos.get("peak") or entry)
        if peak >= entry * (1 + kn["trail_act"]) and px <= peak * (1 - kn["trail_gb"]):
            return True
    else:
        trough = float(pos.get("peak") or entry)  # stored as favorable extreme
        if trough <= entry * (1 - kn["trail_act"]) and px >= trough * (1 + kn["trail_gb"]):
            return True
    return False


def try_entry(st: dict) -> None:
    if st.get("position"):
        return
    try:
        import market_orchestrator as orch
        import trade_events as te

        mode = orch.current_mode()
        if not orch.allows_scalper_entries(mode):
            te.record_skip("SKIP_MODE", bot="scalper", detail=f"mode={mode}", mode=mode)
            log(f"SKIP_MODE entries blocked mode={mode}")
            return
    except Exception as exc:  # noqa: BLE001
        log(f"ORCH_GATE_FAIL {exc}")

    if int(st.get("roundtrips_today") or 0) >= MAX_ROUNDTRIPS:
        log("ROUNDTRIPS_FULL")
        return
    bootstrap_min_float(st)
    kl1, kl5 = klines("1m", 60), klines("5m", 40)
    info = detect_regime(kl1, kl5)
    st["last_regime"] = info["regime"]
    save_state(st)
    log(
        f"REGIME {info['regime']} score={info.get('score', 0):.2f} "
        f"mom1={info.get('mom1', 0)*100:.2f}% side={info.get('side')}"
    )
    if info["regime"] == "dead":
        return
    score = float(info.get("score") or 0)
    if score < 2.4:
        try:
            import trade_events as te
            import market_orchestrator as orch

            te.record_skip(
                "SKIP_SCORE",
                bot="scalper",
                detail=f"score={score:.2f}",
                mode=orch.current_mode(),
            )
        except Exception:
            pass
        return
    side = info.get("side") or "buy"
    # Spot-only: only long setups (shorts need futures)
    if side == "sell":
        log("SKIP_SHORT spot_only_longs")
        return
    eth_q = ensure_spot_eth()
    eth_usd = eth_q * eth_price()
    usdt = free_asset("USDT")
    notional = size_usd(score, eth_usd, usdt)
    if notional < MIN_NOTIONAL:
        try:
            import trade_events as te
            import market_orchestrator as orch

            te.record_skip(
                "SKIP_NO_BANKROLL",
                bot="scalper",
                detail=f"usdt={usdt:.2f} eth_usd={eth_usd:.2f}",
                mode=orch.current_mode(),
            )
        except Exception:
            pass
        log(f"NO_SIZE eth_usd={eth_usd:.2f} usdt={usdt:.2f}")
        return

    reason = (
        "impulso corto alineado (momentum)"
        if info["regime"] == "trend"
        else "rebote cerca del suelo del rango"
    )
    # Futures only when sized >= $20 (micro book typically stays spot)
    fut_notional = max(notional, FUTURES_MIN_NOTIONAL) if usdt >= FUTURES_MIN_NOTIONAL else notional
    if info["regime"] == "trend" and score >= HIGH_SCORE and usdt >= FUTURES_MIN_NOTIONAL:
        meta = maybe_futures_entry(st, "buy", min(fut_notional, usdt * 0.95), score, "trend")
        if meta is not None:
            meta["rng"] = float(info.get("rng") or 0.003)
            meta["hi"] = info.get("hi")
            meta["lo"] = info.get("lo")
            st["position"] = meta
            st["fills_today"] = int(st.get("fills_today") or 0) + 1
            st["last_fill_ts"] = time.time()
            save_state(st)
            eq = eth_book_equity(st)
            try:
                import market_orchestrator as orch
                import trade_events as te

                te.record_trade_event(
                    bot="scalper",
                    side="buy",
                    symbol="ETH",
                    price=float(meta["entry"]),
                    usd=float(meta.get("usd") or fut_notional),
                    mode=orch.current_mode(),
                    regime="trend",
                    equity=eq,
                )
            except Exception as exc:  # noqa: BLE001
                log(f"TRADE_EVENT_FAIL fut_buy {exc}")
            tg_alert(format_buy(float(meta.get("usd") or fut_notional), reason + " via futures", st, eq), entry=float(meta["entry"]))
            return

    if open_spot_long(st, notional, info["regime"], reason):
        pos = st.get("position") or {}
        pos["rng"] = float(info.get("rng") or 0.003)
        pos["hi"] = info.get("hi")
        pos["lo"] = info.get("lo")
        st["position"] = pos
        save_state(st)


def guard_blocked(st: dict) -> bool:
    eq = eth_book_equity(st)
    day_open = float(st.get("day_open_equity") or eq)
    if day_open > 0 and (eq / day_open - 1.0) <= KILL_DAY_PCT:
        if not st.get("killed"):
            st["killed"] = True
            save_state(st)
            if st.get("position"):
                px = eth_price()
                entry = float((st["position"] or {}).get("entry") or px)
                side = (st["position"] or {}).get("side") or "buy"
                pnl = (px / entry - 1.0) if side == "buy" else (entry / px - 1.0)
                close_position(st, "KILL", pnl)
            tg_alert(
                format_guard(
                    "KILL diario",
                    "El book del scalper bajo ~8% hoy; se detiene hasta manana.",
                    eq,
                )
            )
        return True
    if st.get("killed"):
        return True
    pause_until = st.get("pause_until")
    if pause_until and time.time() < float(pause_until):
        return True
    if pause_until and time.time() >= float(pause_until):
        st["pause_until"] = None
        st["loss_streak"] = 0
        save_state(st)
    return False


def tick() -> None:
    st = load_state()
    try:
        import market_orchestrator as orch

        orch.evaluate_and_update(notify=True)
    except Exception as exc:  # noqa: BLE001
        log(f"ORCH_FAIL {exc}")

    # Idle: pull Funding/Futures USDT back to Spot so v6 sees cash
    if not st.get("position"):
        try:
            import binance_wallets as bw

            moved = bw.salvage_usdt_to_spot(force=False)
            if moved:
                log(f"SALVAGE_IDLE {moved}")
        except Exception as exc:  # noqa: BLE001
            log(f"SALVAGE_FAIL {exc}")

    # probe futures once / refresh flags when OK
    fut = futures_ex()
    if fut is None:
        if not st.get("spot_only_reason"):
            st["spot_only_reason"] = _futures_reason or "no futures"
            st["futures_enabled"] = False
            save_state(st)
            log(f"SPOT_ONLY {_futures_reason}")
    else:
        if not st.get("futures_enabled"):
            st["futures_enabled"] = True
            st["spot_only_reason"] = None
            save_state(st)
            log("FUTURES_ENABLED")

    # Keep equity history fresh for /eth and /estado charts
    try:
        record_scalp_equity()
    except Exception as exc:  # noqa: BLE001
        log(f"EQUITY_TICK_FAIL {exc}")

    if guard_blocked(st):
        log("GUARD_BLOCKED")
        maybe_heartbeat(st)
        return
    if manage_open(st):
        return
    try_entry(st)
    maybe_heartbeat(load_state())


def main() -> None:
    log(f"ETH_SCALP_START {STRATEGY_TAG}")
    try:
        bn_ex()  # warm markets cache
    except Exception as exc:  # noqa: BLE001
        log(f"INIT_MARKETS {exc}")
    try:
        ensure_spot_eth()
    except Exception as exc:  # noqa: BLE001
        log(f"INIT_ETH {exc}")
    fut = futures_ex()
    log(f"FUTURES {'ok' if fut else 'disabled'} {_futures_reason or ''}")
    st = load_state()
    if fut is not None:
        st["futures_enabled"] = True
        st["spot_only_reason"] = None
    else:
        st["futures_enabled"] = False
        st["spot_only_reason"] = _futures_reason or "no futures"
    if not st.get("last_fill_ts"):
        st["last_fill_ts"] = time.time()
    save_state(st)
    maybe_startup_tg(st)
    while True:
        try:
            tick()
        except Exception as exc:  # noqa: BLE001
            log(f"TICK_ERR {exc}")
            maybe_tg_error(str(exc))
        st = load_state()
        sleep = POLL_ACTIVE if st.get("position") else POLL_STANDBY
        time.sleep(sleep)


if __name__ == "__main__":
    main()
