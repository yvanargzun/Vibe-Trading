#!/usr/bin/env python3
"""smart-fast-v6: fast realized profits + x2 cycle (Hetzner Binance Spot).

Law: PROMPT_V6.md · knobs: v6_config.py · cycle debug: v6_trace.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import traceback
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("VIBE_TRADING_HOME", "/root/.vibe-trading"))
for _p in (_HERE, HOME, Path("/root/.vibe-trading")):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

import dynamic_goals as goals
import equity_chart as equity_chart
import v6_config as cfg
import v6_trace

AGENT = Path("/opt/vibe-trade/agent")
sys.path.insert(0, str(AGENT))

ENV_PATH = HOME / ".env"
STATE_PATH = HOME / "autotrade_state.json"
LOG_PATH = HOME / "autotrade_loop.log"

# --- KNOBS (re-export from v6_config for call sites) ---
ORDER_USD = cfg.ORDER_USD
TP = cfg.TP
SL = cfg.SL
TRAIL_ACT = cfg.TRAIL_ACT
TRAIL_GB = cfg.TRAIL_GB
TIME_STOP_HOURS = cfg.TIME_STOP_HOURS
MIN_EXIT_USD = cfg.MIN_EXIT_USD
MAX_BUYS_PER_DAY = cfg.MAX_BUYS_PER_DAY
MAX_OPEN_LEGS = cfg.MAX_OPEN_LEGS
MAX_MAJOR_BUYS_DAY = cfg.MAX_MAJOR_BUYS_DAY
COOLDOWN_HOURS = cfg.COOLDOWN_HOURS
POLL_OPEN_SEC = cfg.POLL_OPEN_SEC
POLL_HUNT_SEC = cfg.POLL_HUNT_SEC
POLL_IDLE_SEC = cfg.POLL_IDLE_SEC
MIN_USDT = cfg.MIN_USDT
MIN_BUY_SCORE = cfg.MIN_BUY_SCORE
MIN_BUY_SCORE_BEAR = cfg.MIN_BUY_SCORE_BEAR
DUST_USD = cfg.DUST_USD
DAY_LOSS_HALT_PCT = cfg.DAY_LOSS_HALT_PCT
STRATEGY_TAG = cfg.STRATEGY_TAG
VENUE_TAG = cfg.VENUE_TAG
MAJORS = set(cfg.MAJORS)
FUND_ASSETS = cfg.FUND_ASSETS
UNIVERSE = list(cfg.UNIVERSE)
STABLES = set(cfg.STABLES)
SCALP_USDT_RESERVE = cfg.SCALP_USDT_RESERVE
SCALP_STATE_PATH = HOME / "eth_scalp_state.json"
ORDER_LOCK_PATH = HOME / "order.lock"
INTENTS_PATH = HOME / "ops_intents.jsonl"

# Per-tick guards
_tick_sold: set[str] = set()
_tick_fund_sold: set[str] = set()


def refresh_knobs_from_overlay() -> dict:
    """Pull Ops overlay into cfg + local aliases used by this module."""
    global ORDER_USD, TP, SL, TRAIL_ACT, TRAIL_GB, TIME_STOP_HOURS
    global MAX_BUYS_PER_DAY, MAX_OPEN_LEGS, MIN_BUY_SCORE, MIN_BUY_SCORE_BEAR
    global DAY_LOSS_HALT_PCT, COOLDOWN_HOURS, MIN_USDT
    applied = cfg.apply_overlay(str(HOME))
    ORDER_USD = cfg.ORDER_USD
    TP = cfg.TP
    SL = cfg.SL
    TRAIL_ACT = cfg.TRAIL_ACT
    TRAIL_GB = cfg.TRAIL_GB
    TIME_STOP_HOURS = cfg.TIME_STOP_HOURS
    MAX_BUYS_PER_DAY = cfg.MAX_BUYS_PER_DAY
    MAX_OPEN_LEGS = cfg.MAX_OPEN_LEGS
    MIN_BUY_SCORE = cfg.MIN_BUY_SCORE
    MIN_BUY_SCORE_BEAR = cfg.MIN_BUY_SCORE_BEAR
    DAY_LOSS_HALT_PCT = cfg.DAY_LOSS_HALT_PCT
    COOLDOWN_HOURS = cfg.COOLDOWN_HOURS
    MIN_USDT = cfg.MIN_USDT
    if applied:
        log(f"KNOBS_OVERLAY {applied}")
    return applied


def _rewrite_intents(rows: list[dict]) -> None:
    INTENTS_PATH.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def process_ops_intents(state: dict) -> int:
    """Execute queued Ops Copiloto intents (buy/sell/close). Returns actions made."""
    if not INTENTS_PATH.exists():
        return 0
    try:
        lines = INTENTS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    rows: list[dict] = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    made = 0
    changed = False
    for row in rows:
        if row.get("status") != "queued":
            continue
        action = str(row.get("action") or "")
        sym = str(row.get("symbol") or "")
        try:
            if action == "buy":
                usd = float(row.get("usd") or ORDER_USD)
                result = place_gate_order(sym, "buy", notional=usd)
                ok = order_succeeded(result)
                row["status"] = "done" if ok else "error"
                row["result"] = str(result.get("status") or result.get("error") or result)[:200]
                log(f"OPS_INTENT buy {sym} ${usd:.2f} -> {row['status']}")
                if ok:
                    made += 1
                    base = sym.split("/")[0]
                    note_buy(state, base)
            elif action in ("sell", "close"):
                base = sym.split("/")[0]
                holdings = sync_positions(state)
                h = holdings.get(base)
                if not h or h["usd"] < 0.5:
                    row["status"] = "error"
                    row["result"] = "no_position"
                else:
                    result = place_gate_order(sym, "sell", quantity=h["qty"])
                    ok = order_succeeded(result)
                    row["status"] = "done" if ok else "error"
                    row["result"] = str(result.get("status") or result.get("error") or result)[:200]
                    log(f"OPS_INTENT sell {sym} -> {row['status']}")
                    if ok:
                        made += 1
            elif action == "close_all":
                holdings = sync_positions(state)
                any_ok = False
                for base, h in list(holdings.items()):
                    if h["usd"] < 0.5:
                        continue
                    pair = f"{base}/USDT"
                    result = place_gate_order(pair, "sell", quantity=h["qty"])
                    if order_succeeded(result):
                        any_ok = True
                        made += 1
                row["status"] = "done" if any_ok else "error"
                row["result"] = "close_all"
                log(f"OPS_INTENT close_all -> {row['status']}")
            else:
                row["status"] = "error"
                row["result"] = "bad_action"
        except Exception as exc:  # noqa: BLE001
            row["status"] = "error"
            row["result"] = str(exc)[:200]
            log(f"OPS_INTENT_FAIL {action} {exc}")
        changed = True
    if changed:
        _rewrite_intents(rows)
        save_state(state)
    return made


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


def tg(text: str) -> None:
    """One Telegram bubble: novice text + equity chart (Binance)."""
    try:
        sys.path.insert(0, str(HOME))
        from telegram_notify_prefs import should_notify

        if not should_notify("vibe"):
            return
    except Exception as exc:  # noqa: BLE001
        log(f"TG_PREFS_CHECK {exc}")

    env = load_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return

    try:
        eq = book_equity()
        st = load_state()
        g = st.get("goals") or {}
        snap = st.get("goal_snap") or {}
        ok, _, _ = equity_chart.build_and_send(
            history_path=HOME / "equity_history.json",
            chart_path=HOME / "equity_alert_last.png",
            token=token,
            chat=chat,
            venue_tag="Binance",
            equity=eq,
            text=text,
            day_open=float(g.get("day_open_equity") or snap.get("day_open") or eq),
            daily_target=float(g.get("daily_target_usd") or snap.get("daily_target") or 0),
            week_open=float(g.get("week_open_equity") or snap.get("week_open") or eq),
            weekly_target=float(g.get("weekly_target_usd") or snap.get("weekly_target") or 0),
        )
        if ok:
            return
    except Exception as exc:  # noqa: BLE001
        log(f"TG_COMPOSITE_FAIL {exc}")

    # Fallback: single text only
    try:
        sys.path.insert(0, str(HOME))
        from telegram_notify_prefs import send_text

        send_text(text, channel="vibe", dedupe=False)
        return
    except Exception as exc:  # noqa: BLE001
        log(f"TG_PREFS_FALLBACK {exc}")
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
        with urllib.request.urlopen(req, timeout=20) as r:
            json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        log(f"TG_FAIL {exc}")


ASSET_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "BNB": "BNB",
    "SOL": "Solana",
    "XRP": "XRP",
    "DOGE": "Dogecoin",
    "ADA": "Cardano",
    "LINK": "Chainlink",
}


def asset_label(base: str) -> str:
    name = ASSET_NAMES.get(base, base)
    return f"{name} ({base})" if name != base else base


def _daily_cap() -> tuple[int, int]:
    from src.live.daily_count import read_daily_count
    from src.live.mandate.store import load_mandate

    mandate = load_mandate("binance")
    cap = mandate.hard_caps.max_trades_per_day if mandate else 3
    return read_daily_count("binance"), cap


def _progress_lines(state: dict | None = None) -> list[str]:
    st = state if state is not None else load_state()
    eq = book_equity()
    goals.ensure_goals(st, eq, venue="binance")
    save_state(st)
    return goals.progress_lines(st, eq)


def sell_motive(kind: str, emergency: bool) -> str:
    motives = {
        "tp": "cobro ganancia automatica",
        "sl": "corto perdidas para no perder mas",
        "trail": "protegio ganancia cuando empeco a bajar",
        "time": "no se movio a tiempo; libero el dinero",
        "rotate": "redujo posiciones para no tener demasiadas a la vez",
    }
    base = motives.get(kind, "cerro la posicion segun las reglas")
    if emergency:
        return f"dia de trades lleno; igual cerro ({base})"
    return base


def format_buy_tg(
    *,
    base: str,
    usd: float,
    topup: bool = False,
    state: dict | None = None,
) -> str:
    daily, cap = _daily_cap()
    title = "COMPRA extra" if topup else "COMPRA"
    if topup:
        motivo = (
            f"aumento una posicion chica de {asset_label(base)} "
            "para luego poder venderla (Binance pide minimo ~$5)"
        )
    else:
        motivo = f"el sistema eligio {asset_label(base)} entre las mejores opciones de ahora"
    lines = [
        f"[{VENUE_TAG}] {title} · {asset_label(base)}",
        f"Pague unos ${usd:.2f}",
        f"Por que: {motivo}",
        f"Trades del dia: {daily}/{cap}",
        *_progress_lines(state),
        (
            f"Siguiente: espera; el bot intentara vender con ganancia pequena "
            f"(~{TP * 100:.1f}%) o cortar si baja (~{SL * 100:.1f}%)"
        ),
    ]
    return "\n".join(lines)


def format_sell_tg(
    *,
    base: str,
    usd: float,
    pnl_pct: float,
    kind: str,
    emergency: bool = False,
    state: dict | None = None,
) -> str:
    daily, cap = _daily_cap()
    pnl_usd = usd * pnl_pct
    sign = "+" if pnl_usd >= 0 else ""
    title = "VENTA urgente" if emergency else "VENTA"
    result = f"{sign}${pnl_usd:.2f} ({sign}{pnl_pct * 100:.1f}%) — {sell_motive(kind, emergency)}"
    left = cap - daily
    if left > 0:
        nxt = "puede buscar otra compra si quedan trades y hay buena oportunidad"
    else:
        nxt = "hoy ya no quedan trades; el bot sigue vigilando hasta manana"
    lines = [
        f"[{VENUE_TAG}] {title} · {asset_label(base)}",
        "Volvio dinero a USDT",
        f"Resultado: {result}",
        f"Trades del dia: {daily}/{cap}",
        *_progress_lines(state),
        f"Siguiente: {nxt}",
    ]
    return "\n".join(lines)


def format_double_tg(*, old_base: float, new_eq: float) -> str:
    return "\n".join(
        [
            f"[{VENUE_TAG}] META LEJANA · duplicaste",
            f"Pasaste de ${old_base:.2f} a ${new_eq:.2f}",
            "El bot reinicia el objetivo: ahora busca duplicar otra vez desde este nuevo total.",
            "No tienes que hacer nada.",
        ]
    )


def format_error_tg(exc: str) -> str:
    low = exc.lower()
    if "notional" in low:
        detalle = "Binance rechazo el monto (minimo suele ser ~$5)"
    elif "usdt" in low or "insufficient" in low or "balance" in low:
        detalle = "faltaba USDT libre en Spot"
    elif "halt" in low:
        detalle = "el trading esta pausado (halt)"
    elif "mandate" in low or "denied" in low or "blocked" in low:
        detalle = "la operacion choco con un limite de seguridad"
    else:
        detalle = (exc or "error desconocido")[:120]
    return "\n".join(
        [
            f"[{VENUE_TAG}] AVISO · no se pudo completar la operacion",
            f"Detalle simple: {detalle}",
            "El bot reintentara en el proximo ciclo. No tienes que hacer nada.",
        ]
    )


def http_get(url: str):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def save_state(doc: dict) -> None:
    STATE_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def ensure_buy_counters(state: dict) -> dict:
    day = utc_day()
    if state.get("buys_date") != day:
        state["buys_date"] = day
        state["buys_today"] = 0
        state["major_buys_today"] = 0
        save_state(state)
    state.setdefault("buys_today", 0)
    state.setdefault("major_buys_today", 0)
    return state


def note_buy(state: dict, base: str) -> None:
    ensure_buy_counters(state)
    state["buys_today"] = int(state.get("buys_today") or 0) + 1
    if base in MAJORS:
        state["major_buys_today"] = int(state.get("major_buys_today") or 0) + 1
    save_state(state)


def sma(xs: list[float], n: int) -> float | None:
    if len(xs) < n:
        return None
    return sum(xs[-n:]) / n


def ema(xs: list[float], n: int) -> float | None:
    if len(xs) < n:
        return None
    k = 2 / (n + 1)
    v = sum(xs[:n]) / n
    for x in xs[n:]:
        v = x * k + v * (1 - k)
    return v


def rsi(closes: list[float], n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = sum(gains[-n:]) / n
    al = sum(losses[-n:]) / n
    if al <= 1e-12:
        return 100.0
    return 100 - (100 / (1 + ag / al))


def atr_pct(klines: list, n: int = 14) -> float:
    if len(klines) < n + 1:
        return 0.02
    trs = []
    for i in range(1, len(klines)):
        h = float(klines[i][2])
        l = float(klines[i][3])
        pc = float(klines[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return (sum(trs[-n:]) / n) / max(float(klines[-1][4]), 1e-12)


def book_equity() -> float:
    """Full Binance book: Spot (+Earn LD*) + Funding USDT + idle Futures USDT."""
    try:
        import binance_wallets as bw

        # Prefer pulling stranded USDT back so usable cash matches equity
        bw.salvage_usdt_to_spot(force=False)
        return float(bw.total_book_equity())
    except Exception as exc:  # noqa: BLE001
        log(f"BOOK_EQUITY_MULTI_FAIL {exc}")
    from src.trading.connectors.binance import sdk as bn

    cfg = bn.load_config()
    acc = bn.get_account_snapshot(cfg)
    total = 0.0
    for b in acc.get("balances", []) or []:
        raw = str(b.get("asset") or "")
        qty = float(b.get("total") or 0)
        if qty <= 0:
            continue
        asset = raw[2:] if raw.startswith("LD") and len(raw) > 2 else raw
        if asset in STABLES:
            total += qty
            continue
        try:
            px = float(http_get(f"https://api.binance.com/api/v3/ticker/price?symbol={asset}USDT")["price"])
        except Exception:
            continue
        total += qty * px
    return total


def free_usdt() -> float:
    try:
        import binance_wallets as bw

        bw.salvage_usdt_to_spot(force=False)
        return float(bw.free_spot_usdt())
    except Exception:
        pass
    from src.trading.connectors.binance import sdk as bn

    cfg = bn.load_config()
    acc = bn.get_account_snapshot(cfg)
    for b in acc.get("balances", []) or []:
        if str(b.get("asset") or "") == "USDT":
            return float(b.get("free") or 0)
    return 0.0


def scalp_reserved_usdt() -> float:
    """ETH scalper retired — never reserve USDT from v6."""
    return 0.0


def usable_usdt_for_v6() -> float:
    return max(0.0, free_usdt() - scalp_reserved_usdt())


def with_order_lock(timeout: float = 45.0):
    """Simple flock-style lock for serialized Binance orders."""
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
                    # stale lock salvage
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


def spot_holdings() -> dict[str, dict[str, float]]:
    from src.trading.connectors.binance import sdk as bn

    cfg = bn.load_config()
    ex = bn._exchange(cfg)
    bal = ex.fetch_balance()
    out: dict[str, dict[str, float]] = {}
    for asset, qty in (bal.get("free") or {}).items():
        a = str(asset)
        q = float(qty or 0)
        if q <= 0 or a in STABLES or a.startswith("LD"):
            continue
        try:
            px = float(http_get(f"https://api.binance.com/api/v3/ticker/price?symbol={a}USDT")["price"])
        except Exception:
            continue
        usd = q * px
        if usd < 0.05:
            continue
        out[a] = {"qty": q, "px": px, "usd": usd}
    return out


def flexible_positions() -> list[dict]:
    from src.trading.connectors.binance import sdk as bn

    cfg = bn.load_config()
    ex = bn._exchange(cfg)
    try:
        data = ex.request("simple-earn/flexible/position", "sapi", "GET", {"size": 100})
    except Exception as exc:  # noqa: BLE001
        log(f"POS_FAIL {exc}")
        return []
    return list(data.get("rows") or [])


def redeem_flexible(asset: str, amount: float | None = None, redeem_all: bool = False) -> bool:
    from src.trading.connectors.binance import sdk as bn

    cfg = bn.load_config()
    ex = bn._exchange(cfg)
    rows = [r for r in flexible_positions() if str(r.get("asset") or "").upper() == asset.upper()]
    if not rows:
        return False
    row = rows[0]
    if not row.get("canRedeem", True):
        log(f"REDEEM_LOCKED {asset}")
        return False
    product_id = row.get("productId")
    avail = float(row.get("totalAmount") or 0)
    if avail <= 0 or not product_id:
        return False
    params: dict = {"productId": product_id}
    if redeem_all or amount is None or float(amount) >= avail * 0.999:
        params["redeemAll"] = True
    else:
        qty = min(float(amount), avail)
        params["amount"] = f"{qty:.8f}".rstrip("0").rstrip(".")
    try:
        ex.request("simple-earn/flexible/redeem", "sapi", "POST", params)
        log(f"REDEEM_OK {asset} {params}")
        time.sleep(5)
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"REDEEM_FAIL {asset} {exc}")
        return False


def dust_to_bnb(only_assets: list[str] | None = None, max_usd: float = 1.0) -> bool:
    from src.trading.connectors.binance import sdk as bn

    cfg = bn.load_config()
    ex = bn._exchange(cfg)
    bal = ex.fetch_balance()
    assets: list[str] = []
    for asset, qty in (bal.get("free") or {}).items():
        a = str(asset)
        if a in ("USDT", "BNB") or a.startswith("LD"):
            continue
        if only_assets is not None and a not in only_assets:
            continue
        if float(qty or 0) <= 0:
            continue
        try:
            px = float(http_get(f"https://api.binance.com/api/v3/ticker/price?symbol={a}USDT")["price"])
            usd = float(qty) * px
        except Exception:
            usd = 0.0
        if usd < 0.001 or usd >= max_usd:
            continue
        assets.append(a)
    if not assets:
        return False
    params = {"timestamp": int(time.time() * 1000), "recvWindow": 60000}
    query = urllib.parse.urlencode(params) + "".join(
        f"&asset={urllib.parse.quote(a)}" for a in assets
    )
    sig = hmac.new(cfg.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"https://api.binance.com/sapi/v1/asset/dust?{query}&signature={sig}"
    req = urllib.request.Request(
        url, data=b"", method="POST", headers={"X-MBX-APIKEY": cfg.api_key}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        log(f"DUST_OK assets={assets} transferred={data.get('totalTransfered')}")
        time.sleep(2)
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"DUST_FAIL {exc}")
        return False


def market_sell_raw(
    asset: str,
    need_usd: float | None = None,
    sell_all: bool = False,
    *,
    state: dict | None = None,
    reason: str = "FUND",
) -> bool:
    """Raw market sell (funding / emergency). Does NOT consume mandate daily count."""
    from src.trading.connectors.binance import sdk as bn

    if asset in _tick_sold or asset in _tick_fund_sold:
        return False
    cfg_bn = bn.load_config()
    ex = bn._exchange(cfg_bn)
    symbol = f"{asset}/USDT"
    try:
        ex.load_markets()
        bal = ex.fetch_balance()
        spot_free = float((bal.get("free") or {}).get(asset) or 0)
        if spot_free <= 0:
            return False
        px = float(http_get(f"https://api.binance.com/api/v3/ticker/price?symbol={asset}USDT")["price"])
        if px <= 0:
            return False
        if sell_all or need_usd is None:
            qty = spot_free
        else:
            qty = min(spot_free, max(need_usd, MIN_EXIT_USD) / px)
            if qty * px < MIN_EXIT_USD * 0.999:
                qty = spot_free
        if spot_free * px < DUST_USD:
            log(f"FUND_SELL_SKIP {asset} dust_usd={spot_free * px:.4f}")
            _tick_fund_sold.add(asset)
            return False
        qty = float(ex.amount_to_precision(symbol, qty))
        try:
            min_amt = float((ex.market(symbol).get("limits") or {}).get("amount", {}).get("min") or 0)
        except Exception:
            min_amt = 0.0
        if min_amt and qty + 1e-12 < min_amt:
            log(f"FUND_SELL_SKIP {asset} qty={qty} < min_amt={min_amt}")
            _tick_fund_sold.add(asset)
            return False
        usd = qty * px
        if qty <= 0 or usd < MIN_EXIT_USD * 0.99:
            log(f"FUND_SELL_SKIP {asset} notional={usd:.4f}")
            _tick_fund_sold.add(asset)
            return False
        order = ex.create_order(symbol, "market", "sell", qty)
        log(f"FUND_SELL {asset} qty={qty} id={order.get('id')} reason={reason}")
        _tick_fund_sold.add(asset)
        _record_raw_sell_event(asset, px=px, usd=usd, state=state, reason=reason)
        time.sleep(2)
        return True
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        log(f"FUND_SELL_FAIL {asset} {msg}")
        if "minimum amount" in msg.lower() or "precision" in msg.lower():
            _tick_fund_sold.add(asset)
        return False


def _record_raw_sell_event(
    asset: str,
    *,
    px: float,
    usd: float,
    state: dict | None,
    reason: str,
) -> None:
    try:
        import market_orchestrator as orch
        import trade_events as te

        pnl_pct = None
        result = None
        meta = {}
        if state:
            meta = (state.get("positions") or {}).get(asset) or {}
            entry = float(meta.get("entry") or 0)
            if entry > 0 and px > 0:
                pnl_pct = (px / entry) - 1.0
            else:
                result = "flat"
                reason = f"{reason}|untracked"
        else:
            result = "flat"
            reason = f"{reason}|untracked"
        te.record_trade_event(
            bot="v6",
            side="sell",
            symbol=asset,
            price=float(px),
            usd=float(usd),
            mode=orch.current_mode(),
            regime=str((state or {}).get("regime") or ""),
            pnl_pct=pnl_pct,
            result=result,
            equity=book_equity(),
            reason=reason,
            kind=reason.split("|")[0][:40],
        )
        if state and asset in (state.get("positions") or {}):
            positions = dict(state.get("positions") or {})
            positions.pop(asset, None)
            state["positions"] = positions
            save_state(state)
    except Exception as exc:  # noqa: BLE001
        log(f"TRADE_EVENT_FAIL fund_sell {exc}")


def _opened_age_sec(meta: dict) -> float | None:
    opened = meta.get("opened_ts")
    if opened is None:
        return None
    try:
        if isinstance(opened, (int, float)):
            ts = float(opened)
            if ts > 1e12:
                ts /= 1000.0
            if ts > 1e9:
                return max(0.0, time.time() - ts)
        raw = str(opened).replace("Z", "+00:00")
        dtp = datetime.fromisoformat(raw)
        if dtp.tzinfo is None:
            dtp = dtp.replace(tzinfo=timezone.utc)
        return max(0.0, time.time() - dtp.timestamp())
    except Exception:
        return None


def _leg_is_young(meta: dict) -> bool:
    age = _opened_age_sec(meta)
    if age is None:
        return False
    return age < float(cfg.YOUNG_LEG_SEC)


def _leg_in_trail(meta: dict, px: float) -> bool:
    entry = float(meta.get("entry") or 0)
    peak = float(meta.get("peak") or entry or 0)
    if entry <= 0 or px <= 0:
        return False
    return peak >= entry * (1 + TRAIL_ACT)


def _fund_sell_allowed(
    asset: str,
    meta: dict,
    h: dict,
    *,
    allow_last_resort: bool,
) -> bool:
    """Whether ensure_spot_usdt may sell this asset at this stage."""
    if asset in _tick_sold or asset in _tick_fund_sold:
        return False
    usd = float(h.get("usd") or 0)
    if usd < MIN_EXIT_USD:
        return False
    if not meta:
        return True  # idle / untracked
    px = float(h.get("px") or 0)
    entry = float(meta.get("entry") or 0)
    pnl = (px / entry - 1.0) if entry > 0 and px > 0 else 0.0
    if _leg_is_young(meta) or _leg_in_trail(meta, px):
        return False
    age = _opened_age_sec(meta)
    aged_ok = age is not None and age >= COOLDOWN_HOURS * 3600
    loss_ok = pnl <= -(SL * 0.5)
    if aged_ok or loss_ok:
        return True
    return bool(allow_last_resort)


def ensure_spot_usdt(
    need: float,
    state: dict,
    *,
    allow_last_resort: bool = False,
) -> float:
    """Fund USDT without dumping young/trail strategy legs when possible."""
    free = usable_usdt_for_v6()
    if free >= need:
        return free_usdt()

    hard_protect = {
        a
        for a, meta in (state.get("positions") or {}).items()
        if float((meta or {}).get("usd") or 0) >= MIN_EXIT_USD
    }

    # 1) Earn USDT
    for row in flexible_positions():
        if str(row.get("asset") or "").upper() == "USDT":
            redeem_flexible("USDT", redeem_all=True)
            break
    free = usable_usdt_for_v6()
    if free >= need:
        return free_usdt()

    # 2) Dust then BNB if not hard-protected young
    dust_to_bnb(max_usd=1.0)
    bnb_meta = (state.get("positions") or {}).get("BNB") or {}
    if "BNB" not in hard_protect or _fund_sell_allowed(
        "BNB", bnb_meta, {"usd": 99, "px": 1}, allow_last_resort=False
    ):
        # only sell BNB if not a young/trail strategy leg
        if "BNB" not in hard_protect or (
            not _leg_is_young(bnb_meta)
            and not _leg_in_trail(bnb_meta, float(bnb_meta.get("peak") or 0) or 1.0)
        ):
            market_sell_raw(
                "BNB",
                need_usd=need - usable_usdt_for_v6(),
                state=state,
                reason="FUND_BNB",
            )
    free = usable_usdt_for_v6()
    if free >= need:
        return free_usdt()

    # 3) Redeem earn then sell idle / aged tracked
    for asset in ("FDUSD",) + FUND_ASSETS + ("BTC", "BNB", "ETH"):
        free = usable_usdt_for_v6()
        if free >= need:
            return free_usdt()
        redeem_flexible(asset, redeem_all=True)

    holdings = spot_holdings()
    # Prefer untracked first
    for asset, h in sorted(holdings.items(), key=lambda kv: kv[1]["usd"]):
        free = usable_usdt_for_v6()
        if free >= need:
            return free_usdt()
        meta = (state.get("positions") or {}).get(asset) or {}
        if meta and float(meta.get("usd") or h["usd"] or 0) >= MIN_EXIT_USD:
            continue  # tracked — second pass
        if h["usd"] < MIN_EXIT_USD:
            continue
        market_sell_raw(
            asset,
            need_usd=need - usable_usdt_for_v6(),
            state=state,
            reason="FUND_IDLE",
        )

    # Tracked aged / mid-loss (never young/trail)
    for asset, h in sorted(holdings.items(), key=lambda kv: kv[1]["usd"]):
        free = usable_usdt_for_v6()
        if free >= need:
            return free_usdt()
        meta = (state.get("positions") or {}).get(asset) or {}
        if not meta:
            continue
        if not _fund_sell_allowed(asset, meta, h, allow_last_resort=False):
            continue
        market_sell_raw(
            asset,
            need_usd=need - usable_usdt_for_v6(),
            state=state,
            reason="FUND_AGED",
        )

    free = usable_usdt_for_v6()
    if free >= need:
        return free_usdt()

    # 4) Last resort only when explicitly allowed (recap / usable stuck)
    if allow_last_resort:
        for asset in list(hard_protect):
            free = usable_usdt_for_v6()
            if free >= need:
                break
            meta = (state.get("positions") or {}).get(asset) or {}
            h = holdings.get(asset) or spot_holdings().get(asset)
            if not h:
                continue
            if _leg_is_young(meta) or _leg_in_trail(meta, float(h.get("px") or 0)):
                log(f"FUND_SELL_LAST_RESORT_SKIP {asset} young_or_trail")
                continue
            log(f"FUND_SELL_LAST_RESORT {asset}")
            market_sell_raw(
                asset,
                need_usd=need - usable_usdt_for_v6(),
                state=state,
                reason="FUND_LAST_RESORT",
            )

    return free_usdt()


def detect_regime() -> dict[str, Any]:
    kl = http_get("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=80")
    closes = [float(c[4]) for c in kl]
    t = http_get("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT")
    chg24 = float(t["priceChangePercent"])
    e12, e26, s50 = ema(closes, 12), ema(closes, 26), sma(closes, 50)
    last = closes[-1]
    atr = atr_pct(kl)
    if e12 is None or e26 is None or s50 is None:
        return {"regime": "chop", "btc_chg24": chg24, "atr": atr, "last": last}
    bullish = e12 > e26 and last > s50 and chg24 > -1.5
    bearish = e12 < e26 and last < s50 and chg24 < -1.0
    if bullish:
        regime = "bull"
    elif bearish:
        regime = "bear"
    else:
        regime = "chop"
    return {"regime": regime, "btc_chg24": chg24, "atr": atr, "last": last}


def analyze_symbol(symbol: str, btc_chg24: float, regime: str) -> dict[str, Any] | None:
    try:
        t = http_get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}")
        kl1h = http_get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=60")
        kl15 = http_get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=60")
    except Exception:
        return None
    closes = [float(c[4]) for c in kl1h]
    c15 = [float(c[4]) for c in kl15]
    last = float(t["lastPrice"])
    chg = float(t["priceChangePercent"])
    vol = float(t["quoteVolume"])
    r1 = rsi(closes, 14)
    r15 = rsi(c15, 14)
    e12, e26 = ema(closes, 12), ema(closes, 26)
    s20 = sma(closes, 20)
    atr = atr_pct(kl1h)
    rs = chg - btc_chg24
    vols = [float(c[5]) for c in kl1h[-20:]]
    v_avg = sum(vols[:-1]) / max(len(vols) - 1, 1)
    v_now = vols[-1] if vols else 0
    vol_z = (v_now / v_avg) if v_avg > 0 else 1.0

    score = 0.0
    reasons: list[str] = []
    score += min(vol / 8e8, 1.2)

    if regime == "bull":
        if 38 <= r1 <= 55 and last >= (s20 or last):
            score += 1.6
            reasons.append("bull_dip")
        if rs > 1.0 and r1 < 68 and e12 and e26 and e12 >= e26:
            score += 1.4
            reasons.append("rs_leader")
        if chg > 0 and r15 < 62:
            score += 0.5
    elif regime == "bear":
        if r1 <= 32 and rs > -2.5:
            score += 1.8
            reasons.append("bear_oversold")
        if r15 <= 30 and chg > -8:
            score += 0.7
        score -= 0.6
    else:
        if 35 <= r1 <= 48:
            score += 1.5
            reasons.append("chop_mr")
        if abs(rs) < 0.8 and 40 <= r1 <= 55:
            score += 0.4
        if r1 > 65:
            score -= 1.0

    if e12 and e26:
        if e12 > e26:
            score += 0.55 if regime != "bear" else 0.15
        else:
            score -= 0.35 if regime == "bear" else 0.15

    if vol_z >= 1.35:
        score += 0.45
        reasons.append("vol_exp")
    elif vol_z < 0.7:
        score -= 0.25

    if 0.008 <= atr <= 0.035:
        score += 0.35
    elif atr > 0.055:
        score -= 0.7

    base = symbol[:-4]
    if base in MAJORS and regime == "bull":
        score += 0.25
    if base not in MAJORS and regime == "bull" and rs > 2.0:
        score += 0.55
        reasons.append("alt_beta")

    if r1 >= 72 and regime != "bear":
        return None
    if chg < -9:
        return None

    return {
        "symbol": symbol,
        "base": base,
        "last": last,
        "chg": chg,
        "rsi": r1,
        "rs": rs,
        "atr": atr,
        "score": score,
        "reasons": reasons,
    }


def rank_candidates(regime_info: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in UNIVERSE:
        row = analyze_symbol(s, float(regime_info["btc_chg24"]), regime_info["regime"])
        if row:
            rows.append(row)
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


def parse_opened_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def sync_positions(state: dict) -> dict[str, dict[str, float]]:
    holdings = spot_holdings()
    positions = dict(state.get("positions") or {})
    for asset in list(positions.keys()):
        prev = positions.get(asset) or {}
        if asset not in holdings or holdings[asset]["usd"] < 0.4:
            # Leg vanished without execute_sell — audit it
            try:
                import market_orchestrator as orch
                import trade_events as te

                entry = float(prev.get("entry") or 0)
                last_usd = float(prev.get("usd") or 0)
                px = entry if entry > 0 else 0.0
                pnl_pct = None
                result = "unknown"
                if asset in holdings and holdings[asset]["usd"] < 0.4 and entry > 0:
                    px = float(holdings[asset].get("px") or entry)
                    pnl_pct = (px / entry) - 1.0
                    result = None  # derive from pnl
                te.record_trade_event(
                    bot="v6",
                    side="sell",
                    symbol=asset,
                    price=float(px or 0),
                    usd=float(last_usd or 0),
                    mode=orch.current_mode(),
                    regime=str(state.get("regime") or ""),
                    pnl_pct=pnl_pct,
                    result=result,
                    equity=book_equity(),
                    reason="SYNC_VANISH",
                    kind="SYNC",
                )
            except Exception as exc:  # noqa: BLE001
                log(f"TRADE_EVENT_FAIL sync_vanish {asset} {exc}")
            positions.pop(asset, None)
    for asset, h in holdings.items():
        prev = positions.get(asset) or {}
        entry = float(prev.get("entry") or 0)
        peak = float(prev.get("peak") or 0)
        if entry <= 0:
            entry = h["px"]
        peak = max(peak, h["px"], entry)
        positions[asset] = {
            "entry": entry,
            "peak": peak,
            "qty": h["qty"],
            "usd": round(h["usd"], 4),
            "opened_ts": prev.get("opened_ts") or utc_ts(),
            "score": prev.get("score"),
            "regime": prev.get("regime"),
        }
    state["positions"] = positions
    save_state(state)
    return holdings


def fill_average(result: dict, fallback: float) -> float:
    for key in ("average", "avgPrice", "price"):
        try:
            v = float(result.get(key) or 0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    info = result.get("info") or {}
    if isinstance(info, dict):
        for key in ("avgPrice", "price", "cummulativeQuoteQty"):
            try:
                v = float(info.get(key) or 0)
                if key == "cummulativeQuoteQty":
                    filled = float(info.get("executedQty") or 0)
                    if filled > 0 and v > 0:
                        return v / filled
                elif v > 0:
                    return v
            except (TypeError, ValueError):
                pass
    return fallback


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
    outcome = str((result.get("live_action") or {}).get("outcome") or "").lower()
    if outcome in ("error", "rejected", "denied"):
        return False
    return status in ("closed", "filled", "ok", "open") or bool(result.get("id"))


def place_gate_order(
    pair: str,
    side: str,
    *,
    notional: float | None = None,
    quantity: float | None = None,
) -> dict:
    from src.trading.connectors.binance import sdk as bn
    from src.live.enforcement import OrderIntent
    from src.live.mandate.model import InstrumentType, AssetClass
    from src.live.sdk_order_gate import execute_live_order

    cfg = bn.load_config()
    notional_usd = notional
    if notional_usd is None and quantity is not None:
        base = pair.split("/")[0]
        px = float(http_get(f"https://api.binance.com/api/v3/ticker/price?symbol={base}USDT")["price"])
        notional_usd = quantity * px
    intent = OrderIntent(
        symbol=pair,
        side=side,
        notional_usd=float(notional_usd or 0),
        quantity=quantity,
        instrument_type=InstrumentType.CRYPTO,
        asset_class=AssetClass.CRYPTO,
    )
    kwargs: dict[str, Any] = {"symbol": pair, "side": side, "order_type": "market"}
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
            session_id="autotrade-smart-fast-v6",
        )


def in_cooldown(state: dict, asset: str, *, allow_topup: bool = False) -> bool:
    if allow_topup:
        return False
    cd = (state.get("cooldowns") or {}).get(asset)
    if not cd:
        return False
    try:
        until = datetime.strptime(cd, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return utc_now() < until


def set_cooldown(state: dict, asset: str) -> None:
    cds = dict(state.get("cooldowns") or {})
    until = utc_now().timestamp() + COOLDOWN_HOURS * 3600
    cds[asset] = datetime.fromtimestamp(until, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["cooldowns"] = cds
    save_state(state)


MIN_HOLD_MINUTES = 25.0  # agent: no exit except SL before this


def classify_exit(
    asset: str,
    h: dict[str, float],
    meta: dict,
    *,
    rotate_down: bool = False,
) -> dict[str, Any] | None:
    if h["usd"] < MIN_EXIT_USD * 0.99:
        return None
    entry = float(meta.get("entry") or h["px"])
    peak = max(float(meta.get("peak") or entry), h["px"])
    pnl = (h["px"] / entry) - 1.0 if entry > 0 else 0.0
    dd_from_peak = (h["px"] / peak) - 1.0 if peak > 0 else 0.0
    opened = parse_opened_ts(meta.get("opened_ts"))
    age_h = (utc_now() - opened).total_seconds() / 3600.0 if opened else 0.0
    age_m = age_h * 60.0

    reason = None
    kind = None  # sl|tp|trail|time|rotate
    if pnl <= -SL:
        reason, kind = f"SL {pnl*100:.1f}%", "sl"
    elif age_m < MIN_HOLD_MINUTES:
        # hold gate: only hard SL may exit early
        return None
    elif pnl >= TP:
        reason, kind = f"TP {pnl*100:.1f}%", "tp"
    elif peak >= entry * (1 + TRAIL_ACT) and dd_from_peak <= -TRAIL_GB:
        reason, kind = f"TRAIL {pnl*100:.1f}%", "trail"
    elif age_h >= TIME_STOP_HOURS and pnl < 0.015:
        reason, kind = f"TIME {age_h:.1f}h", "time"
    elif rotate_down:
        reason, kind = f"ROTATE {pnl*100:.1f}%", "rotate"

    if not reason:
        return None
    return {
        "asset": asset,
        "qty": h["qty"],
        "usd": h["usd"],
        "pnl": pnl,
        "reason": reason,
        "kind": kind,
        "peak": peak,
        "entry": entry,
    }


def pick_exit(
    state: dict,
    holdings: dict[str, dict[str, float]],
    *,
    rotate_down: bool = False,
    emergency_only: bool = False,
) -> dict[str, Any] | None:
    positions = state.get("positions") or {}
    cands: list[dict[str, Any]] = []
    for asset, h in holdings.items():
        if asset in _tick_sold:
            continue
        meta = positions.get(asset) or {}
        # persist peak
        if meta:
            meta["peak"] = max(float(meta.get("peak") or 0), h["px"])
            meta["usd"] = h["usd"]
            meta["qty"] = h["qty"]
            positions[asset] = meta
        exit_c = classify_exit(asset, h, meta, rotate_down=rotate_down)
        if not exit_c:
            continue
        if emergency_only and exit_c["kind"] not in ("tp", "sl"):
            continue
        # priority rank
        rank = {"sl": 4, "tp": 3, "trail": 2, "time": 1, "rotate": 0}.get(exit_c["kind"], 0)
        # for rotate, prefer worst pnl
        score = rank * 10 - (exit_c["pnl"] if exit_c["kind"] == "rotate" else 0)
        exit_c["score"] = score
        cands.append(exit_c)
    state["positions"] = positions
    save_state(state)
    if not cands:
        return None
    cands.sort(key=lambda x: x["score"], reverse=True)
    return cands[0]


def precision_ok(pair: str, qty: float, px: float) -> float | None:
    from src.trading.connectors.binance import sdk as bn

    cfg = bn.load_config()
    ex = bn._exchange(cfg)
    ex.load_markets()
    q = float(ex.amount_to_precision(pair, qty))
    if q <= 0 or q * px < MIN_EXIT_USD * 0.99:
        return None
    return q


def execute_sell(
    state: dict,
    exit_c: dict[str, Any],
    *,
    emergency: bool,
) -> bool:
    asset = exit_c["asset"]
    if asset in _tick_sold or asset in _tick_fund_sold:
        return False

    # refetch
    holdings = spot_holdings()
    if asset not in holdings:
        return False
    h = holdings[asset]
    pair = f"{asset}/USDT"
    qty = precision_ok(pair, h["qty"], h["px"])
    if qty is None:
        log(f"EXIT_SKIP {asset} notional after precision < {MIN_EXIT_USD}")
        return False

    tag = "EMERGENCY_EXIT" if emergency else "STRATEGY_SELL"

    log(f"{tag} {pair} qty={qty} {exit_c['reason']} pnl={exit_c['pnl']*100:.2f}%")
    if emergency:
        ok = market_sell_raw(asset, sell_all=True, state=state, reason="EMERGENCY")
        # market_sell_raw logs FUND_SELL; rewrite intent in log already via tag above
        result = {"status": "ok"} if ok else {"status": "error"}
        # avoid double fund tag confusion
        if ok:
            log(f"EMERGENCY_EXIT_OK {asset}")
            _tick_sold.add(asset)
            # market_sell_raw already recorded trade event + popped position
            set_cooldown(state, asset)
            state["last_symbol"] = f"-{asset}"
            recent = list(state.get("recent_symbols") or [])
            recent.append(f"-{asset}")
            state["recent_symbols"] = recent[-8:]
            exits = list(state.get("exits") or [])
            exits.append(
                {
                    "ts": utc_ts(),
                    "asset": asset,
                    "reason": exit_c["reason"],
                    "kind": exit_c["kind"],
                    "pnl_pct": round(exit_c["pnl"] * 100, 2),
                    "emergency": emergency,
                }
            )
            state["exits"] = exits[-30:]
            save_state(state)
            tg(
                format_sell_tg(
                    base=asset,
                    usd=float(exit_c.get("usd") or h["usd"]),
                    pnl_pct=float(exit_c["pnl"]),
                    kind=str(exit_c.get("kind") or ""),
                    emergency=emergency,
                    state=state,
                )
            )
            return True
        return False
    else:
        result = place_gate_order(pair, "sell", quantity=qty)

    status = result.get("status") or result.get("denied_reason") or result.get("error") or result
    log(f"{tag}_RESULT {pair} {status}")
    if not order_succeeded(result):
        return False

    _tick_sold.add(asset)
    positions = dict(state.get("positions") or {})
    positions.pop(asset, None)
    state["positions"] = positions
    set_cooldown(state, asset)
    state["last_symbol"] = f"-{asset}"
    recent = list(state.get("recent_symbols") or [])
    recent.append(f"-{asset}")
    state["recent_symbols"] = recent[-8:]
    exits = list(state.get("exits") or [])
    exits.append(
        {
            "ts": utc_ts(),
            "asset": asset,
            "reason": exit_c["reason"],
            "kind": exit_c["kind"],
            "pnl_pct": round(exit_c["pnl"] * 100, 2),
            "emergency": emergency,
        }
    )
    state["exits"] = exits[-30:]
    save_state(state)
    try:
        import market_orchestrator as orch
        import trade_events as te

        te.record_trade_event(
            bot="v6",
            side="sell",
            symbol=asset,
            price=float(h["px"]),
            usd=float(exit_c.get("usd") or h["usd"]),
            mode=orch.current_mode(),
            regime=str(state.get("regime") or ""),
            pnl_pct=float(exit_c["pnl"]),
            equity=book_equity(),
            reason=str(exit_c.get("reason") or ""),
            kind=str(exit_c.get("kind") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        log(f"TRADE_EVENT_FAIL sell {exc}")
    tg(
        format_sell_tg(
            base=asset,
            usd=float(exit_c.get("usd") or h["usd"]),
            pnl_pct=float(exit_c["pnl"]),
            kind=str(exit_c.get("kind") or ""),
            emergency=emergency,
            state=state,
        )
    )
    return True


def try_exits(state: dict) -> bool:
    from src.live.daily_count import read_daily_count
    from src.live.halt import halt_flag_set

    if halt_flag_set("binance") or halt_flag_set(None):
        log("HALTED")
        return False

    holdings = sync_positions(state)
    positions = state.get("positions") or {}
    tracked_legs = sum(
        1
        for a, h in holdings.items()
        if h["usd"] >= 0.5 and (a in positions or h["usd"] >= MIN_EXIT_USD)
    )
    daily = read_daily_count("binance")
    rotate = tracked_legs > MAX_OPEN_LEGS and daily < 3

    if daily < 3:
        exit_c = pick_exit(state, holdings, rotate_down=rotate, emergency_only=False)
        if not exit_c:
            return False
        return execute_sell(state, exit_c, emergency=False)

    # day full: emergency TP/SL only
    exit_c = pick_exit(state, holdings, rotate_down=False, emergency_only=True)
    if not exit_c:
        return False
    return execute_sell(state, exit_c, emergency=True)


def try_consolidate(state: dict) -> bool:
    """Top-up undersize legs via gate buy; dust true dust."""
    from src.live.daily_count import read_daily_count
    from src.live.halt import halt_flag_set

    if halt_flag_set("binance") or halt_flag_set(None):
        return False
    try:
        import market_orchestrator as orch

        if not orch.allows_v6_buys():
            return False
    except Exception:
        pass
    ensure_buy_counters(state)
    daily = read_daily_count("binance")
    if daily >= 3 or int(state.get("buys_today") or 0) >= MAX_BUYS_PER_DAY:
        # still allow dust
        dust_to_bnb(max_usd=1.0)
        return False

    holdings = sync_positions(state)
    undersize = [
        (a, h)
        for a, h in holdings.items()
        if 0.5 <= h["usd"] < MIN_EXIT_USD
    ]
    if not undersize:
        dust_to_bnb(max_usd=1.0)
        return False

    # Prefer topping the largest undersize first
    undersize.sort(key=lambda x: x[1]["usd"], reverse=True)
    asset, h = undersize[0]
    # cooldown does NOT block top-up
    free = ensure_spot_usdt(ORDER_USD, state)
    if free < MIN_USDT:
        log(f"CONSOLIDATE_NO_USDT free={free:.4f}")
        dust_to_bnb(max_usd=1.0)
        return False

    pair = f"{asset}/USDT"
    log(f"STRATEGY_BUY TOPUP {pair} ${ORDER_USD:.2f} from_usd={h['usd']:.2f}")
    result = place_gate_order(pair, "buy", notional=ORDER_USD)
    status = result.get("status") or result.get("error") or result
    log(f"STRATEGY_BUY_RESULT {pair} {status}")
    if not order_succeeded(result):
        return False

    avg = fill_average(result, h["px"])
    meta = dict((state.get("positions") or {}).get(asset) or {})
    old_entry = float(meta.get("entry") or h["px"])
    old_usd = float(meta.get("usd") or h["usd"])
    new_usd = old_usd + ORDER_USD
    # VWAP-ish entry
    entry = (old_entry * old_usd + avg * ORDER_USD) / max(new_usd, 1e-9)
    meta.update(
        {
            "entry": entry,
            "peak": max(float(meta.get("peak") or 0), avg, h["px"]),
            "opened_ts": meta.get("opened_ts") or utc_ts(),
            "usd": new_usd,
        }
    )
    positions = dict(state.get("positions") or {})
    positions[asset] = meta
    state["positions"] = positions
    note_buy(state, asset)
    state["last_symbol"] = asset
    save_state(state)
    sync_positions(state)
    tg(
        format_buy_tg(
            base=asset,
            usd=ORDER_USD,
            topup=True,
            state=state,
        )
    )
    return True


def select_buy(
    ranked: list[dict[str, Any]],
    state: dict,
    regime: str,
    holdings: dict[str, dict[str, float]],
) -> dict[str, Any] | None:
    ensure_buy_counters(state)
    if int(state.get("buys_today") or 0) >= MAX_BUYS_PER_DAY:
        return None
    min_score = MIN_BUY_SCORE_BEAR if regime == "bear" else MIN_BUY_SCORE
    open_legs = sum(1 for a, h in holdings.items() if h["usd"] >= MIN_EXIT_USD * 0.9)
    if open_legs >= MAX_OPEN_LEGS:
        log(f"SKIP_BUY max open legs={open_legs}")
        return None
    major_buys = int(state.get("major_buys_today") or 0)
    held_majors = sum(1 for a in holdings if a in MAJORS and holdings[a]["usd"] >= 3)

    for row in ranked:
        if row["score"] < min_score:
            continue
        base = row["base"]
        if in_cooldown(state, base):
            continue
        if base in holdings and holdings[base]["usd"] >= 3.0:
            continue
        if base in MAJORS and major_buys >= MAX_MAJOR_BUYS_DAY:
            continue
        if held_majors >= 2 and base in MAJORS:
            continue
        if regime == "bear" and base not in ("BTC", "ETH") and row["score"] < min_score + 0.3:
            continue
        return row
    return None


def _trace_skip(reason: str, **fields: Any) -> None:
    cur = v6_trace.current()
    if cur is not None:
        cur.skip(reason, **fields)


def try_buy(state: dict, regime_info: dict[str, Any]) -> bool:
    from src.live.daily_count import read_daily_count
    from src.live.halt import halt_flag_set
    from src.live.mandate.store import load_mandate

    import market_orchestrator as orch
    import trade_events as te

    if halt_flag_set("binance") or halt_flag_set(None):
        log("HALTED")
        _trace_skip("halt")
        return False
    mode = orch.current_mode()
    mode_doc = orch.load_mode()
    feats = mode_doc.get("features") or {}
    mode_reason = str(mode_doc.get("reason") or "")
    eq_feats = float(feats.get("equity") or 0) or float(book_equity() or 0)
    fee_lim = float(feats.get("fee_limit") or cfg.fee_notional_limit(eq_feats))

    if not orch.allows_v6_buys(mode):
        if float(feats.get("notional_frac") or 0) >= fee_lim:
            te.record_skip(
                "SKIP_FEE_BUDGET",
                bot="v6",
                detail=f"frac={feats.get('notional_frac')} lim={fee_lim}",
                mode=mode,
            )
            _trace_skip("fee_budget", mode=mode, frac=feats.get("notional_frac"))
        else:
            te.record_skip("SKIP_MODE", bot="v6", detail=f"mode={mode}", mode=mode)
            _trace_skip("orch_mode", mode=mode)
        log(f"SKIP_MODE buy blocked mode={mode}")
        return False

    # Win-rate tilt brake (no forced quota)
    wr, wins, losses_d, rated = te.win_rate_today(bot="v6")
    if (
        rated >= int(cfg.MIN_CLOSES_FOR_WINRATE)
        and wr is not None
        and wr < float(cfg.MIN_WIN_RATE_CONTINUE)
    ):
        te.record_skip(
            "SKIP_DAY_EDGE",
            bot="v6",
            detail=f"wr={wr:.2f} wins={wins} losses={losses_d} rated={rated}",
            mode=mode,
        )
        _trace_skip("day_edge_fail", wr=wr, rated=rated)
        log(f"SKIP_DAY_EDGE wr={wr:.2f} rated={rated}")
        return False

    # Soft / grace: at most one clip while those reasons are active
    one_clip = (
        "grace_1clip" in mode_reason
        or "fee_budget_soft" in mode_reason
        or "allow_one_clip" in mode_reason
    )
    if eq_feats > 0 and eq_feats < float(cfg.MIN_EQUITY_RECHARGE):
        one_clip = True
        if int(state.get("buys_today") or 0) >= int(cfg.GRACE_MAX_BUYS):
            te.record_skip(
                "SKIP_GRACE_FULL",
                bot="v6",
                detail=f"equity={eq_feats:.2f} buys={state.get('buys_today')}",
                mode=mode,
            )
            log(f"SKIP_GRACE_FULL buys={state.get('buys_today')}")
            return False
    if one_clip and int(state.get("buys_today") or 0) >= 1:
        te.record_skip(
            "SKIP_ONE_CLIP",
            bot="v6",
            detail=mode_reason[:120],
            mode=mode,
        )
        log("SKIP_ONE_CLIP already used today under soft/grace mode")
        return False

    mandate = load_mandate("binance")
    if mandate is None:
        log("NO_MANDATE")
        _trace_skip("no_mandate")
        return False
    ensure_buy_counters(state)
    daily = read_daily_count("binance")
    cap = mandate.hard_caps.max_trades_per_day
    if daily >= cap:
        log(f"DAY_FULL {daily}/{cap}")
        _trace_skip("day_full", daily=daily, cap=cap)
        return False
    if int(state.get("buys_today") or 0) >= MAX_BUYS_PER_DAY:
        log(f"BUYS_FULL {state['buys_today']}/{MAX_BUYS_PER_DAY}")
        _trace_skip("buys_full", buys=state.get("buys_today"))
        return False

    notional = min(ORDER_USD, float(mandate.hard_caps.max_order_notional_usd))
    holdings = sync_positions(state)
    ranked = rank_candidates(regime_info)
    if ranked:
        top = ranked[0]
        log(
            f"RANK1 {top['base']} score={top['score']:.2f} rsi={top['rsi']:.0f} "
            f"rs={top['rs']:+.1f} {','.join(top['reasons']) or '-'}"
        )
        cur = v6_trace.current()
        if cur is not None:
            cur.phase(
                "rank1",
                symbol=top["base"],
                score=round(float(top["score"]), 2),
                reasons=",".join(top["reasons"]) or "-",
            )
    pick = select_buy(ranked, state, regime_info["regime"], holdings)
    if not pick:
        te.record_skip(
            "SKIP_SCORE",
            bot="v6",
            detail=f"regime={regime_info['regime']}",
            mode=mode,
        )
        _trace_skip("score", regime=regime_info["regime"])
        log(f"NO_QUALITY_BUY regime={regime_info['regime']} (cash ok)")
        return False

    # Don't burn fee/funding attempts when usable USDT is empty after scalper reserve
    if usable_usdt_for_v6() < MIN_USDT * 0.5:
        free = ensure_spot_usdt(notional, state)
        if usable_usdt_for_v6() < MIN_USDT:
            te.record_skip(
                "SKIP_NO_BANKROLL",
                bot="v6",
                detail=f"usable={usable_usdt_for_v6():.2f}",
                mode=mode,
            )
            _trace_skip("no_bankroll", usable=round(usable_usdt_for_v6(), 4))
            log(f"NO_USDT usable={usable_usdt_for_v6():.4f} free={free:.4f}")
            return False
    else:
        free = ensure_spot_usdt(notional, state)
        if free < MIN_USDT and usable_usdt_for_v6() < MIN_USDT:
            te.record_skip(
                "SKIP_NO_BANKROLL",
                bot="v6",
                detail=f"free={free:.2f}",
                mode=mode,
            )
            _trace_skip("no_bankroll", free=round(free, 4))
            log(f"NO_USDT free={free:.4f}")
            return False

    if usable_usdt_for_v6() < MIN_USDT:
        te.record_skip(
            "SKIP_NO_BANKROLL",
            bot="v6",
            detail=f"usable={usable_usdt_for_v6():.2f}",
            mode=mode,
        )
        _trace_skip("no_bankroll", usable=round(usable_usdt_for_v6(), 4))
        log(f"SKIP_BUY low_usable_usdt={usable_usdt_for_v6():.4f}")
        return False

    pair = f"{pick['base']}/USDT"
    cur = v6_trace.current()
    if cur is not None:
        cur.decide(
            "BUY",
            reason="placing",
            symbol=pick["base"],
            score=round(float(pick["score"]), 2),
            notional=notional,
            mode=mode,
        )
    log(
        f"STRATEGY_BUY {pair} ${notional:.2f} score={pick['score']:.2f} "
        f"regime={regime_info['regime']} mode={mode} reasons={pick['reasons']}"
    )
    result = place_gate_order(pair, "buy", notional=notional)
    status = result.get("status") or result.get("denied_reason") or result.get("error") or result
    log(f"STRATEGY_BUY_RESULT {pair} {status}")
    if not order_succeeded(result):
        return False

    avg = fill_average(result, pick["last"])
    positions = dict(state.get("positions") or {})
    positions[pick["base"]] = {
        "entry": avg,
        "peak": avg,
        "qty": 0,
        "usd": notional,
        "opened_ts": utc_ts(),
        "score": pick["score"],
        "regime": regime_info["regime"],
    }
    state["positions"] = positions
    note_buy(state, pick["base"])
    state["last_symbol"] = pick["base"]
    recent = list(state.get("recent_symbols") or [])
    recent.append(pick["base"])
    state["recent_symbols"] = recent[-8:]
    save_state(state)
    sync_positions(state)
    try:
        te.record_trade_event(
            bot="v6",
            side="buy",
            symbol=pick["base"],
            price=float(avg),
            usd=float(notional),
            mode=mode,
            regime=str(regime_info.get("regime") or ""),
            equity=book_equity(),
        )
    except Exception as exc:  # noqa: BLE001
        log(f"TRADE_EVENT_FAIL buy {exc}")
    tg(
        format_buy_tg(
            base=pick["base"],
            usd=notional,
            topup=False,
            state=state,
        )
    )
    return True


def check_double(state: dict) -> None:
    eq = book_equity()
    goals.ensure_goals(state, eq, venue="binance")
    state["equity"] = round(eq, 4)
    snap = goals.goal_snapshot(state, eq)
    state["goal_snap"] = snap
    save_state(state)
    log(
        f"GOALS day={snap['day_pnl']:+.2f}/{snap['daily_target']:.2f} "
        f"({snap['daily_prog']:.0f}%) week={snap['week_pnl']:+.2f}/{snap['weekly_target']:.2f} "
        f"profile={snap['profile']}"
    )
    for event in goals.evaluate_hits(state, eq):
        save_state(state)
        try:
            hist = Path("/root/.vibe-trading/equity_history.json")
            equity_chart.record_goal_marker(
                hist,
                kind=str(event["kind"]),
                equity=eq,
                label="Meta dia" if event["kind"] == "daily" else "Meta semana",
            )
        except Exception as exc:  # noqa: BLE001
            log(f"GOAL_MARKER_FAIL {exc}")
        # Single bubble: text + chart (via tg → build_and_send)
        tg(goals.format_goal_hit_tg("Binance", event, eq))
        log(f"GOAL_HIT {event['kind']} pnl={event['pnl']:.2f}")
    base = float(state.get("double_baseline") or 0)
    if base <= 0:
        state["double_baseline"] = round(eq, 4)
        save_state(state)
        log(f"BASELINE_SET {eq:.4f}")
        return
    state["progress_to_double"] = round(eq / base, 4) if base else 0
    save_state(state)
    if eq >= base * 2:
        tg(format_double_tg(old_base=base, new_eq=eq))
        state["double_baseline"] = round(eq, 4)
        state["doubles"] = int(state.get("doubles") or 0) + 1
        save_state(state)
        log(f"DOUBLED soft -> new baseline {eq:.4f}")


def has_exitable_leg(state: dict) -> bool:
    holdings = spot_holdings()
    return any(h["usd"] >= MIN_EXIT_USD for h in holdings.values())


def _orch_mode() -> str:
    try:
        import market_orchestrator as orch

        return str(orch.current_mode())
    except Exception:  # noqa: BLE001
        return "?"


def tick() -> None:
    global _tick_sold, _tick_fund_sold
    _tick_sold = set()
    _tick_fund_sold = set()
    refresh_knobs_from_overlay()

    from src.live.daily_count import read_daily_count
    from src.live.halt import halt_flag_set
    from src.live.mandate.store import load_mandate

    with v6_trace.cycle(log_fn=log, strategy=STRATEGY_TAG, prompt=cfg.PROMPT_VERSION) as tr:
        phase = "preflight"
        tr.phase(phase)

        state = load_state()
        state["strategy"] = STRATEGY_TAG
        ensure_buy_counters(state)
        save_state(state)

        if halt_flag_set("binance") or halt_flag_set(None):
            tr.skip("halt")
            tr.end(made=0, note="halted")
            log("HALTED skip tick")
            return

        phase = "ops_intents"
        tr.phase(phase)
        intent_made = process_ops_intents(state)
        if intent_made:
            tr.decide("OPS_INTENT", reason=f"made={intent_made}")
            state = load_state()

        phase = "orch"
        tr.phase(phase)
        try:
            import market_orchestrator as orch

            orch.evaluate_and_update(notify=True)
        except Exception as exc:  # noqa: BLE001
            log(f"ORCH_FAIL {exc}")
            tr.phase("orch_fail", exc=str(exc)[:200])

        phase = "double"
        tr.phase(phase)
        check_double(state)
        mandate = load_mandate("binance")
        cap = mandate.hard_caps.max_trades_per_day if mandate else 3

        phase = "regime"
        tr.phase(phase)
        try:
            regime_info = detect_regime()
        except Exception as exc:  # noqa: BLE001
            log(f"REGIME_FAIL {exc}")
            regime_info = {"regime": "chop", "btc_chg24": 0.0, "atr": 0.02}
            tr.phase("regime_fail", exc=str(exc)[:200])

        state["regime"] = regime_info["regime"]
        save_state(state)
        log(
            f"REGIME {regime_info['regime']} btc24={regime_info['btc_chg24']:+.2f}% "
            f"buys={state.get('buys_today', 0)}/{MAX_BUYS_PER_DAY} "
            f"daily={read_daily_count('binance')}/{cap}"
        )
        log(f"KNOBS {cfg.knobs_summary()}")

        made = 0
        phase = "goals"
        tr.phase(phase)
        eq_now = book_equity()
        goals.ensure_goals(state, eq_now, venue="binance")
        save_state(state)
        day_open = float((state.get("goals") or {}).get("day_open_equity") or eq_now)
        day_pnl_pct = ((eq_now - day_open) / day_open * 100.0) if day_open > 0 else 0.0
        day_thr = cfg.day_loss_halt_pct(eq_now)
        loss_halt_buys = day_pnl_pct <= day_thr
        usable = 0.0
        try:
            usable = float(usable_usdt_for_v6())
        except Exception as exc:  # noqa: BLE001
            log(f"USABLE_FAIL {exc}")

        # Proactive unlock when powder is trapped in non-USDT
        try:
            import market_orchestrator as orch

            md = orch.load_mode()
            mreason = str(md.get("reason") or "")
            if usable < MIN_USDT and eq_now >= MIN_EXIT_USD:
                allow_lr = ("need_recharge" in mreason) or usable < 0.5
                before = usable
                ensure_spot_usdt(ORDER_USD, state, allow_last_resort=allow_lr)
                usable = float(usable_usdt_for_v6())
                if usable > before + 0.01:
                    log(f"UNLOCK_USDT {before:.2f}->{usable:.2f} last_resort={allow_lr}")
        except Exception as exc:  # noqa: BLE001
            log(f"UNLOCK_FAIL {exc}")

        modo = _orch_mode()
        holdings0 = sync_positions(state)
        pos_n = sum(1 for h in holdings0.values() if h["usd"] >= 0.5)
        tr.ctx.update(
            {
                "modo_orch": modo,
                "sleeve_usd": round(eq_now, 4),
                "usable_usdt": round(usable, 4),
                "day_pnl_pct": round(day_pnl_pct, 3),
                "posiciones": pos_n,
                "regime": regime_info["regime"],
                "btc_chg24": regime_info.get("btc_chg24"),
            }
        )
        if loss_halt_buys:
            log(f"DAY_LOSS_HALT_BUYS pnl_pct={day_pnl_pct:.2f}% (threshold {day_thr}%)")
            tr.skip("day_loss_halt", day_pnl_pct=day_pnl_pct, thr=day_thr)

        phase = "exit"
        tr.phase(phase)
        log("ACTION EXIT")
        if try_exits(state):
            made += 1
            tr.decide("EXIT", reason="exit_fired")
            state = load_state()
            time.sleep(2)

        phase = "consolidate"
        tr.phase(phase)
        log("ACTION CONSOLIDATE")
        state = load_state()
        if (not loss_halt_buys) and try_consolidate(state):
            made += 1
            tr.decide("CONSOLIDATE", reason="consolidate_fired")
            state = load_state()
            time.sleep(2)

        phase = "buy"
        tr.phase(phase)
        log("ACTION BUY")
        state = load_state()
        try:
            import market_orchestrator as orch

            can_buy = (not loss_halt_buys) and orch.allows_v6_buys()
        except Exception as exc:  # noqa: BLE001
            can_buy = not loss_halt_buys
            tr.phase("buy_orch_fail", exc=str(exc)[:200])

        bought = False
        if can_buy and try_buy(state, regime_info):
            made += 1
            bought = True
            tr.decide("BUY", reason="buy_ok")
            state = load_state()
            if (
                read_daily_count("binance") < cap
                and int(load_state().get("buys_today") or 0) < MAX_BUYS_PER_DAY
                and not loss_halt_buys
                and can_buy
            ):
                time.sleep(2)
                state = load_state()
                if try_buy(state, regime_info):
                    made += 1
                    tr.decide("BUY", reason="buy_second")
        elif loss_halt_buys:
            log("SKIP_BUY day_loss_halt")
            tr.skip("day_loss_halt")
        elif not can_buy:
            log("SKIP_BUY orch_mode")
            tr.skip("orch_mode", mode=modo)

        phase = "rotate"
        tr.phase(phase)
        state = load_state()
        holdings = sync_positions(state)
        legs = sum(1 for h in holdings.values() if h["usd"] >= 0.5)
        if legs > MAX_OPEN_LEGS and read_daily_count("binance") < cap:
            log("ACTION ROTATE_DOWN")
            exit_c = pick_exit(state, holdings, rotate_down=True, emergency_only=False)
            if exit_c and exit_c["kind"] == "rotate":
                if execute_sell(state, exit_c, emergency=False):
                    made += 1
                    tr.decide(
                        "EXIT",
                        reason="rotate_down",
                        symbol=exit_c.get("base") or exit_c.get("symbol"),
                    )

        if made == 0 and tr.decision.get("action") in ("HOLD", "SKIP"):
            if not bought and tr.decision.get("reason") == "tick_start":
                tr.decide("HOLD", reason="no_action")

        eq_end = book_equity()
        tr.end(
            made=made,
            daily=read_daily_count("binance"),
            daily_cap=cap,
            buys_today=load_state().get("buys_today"),
            sleeve_usd=round(eq_end, 4),
            usable_usdt=round(usable, 4),
            modo_orch=modo,
            regime=regime_info["regime"],
            day_pnl_pct=round(day_pnl_pct, 3),
            posiciones=legs,
        )
        log(
            f"TICK_DONE id={tr.cycle_id} made={made} daily={read_daily_count('binance')}/{cap} "
            f"buys={load_state().get('buys_today')}/{MAX_BUYS_PER_DAY} "
            f"eq=${eq_end:.2f} regime={regime_info['regime']} mode={modo} "
            f"dec={tr.decision.get('action')}:{tr.decision.get('reason')}"
        )


def sleep_seconds() -> int:
    from src.live.daily_count import read_daily_count
    from src.live.mandate.store import load_mandate

    mandate = load_mandate("binance")
    cap = mandate.hard_caps.max_trades_per_day if mandate else 3
    daily = read_daily_count("binance")
    if has_exitable_leg(load_state()):
        return POLL_OPEN_SEC
    if daily < cap:
        return POLL_HUNT_SEC
    return POLL_IDLE_SEC


def main() -> None:
    log(f"AUTOTRADE_START host=hetzner {STRATEGY_TAG} prompt={cfg.PROMPT_VERSION}")
    log(f"KNOBS {cfg.knobs_summary()}")
    log(f"TRACE_FILE {v6_trace.CYCLES_PATH}")
    while True:
        try:
            tick()
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            log(f"TICK_EXC {exc}")
            log(f"TICK_EXC_TB {tb[-2000:]}")
            # Ensure error lands in cycles even if context manager already wrote
            try:
                cur = v6_trace.current()
                if cur is None:
                    tr = v6_trace.CycleTrace(log_fn=log)
                    tr.start(note="outer_catch")
                    tr.error("tick", exc)
            except Exception:  # noqa: BLE001
                pass
            tg(format_error_tg(str(exc)))
        sec = sleep_seconds()
        log(f"SLEEP {sec}")
        time.sleep(sec)


if __name__ == "__main__":
    main()
