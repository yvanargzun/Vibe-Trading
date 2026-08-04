"""Dynamic Telegram monitor every tick: ATR thresholds + live order status.

Each run (intended every 10m):
1. Portfolio mark + dynamic ATR threshold
2. Open orders / daily mandate trade count
3. Telegram digest with both; extra alert if threshold crossed or orders changed
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
import sys as _sys
_sys.path.insert(0, "/root/.vibe-trading")
import dynamic_goals as goals
import equity_chart as equity_chart

AGENT = Path("/opt/vibe-trade/agent")
sys.path.insert(0, str(AGENT))

# Runtime home .env only (never Synaptica demos/messenger .env)
VIBE_HOME = Path(os.environ.get("VIBE_TRADING_HOME", "/root/.vibe-trading"))
VIBE_ENV = VIBE_HOME / ".env"
SNAP_PATH = VIBE_HOME / "telegram_portfolio_snap.json"
STATE_PATH = VIBE_HOME / "telegram_monitor_state.json"
ORDERS_SNAP = VIBE_HOME / "telegram_orders_snap.json"
BASELINE_PATH = VIBE_HOME / "telegram_profit_baseline.json"
DAILY_SENT_PATH = VIBE_HOME / "telegram_daily_sent.json"
SCALP_STATE_PATH = VIBE_HOME / "eth_scalp_state.json"

# Daily summary window: 08:30 Mexico = 14:30 UTC (no DST for this target)
DAILY_UTC_HOUR = 14
DAILY_UTC_MINUTE_START = 30
DAILY_UTC_MINUTE_END = 39

ATR_MULT = 0.85
THRESH_MIN = 1.0
THRESH_MAX = 4.5
LOOKBACK = 14

# Live session fills we placed (cost basis USDT spent).
SESSION_FILLS = (
    {"asset": "BTC", "cost_usd": 4.4209, "qty": 0.00006993},
    {"asset": "BNB", "cost_usd": 4.70696, "qty": 0.008},
    {"asset": "DOGE", "cost_usd": 4.2042, "qty": 60.0},
)


def _load_env(path: Path) -> dict[str, str]:
    vals: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def get(url: str):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def tg(token: str, chat: str, text: str) -> bool:
    # Shared sender: filter + anti-duplicado (evita 2 burbujas por restart+prueba)
    try:
        sys.path.insert(0, "/root/.vibe-trading")
        from telegram_notify_prefs import send_text

        ok = send_text(text, channel="vibe", dedupe=True)
        if not ok:
            print("TG_SKIP_OR_DEDUPE")
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        print("TG_PREFS_WARN", exc)
    body = json.dumps(
        {"chat_id": chat, "text": text[:3500], "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return bool(json.loads(r.read()).get("ok"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False


def atr_pct(symbol: str) -> float | None:
    try:
        kl = get(
            f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit={LOOKBACK + 2}"
        )
    except Exception:
        return None
    if len(kl) < LOOKBACK + 1:
        return None
    trs: list[float] = []
    prev_close = float(kl[0][4])
    for c in kl[1:]:
        high, low, close = float(c[2]), float(c[3]), float(c[4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = close
    window = trs[-LOOKBACK:]
    if not window or prev_close <= 0:
        return None
    return (sum(window) / len(window) / prev_close) * 100.0


def _underlying_asset(code: str) -> tuple[str, str]:
    """Map wallet code Ã¢â€ â€™ (display_label, price_asset). Earn Flexible uses LD* wraps."""
    a = (code or "").strip().upper()
    if a.startswith("LD") and len(a) > 2:
        under = a[2:]
        return f"{under}(Earn)", under
    return a, a


def portfolio() -> tuple[float, dict[str, float], list[str]]:
    from src.trading.connectors.binance import sdk as bn

    # Salvage stranded USDT so Telegram total matches tradeable Spot
    try:
        import binance_wallets as bw

        bw.salvage_usdt_to_spot(force=False)
    except Exception as exc:  # noqa: BLE001
        print("PORTFOLIO_SALVAGE_WARN", exc)

    cfg = bn.load_config()
    acc = bn.get_account_snapshot(cfg)
    # Aggregate by underlying (Spot + Earn Flexible LD*).
    held: dict[str, float] = {}
    labels: dict[str, str] = {}
    for b in acc.get("balances", []):
        raw = str(b.get("asset") or "")
        qty = float(b.get("total") or 0)
        if qty <= 0:
            continue
        label, under = _underlying_asset(raw)
        held[under] = held.get(under, 0.0) + qty
        # Prefer Earn label if any sleeve is in Earn; else spot code.
        if "(Earn)" in label or under not in labels:
            labels[under] = label if under in held and held[under] == qty else under

    # Include Funding + idle UM Futures USDT in USDT sleeve / total
    try:
        import binance_wallets as bw

        extra = float(bw.funding_usdt_free()) + float(bw.futures_usdt_available())
        if extra > 0:
            held["USDT"] = float(held.get("USDT") or 0) + extra
            labels.setdefault("USDT", "USDT")
    except Exception as exc:  # noqa: BLE001
        print("PORTFOLIO_EXTRA_USDT_WARN", exc)

    total = 0.0
    parts: list[str] = []
    priced: dict[str, float] = {}
    for asset, qty in held.items():
        if asset in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USD"):
            px = 1.0
        else:
            try:
                px = float(get(f"https://api.binance.com/api/v3/ticker/price?symbol={asset}USDT")["price"])
            except Exception:
                continue
        val = qty * px
        total += val
        priced[asset] = qty
        # Short ASCII holdings line (no fancy unicode).
        parts.append(f"{labels.get(asset, asset)} ${val:.2f}")
    return total, priced, parts


def dynamic_threshold(held: dict[str, float], total: float) -> tuple[float, str]:
    if total <= 0:
        return 2.0, "empty"

    weighted = 0.0
    weight_sum = 0.0
    for asset, qty in held.items():
        if asset == "USDT":
            continue
        try:
            px = float(get(f"https://api.binance.com/api/v3/ticker/price?symbol={asset}USDT")["price"])
        except Exception:
            continue
        val = qty * px
        ap = atr_pct(f"{asset}USDT")
        if ap is None or not math.isfinite(ap):
            continue
        weighted += ap * val
        weight_sum += val

    if weight_sum <= 0:
        return 2.0, "no ATR"
    avg_atr = weighted / weight_sum
    thr = max(THRESH_MIN, min(THRESH_MAX, avg_atr * ATR_MULT))
    return thr, f"ATR {avg_atr:.2f}%"


def order_status(held: dict[str, float] | None = None) -> dict:
    """Open orders (per held symbol) + mandate daily count + caps."""
    from src.trading.connectors.binance import sdk as bn
    from src.live.daily_count import read_daily_count
    from src.live.mandate.store import load_mandate
    from src.live.halt import halt_flag_set

    cfg = bn.load_config()
    ex = bn._exchange(cfg)
    # ccxt warns / rate-limits fetch_open_orders() without a symbol Ã¢â‚¬â€ probe sleeves.
    symbols = []
    for asset in (held or {}):
        if asset and asset != "USDT" and not str(asset).startswith("LD"):
            symbols.append(f"{asset}/USDT")
    for s in ("BTC/USDT", "BNB/USDT", "DOGE/USDT", "ETH/USDT", "SOL/USDT"):
        if s not in symbols:
            symbols.append(s)

    open_rows = []
    notes: list[str] = []
    seen: set[str] = set()
    for sym in symbols:
        try:
            orders = ex.fetch_open_orders(sym)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{sym}: {exc}")
            continue
        for o in orders or []:
            oid = str(o.get("id") or o.get("orderId") or "")
            if oid and oid in seen:
                continue
            if oid:
                seen.add(oid)
            open_rows.append(
                {
                    "id": oid,
                    "symbol": o.get("symbol") or sym,
                    "side": o.get("side"),
                    "type": o.get("type"),
                    "status": o.get("status"),
                    "amount": o.get("amount"),
                    "price": o.get("price"),
                }
            )

    mandate = load_mandate("binance")
    daily = read_daily_count("binance")
    max_day = mandate.hard_caps.max_trades_per_day if mandate else None
    halted = halt_flag_set("binance")
    return {
        "open_orders": open_rows,
        "open_count": len(open_rows),
        "daily_trades": daily,
        "daily_cap": max_day,
        "mandate_order_usd": mandate.hard_caps.max_order_notional_usd if mandate else None,
        "mandate_exposure_usd": mandate.hard_caps.max_total_exposure_usd if mandate else None,
        "halted": halted,
        "note": "; ".join(notes[:3]) if notes else None,
    }


def _orders_fingerprint(st: dict) -> str:
    ids = sorted(str(o.get("id")) for o in st.get("open_orders") or [])
    return json.dumps(
        {
            "ids": ids,
            "daily": st.get("daily_trades"),
            "halted": st.get("halted"),
        },
        sort_keys=True,
    )


def ensure_baseline(total: float) -> dict:
    """Persist book baseline once; keep session fill costs."""
    if BASELINE_PATH.exists():
        try:
            return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    session_cost = round(sum(f["cost_usd"] for f in SESSION_FILLS), 4)
    doc = {
        "book_baseline_usd": round(total, 4),
        "session_cost_usd": session_cost,
        "session_fills": list(SESSION_FILLS),
        "note": "book_baseline = equity when profit tracking started; session = live fills cost",
        "created_ts": time.time(),
    }
    BASELINE_PATH.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def profit_block(held: dict[str, float], total: float) -> tuple[str, dict]:
    """Session + book unrealized profit lines."""
    base = ensure_baseline(total)
    book_base = float(base.get("book_baseline_usd") or total)
    book_pnl = total - book_base
    book_pct = 0.0 if book_base <= 0 else book_pnl / book_base * 100.0

    session_cost = float(base.get("session_cost_usd") or 0.0)
    session_value = 0.0
    for fill in base.get("session_fills") or SESSION_FILLS:
        asset = str(fill.get("asset") or "")
        qty_held = float(held.get(asset) or 0.0)
        # Use min(held, fill qty) so partial sells don't overstate; if Earn kept it, heldÃ¢â€°Ë†fill.
        qty = qty_held if qty_held > 0 else float(fill.get("qty") or 0.0)
        if asset in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USD"):
            px = 1.0
        else:
            try:
                px = float(get(f"https://api.binance.com/api/v3/ticker/price?symbol={asset}USDT")["price"])
            except Exception:
                continue
        session_value += qty * px
    session_pnl = session_value - session_cost
    session_pct = 0.0 if session_cost <= 0 else session_pnl / session_cost * 100.0

    def fmt(pnl: float, pct: float) -> str:
        sign = "+" if pnl >= 0 else ""
        return f"{sign}${pnl:.2f} ({sign}{pct:.2f}%)"

    # Keep ASCII-only labels to avoid mojibake on some hosts.
    line = f"PnL sesion {fmt(session_pnl, session_pct)} | libro {fmt(book_pnl, book_pct)}"
    stats = {
        "session_pnl": round(session_pnl, 4),
        "session_pct": round(session_pct, 3),
        "book_pnl": round(book_pnl, 4),
        "book_pct": round(book_pct, 3),
        "session_value": round(session_value, 4),
        "session_cost": session_cost,
        "book_baseline": book_base,
    }
    return line, stats


def format_orders_block(st: dict) -> str:
    cap = st["daily_cap"] if st["daily_cap"] is not None else "?"
    line = f"Trades del dia: {st['daily_trades']}/{cap}"
    if st["halted"]:
        line += " | PAUSADO"
    if st["open_count"]:
        line += f" | ordenes abiertas: {st['open_count']}"
    return line


def format_novice_digest(
    *,
    total: float,
    chg: float,
    thr: float,
    thr_hit: bool,
    orders_changed: bool,
    hold: str,
    profit_stats: dict,
    ost: dict,
) -> str:
    """One detailed-but-novice Telegram message (single bubble)."""
    sign = "+" if chg >= 0 else ""
    book_pnl = float(profit_stats.get("book_pnl") or 0)
    book_sign = "+" if book_pnl >= 0 else ""
    book_pct = float(profit_stats.get("book_pct") or 0)
    book_pct_sign = "+" if book_pct >= 0 else ""

    baseline = 0.0
    regime = ""
    pos_lines: list[str] = []
    try:
        st = json.loads((Path("/root/.vibe-trading") / "autotrade_state.json").read_text(encoding="utf-8"))
        baseline = float(st.get("double_baseline") or 0)
        regime = str(st.get("regime") or "")
        for asset, meta in (st.get("positions") or {}).items():
            usd = float((meta or {}).get("usd") or 0)
            if usd >= 0.4:
                pos_lines.append(f"  - {asset}: unos ${usd:.2f}")
    except Exception:
        pass

    regime_txt = {
        "bull": "mercado alcista (subiendo en general)",
        "bear": "mercado bajista (bajando en general)",
        "chop": "mercado lateral (sin tendencia clara)",
    }.get(regime, "sin dato de mercado aun")

    daily = ost.get("daily_trades")
    cap = ost.get("daily_cap") if ost.get("daily_cap") is not None else "?"
    left = ""
    try:
        left = f" (te quedan {int(cap) - int(daily)} de {cap})"
    except Exception:
        left = ""

    if thr_hit:
        mood = "ALERTA: tu dinero se movio mas de lo normal"
    elif orders_changed:
        mood = "Hubo un cambio en trades/ordenes"
    else:
        mood = "Todo tranquilo en estos ~10 minutos"

    lines = [
        "[Binance] Resumen Vibe (cada 10 min)",
        mood,
        "",
        "Tu dinero ahora",
        f"- Billetera total aprox: ${total:.2f}",
        f"- Cambio vs el resumen anterior: {sign}{chg:.2f}%",
        f"- Cambio del dia (mark): {book_sign}${book_pnl:.2f} ({book_pct_sign}{book_pct:.2f}%)",
    ]
    try:
        # load autotrade state for goals window
        st_goals = {}
        try:
            st_goals = json.loads(Path("/root/.vibe-trading/autotrade_state.json").read_text(encoding="utf-8"))
        except Exception:
            st_goals = {"double_baseline": baseline}
        goals.ensure_goals(st_goals, total, venue="binance")
        try:
            Path("/root/.vibe-trading/autotrade_state.json").write_text(
                json.dumps(st_goals, indent=2) + "\n", encoding="utf-8"
            )
        except Exception:
            pass
        lines += goals.digest_goal_block(st_goals, total)
    except Exception:
        if baseline > 0:
            target = baseline * 2
            prog = max(0.0, min(100.0, (total / target) * 100.0))
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
        lines.append(f"- {hold}")
    lines += [
        "",
        "Actividad de hoy",
        f"- Trades usados: {daily}/{cap}{left}",
        f"- Mercado (lectura del bot): {regime_txt}",
    ]
    text = "\n".join(lines)
    if len(text) > 3400:
        text = text[:3397] + "..."
    return text


def _utc_now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def daily_summary_due() -> bool:
    """True once per UTC day in the 14:30â€“14:39 window (08:30 Mexico)."""
    now = _utc_now()
    if now.hour != DAILY_UTC_HOUR:
        return False
    if not (DAILY_UTC_MINUTE_START <= now.minute <= DAILY_UTC_MINUTE_END):
        return False
    day = now.date().isoformat()
    if DAILY_SENT_PATH.exists():
        try:
            doc = json.loads(DAILY_SENT_PATH.read_text(encoding="utf-8"))
            if str(doc.get("day") or "") == day:
                return False
        except json.JSONDecodeError:
            pass
    return True


def mark_daily_sent() -> None:
    day = _utc_now().date().isoformat()
    DAILY_SENT_PATH.write_text(json.dumps({"day": day, "ts": time.time()}, indent=2) + "\n", encoding="utf-8")


def format_daily_summary(
    *,
    total: float,
    profit_stats: dict,
    ost: dict,
    hold: str,
) -> str:
    book_pnl = float(profit_stats.get("book_pnl") or 0)
    book_sign = "+" if book_pnl >= 0 else ""
    day_pnl = 0.0
    day_tgt = 0.0
    regime = ""
    buys = 0
    try:
        st = json.loads(Path("/root/.vibe-trading/autotrade_state.json").read_text(encoding="utf-8"))
        snap = st.get("goal_snap") or {}
        day_pnl = float(snap.get("day_pnl") or 0)
        day_tgt = float(snap.get("daily_target") or (st.get("goals") or {}).get("daily_target_usd") or 0)
        regime = str(st.get("regime") or "")
        buys = int(st.get("buys_today") or 0)
    except Exception:
        pass
    scalp_fills = 0
    scalp_regime = "?"
    scalp_killed = False
    try:
        sc = json.loads(SCALP_STATE_PATH.read_text(encoding="utf-8"))
        scalp_fills = int(sc.get("fills_today") or 0)
        scalp_regime = str(sc.get("last_regime") or "?")
        scalp_killed = bool(sc.get("killed"))
    except Exception:
        pass
    day_sign = "+" if day_pnl >= 0 else ""
    lines = [
        "[Binance] Resumen diario Â· 08:30",
        f"Billetera: ${total:.2f}",
        f"PnL dia (metas): {day_sign}${day_pnl:.2f} / meta ${day_tgt:.2f}",
        f"PnL libro: {book_sign}${book_pnl:.2f}",
        f"Tienes: {hold}",
        f"Vibe v6: buys hoy {buys} | trades mandate {ost.get('daily_trades')}/{ost.get('daily_cap') or '?'}"
        + (" | PAUSADO" if ost.get("halted") else ""),
        f"Scalper ETH: fills hoy {scalp_fills} | regimen {scalp_regime}"
        + (" | KILL activo" if scalp_killed else ""),
        f"Mercado (v6): {regime or 'n/d'}",
        "Siguiente: solo avisos de trades o movimientos fuertes; este resumen vuelve manana.",
    ]
    return "\n".join(lines)


def main() -> int:
    env = _load_env(VIBE_ENV)
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("ERR no telegram config")
        return 1

    force = os.environ.get("FORCE_DIGEST", "").strip() in ("1", "true", "yes") or (
        "--announce" in sys.argv
    )

    total, held, parts = portfolio()
    thr, reason = dynamic_threshold(held, total)
    ost = order_status(held)
    profit_txt, profit_stats = profit_block(held, total)

    prev = {"total": total}
    if SNAP_PATH.exists():
        try:
            prev = json.loads(SNAP_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    prev_total = float(prev.get("total") or total)
    chg = 0.0 if prev_total <= 0 else (total - prev_total) / prev_total * 100.0

    # Fail-soft: empty mark after a healthy book is almost always a read glitch
    stale_empty = prev_total >= 1.0 and total <= 0.05
    if stale_empty:
        print("SKIP_EMPTY_BOOK prev=", prev_total)
        return 0

    prev_fp = ""
    if ORDERS_SNAP.exists():
        try:
            prev_fp = json.loads(ORDERS_SNAP.read_text(encoding="utf-8")).get("fp", "")
        except json.JSONDecodeError:
            prev_fp = ""
    fp = _orders_fingerprint(ost)
    orders_changed = bool(prev_fp) and fp != prev_fp
    thr_hit = abs(chg) >= thr
    daily_due = daily_summary_due()

    state = {
        "total": total,
        "chg_pct": round(chg, 3),
        "threshold_pct": round(thr, 3),
        "reason": reason,
        "profit": profit_stats,
        "orders": ost,
        "ts": time.time(),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    ORDERS_SNAP.write_text(json.dumps({"fp": fp, "orders": ost, "ts": time.time()}), encoding="utf-8")

    print(
        "AGENT_LOOP_TICK_trade_alert "
        + json.dumps(
            {
                "total": round(total, 4),
                "chg_pct": round(chg, 3),
                "threshold_pct": round(thr, 3),
                "open_orders": ost["open_count"],
                "daily_trades": ost["daily_trades"],
                "orders_changed": orders_changed,
                "thr_hit": thr_hit,
                "daily_due": daily_due,
                "force": force,
                "session_pnl": profit_stats.get("session_pnl"),
                "book_pnl": profit_stats.get("book_pnl"),
            }
        )
    )

    hold = " ".join(parts[:4]) if parts else "sin posiciones grandes"
    _ = profit_txt

    should_send = force or thr_hit or orders_changed or daily_due
    if not should_send:
        # Still advance portfolio snap so next % is vs this quiet tick
        SNAP_PATH.write_text(
            json.dumps({"total": total, "held": held, "ts": time.time(), "last_thr": thr}),
            encoding="utf-8",
        )
        print("SKIP_QUIET", f"chg={chg:.3f}% thr={thr:.2f}% orders_changed={orders_changed}")
        return 0

    if daily_due and not (thr_hit or orders_changed or force):
        digest = format_daily_summary(
            total=total, profit_stats=profit_stats, ost=ost, hold=hold
        )
        kind = "daily"
    else:
        digest = format_novice_digest(
            total=total,
            chg=chg,
            thr=thr,
            thr_hit=thr_hit,
            orders_changed=orders_changed,
            hold=hold,
            profit_stats=profit_stats,
            ost=ost,
        )
        kind = "event"

    if len(digest) > 3400:
        digest = digest[:3397] + "..."

    # Gate digests on Vibe filter (on-demand status uses FORCE_STATUS=1)
    force_status = os.environ.get("FORCE_STATUS", "").strip().lower() in ("1", "true", "yes")
    if not force_status:
        try:
            sys.path.insert(0, "/root/.vibe-trading")
            from telegram_notify_prefs import should_notify, load_prefs

            if not should_notify("vibe"):
                print("SKIP_FILTER mode=", load_prefs().get("mode"), "channel=vibe")
                SNAP_PATH.write_text(
                    json.dumps({"total": total, "held": held, "ts": time.time(), "last_thr": thr}),
                    encoding="utf-8",
                )
                return 0
        except Exception as exc:  # noqa: BLE001
            print("FILTER_CHECK_WARN", exc)

    text_ok = False
    chart_ok = False
    try:
        st_goals = {}
        try:
            st_goals = json.loads(Path("/root/.vibe-trading/autotrade_state.json").read_text(encoding="utf-8"))
        except Exception:
            st_goals = {}
        g = st_goals.get("goals") or {}
        snap = st_goals.get("goal_snap") or {}
        text_ok, chart_ok, _ = equity_chart.build_and_send(
            history_path=Path("/root/.vibe-trading/equity_history.json"),
            chart_path=Path("/root/.vibe-trading/equity_chart.png"),
            token=token,
            chat=chat,
            venue_tag="Binance",
            equity=total,
            text=digest,
            day_open=float(g.get("day_open_equity") or snap.get("day_open") or total),
            daily_target=float(g.get("daily_target_usd") or snap.get("daily_target") or 0),
            week_open=float(g.get("week_open_equity") or snap.get("week_open") or total),
            weekly_target=float(g.get("weekly_target_usd") or snap.get("weekly_target") or 0),
        )
    except Exception as exc:  # noqa: BLE001
        print("CHART_FAIL", exc)
        text_ok = tg(token, chat, digest)
        chart_ok = False
    if not text_ok and not chart_ok:
        text_ok = tg(token, chat, digest)
    ok = text_ok or chart_ok
    if ok and daily_due:
        mark_daily_sent()
    print(
        "STATUS_SENT",
        ok,
        f"kind={kind} text={text_ok} chart={chart_ok} chg={chg:.2f}% thr={thr:.2f}% open={ost['open_count']} bytes={len(digest)}",
    )

    # Advance snapshot every tick so next % is vs this report (single timeline).
    SNAP_PATH.write_text(
        json.dumps({"total": total, "held": held, "ts": time.time(), "last_thr": thr}),
        encoding="utf-8",
    )
    if thr_hit:
        print("ALERT_SENT", ok, f"{chg:.2f}% thr={thr:.2f}%")
    else:
        print("NO_THRESH_ALERT", f"{chg:.3f}% thr={thr:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
