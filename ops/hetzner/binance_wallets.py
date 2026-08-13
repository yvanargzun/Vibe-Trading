#!/usr/bin/env python3
"""Binance multi-wallet helpers: Spot + Funding + UM Futures USDT.

Prevents phantom equity cliffs when USDT sits outside Spot, and salvages
idle Funding/Futures USDT back to MAIN for v6/scalper.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

HOME = Path("/root/.vibe-trading")
STABLES = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USD"}
_LAST_SALVAGE_TS = 0.0
_LAST_EARN_UNLOCK_TS = 0.0
SALVAGE_COOLDOWN_SEC = 120.0
EARN_UNLOCK_COOLDOWN_SEC = 300.0


def _http_price(asset: str) -> float:
    if asset in STABLES:
        return 1.0
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={asset}USDT"
    with urllib.request.urlopen(url, timeout=15) as r:
        return float(json.loads(r.read())["price"])


def _exchange():
    from src.trading.connectors.binance import sdk as bn

    cfg = bn.load_config()
    return bn._exchange(cfg), cfg, bn


def spot_balances() -> list[dict[str, Any]]:
    _, cfg, bn = _exchange()
    acc = bn.get_account_snapshot(cfg)
    return list(acc.get("balances", []) or [])


def earn_ld_positions() -> list[dict[str, Any]]:
    """LD* Flexible Earn wrappers visible in the Spot snapshot."""
    out: list[dict[str, Any]] = []
    for b in spot_balances():
        raw = str(b.get("asset") or "")
        if not raw.startswith("LD") or len(raw) <= 2:
            continue
        qty = float(b.get("total") or 0) or (
            float(b.get("free") or 0) + float(b.get("locked") or 0)
        )
        if qty <= 0:
            continue
        asset = raw[2:]
        usd = 0.0
        try:
            usd = qty * (1.0 if asset in STABLES else _http_price(asset))
        except Exception:
            usd = 0.0
        out.append({"ld_asset": raw, "asset": asset, "qty": qty, "usd": usd})
    return out


def earn_locked_usd() -> float:
    return sum(float(p.get("usd") or 0) for p in earn_ld_positions())


def flexible_earn_rows() -> list[dict[str, Any]]:
    try:
        ex, _, _ = _exchange()
        data = ex.request("simple-earn/flexible/position", "sapi", "GET", {"size": 100})
        return list((data or {}).get("rows") or [])
    except Exception as exc:  # noqa: BLE001
        print(f"EARN_POS_FAIL {exc}", flush=True)
        return []


def redeem_flexible_asset(asset: str, *, redeem_all: bool = True) -> bool:
    """Redeem Simple Earn flexible position for ``asset`` back to Spot."""
    rows = [
        r
        for r in flexible_earn_rows()
        if str(r.get("asset") or "").upper() == asset.upper()
    ]
    if not rows:
        return False
    row = rows[0]
    if not row.get("canRedeem", True):
        print(f"EARN_REDEEM_LOCKED {asset}", flush=True)
        return False
    product_id = row.get("productId")
    avail = float(row.get("totalAmount") or 0)
    if avail <= 0 or not product_id:
        return False
    params: dict[str, Any] = {"productId": product_id}
    if redeem_all:
        params["redeemAll"] = True
    else:
        params["amount"] = f"{avail:.8f}".rstrip("0").rstrip(".")
    try:
        ex, _, _ = _exchange()
        ex.request("simple-earn/flexible/redeem", "sapi", "POST", params)
        print(f"EARN_REDEEM_OK {asset} {params}", flush=True)
        time.sleep(3)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"EARN_REDEEM_FAIL {asset} {exc}", flush=True)
        return False


def redeem_all_flexible_earn(*, force: bool = False) -> list[str]:
    """Redeem every flexible Earn row. Returns redeemed asset symbols."""
    global _LAST_EARN_UNLOCK_TS
    now = time.time()
    if not force and (now - _LAST_EARN_UNLOCK_TS) < EARN_UNLOCK_COOLDOWN_SEC:
        return []
    redeemed: list[str] = []
    for row in flexible_earn_rows():
        asset = str(row.get("asset") or "").upper()
        if not asset:
            continue
        if redeem_flexible_asset(asset, redeem_all=True):
            redeemed.append(asset)
    if redeemed or force:
        _LAST_EARN_UNLOCK_TS = now
    return redeemed


def funding_usdt_free() -> float:
    try:
        ex, _, _ = _exchange()
        rows = ex.request("asset/get-funding-asset", "sapi", "POST", {"asset": "USDT"})
        if isinstance(rows, list):
            for row in rows:
                if str(row.get("asset") or "") == "USDT":
                    return float(row.get("free") or 0)
        if isinstance(rows, dict):
            return float(rows.get("free") or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"FUNDING_USDT_FAIL {exc}", flush=True)
    return 0.0


def futures_usdt_available() -> float:
    try:
        ex, _, _ = _exchange()
        bals = ex.fapiPrivateV2GetBalance()
        for row in bals or []:
            if str(row.get("asset") or "") == "USDT":
                return float(row.get("availableBalance") or row.get("balance") or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"FUTURES_USDT_FAIL {exc}", flush=True)
    return 0.0


def futures_eth_open() -> bool:
    try:
        ex, _, _ = _exchange()
        pos = ex.fapiPrivateV2GetPositionRisk()
        for p in pos or []:
            sym = str(p.get("symbol") or "")
            amt = abs(float(p.get("positionAmt") or 0))
            if amt > 0 and sym.startswith("ETH"):
                return True
    except Exception:
        pass
    return False


def scalp_has_position() -> bool:
    p = HOME / "eth_scalp_state.json"
    if not p.exists():
        return False
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return bool(st.get("position"))


def spot_book_equity() -> float:
    """Spot (+ LD Earn wraps in snapshot) only."""
    total = 0.0
    for b in spot_balances():
        raw = str(b.get("asset") or "")
        qty = float(b.get("total") or 0) or (
            float(b.get("free") or 0) + float(b.get("locked") or 0)
        )
        if qty <= 0:
            continue
        asset = raw[2:] if raw.startswith("LD") and len(raw) > 2 else raw
        if asset in STABLES:
            total += qty
            continue
        try:
            total += qty * _http_price(asset)
        except Exception:
            continue
    return total


def total_book_equity() -> float:
    """Spot mark + Funding USDT + UM Futures free USDT (idle capital)."""
    total = spot_book_equity()
    fund = funding_usdt_free()
    fut = futures_usdt_available()
    # Avoid double-count if already in spot path (they're separate wallets)
    total += max(fund, 0.0) + max(fut, 0.0)
    return total


def free_spot_usdt() -> float:
    for b in spot_balances():
        if str(b.get("asset") or "") == "USDT":
            return float(b.get("free") or 0)
    return 0.0


def salvage_usdt_to_spot(*, force: bool = False) -> dict[str, float]:
    """Pull idle Funding + Futures USDT back to MAIN Spot.

    Skips Futures salvage only when an ETH futures position is still open
    (margin in use). Scalper service is retired — ignore scalp state.
    """
    global _LAST_SALVAGE_TS
    now = time.time()
    if not force and (now - _LAST_SALVAGE_TS) < SALVAGE_COOLDOWN_SEC:
        return {}
    _LAST_SALVAGE_TS = now
    moved: dict[str, float] = {}
    ex, _, _ = _exchange()

    fund = funding_usdt_free()
    if fund >= 1.0:
        amt = round(fund * 0.999, 8)
        try:
            ex.request(
                "asset/transfer",
                "sapi",
                "POST",
                {"type": "FUNDING_MAIN", "asset": "USDT", "amount": f"{amt}"},
            )
            moved["funding"] = amt
            print(f"SALVAGE_FUNDING_MAIN {amt}", flush=True)
            time.sleep(0.8)
        except Exception as exc:  # noqa: BLE001
            print(f"SALVAGE_FUNDING_FAIL {exc}", flush=True)

    if not futures_eth_open():
        fut = futures_usdt_available()
        if fut >= 1.0:
            amt = round(min(fut * 0.99, fut - 0.05), 4)
            if amt >= 1.0:
                try:
                    ex.request(
                        "asset/transfer",
                        "sapi",
                        "POST",
                        {"type": "UMFUTURE_MAIN", "asset": "USDT", "amount": f"{amt:.4f}"},
                    )
                    moved["futures"] = amt
                    print(f"SALVAGE_UMFUTURE_MAIN {amt}", flush=True)
                    time.sleep(0.8)
                except Exception as exc:  # noqa: BLE001
                    print(f"SALVAGE_FUTURES_FAIL {exc}", flush=True)
    else:
        print("SALVAGE_FUTURES_SKIP eth_futures_open", flush=True)
    return moved
