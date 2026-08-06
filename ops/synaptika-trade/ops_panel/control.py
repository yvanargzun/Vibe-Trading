#!/usr/bin/env python3
"""Mutable control plane for Ops Copiloto → Binance + Alpaca paper.

Writes control files under VIBE_HOME / ALPACA_HOME. Trading loops apply them
on the next tick (mode/knobs/HALT) or execute queued intents (buy/sell/close).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

BINANCE_MODES = frozenset({"recap", "standby", "defensive", "v6_primary"})
ALPACA_MODES = frozenset(
    {
        "canonical_v2",
        "smart_time",
        "scalp",
        "swing",
        "breakout",
        "mean_rev",
        "trend_follow",
        "rotation",
    }
)
NOTIFY_MODES = frozenset({"vibe", "scalper", "fb", "all"})
KNOB_KEYS = frozenset(
    {
        "ORDER_USD",
        "TP",
        "SL",
        "TRAIL_ACT",
        "TRAIL_GB",
        "TIME_STOP_HOURS",
        "MAX_BUYS_PER_DAY",
        "MAX_OPEN_LEGS",
        "MIN_BUY_SCORE",
        "MIN_BUY_SCORE_BEAR",
        "DAY_LOSS_HALT_PCT",
        "DAY_LOSS_HALT",
        "MAX_TRADES_PER_DAY",
        "COOLDOWN_HOURS",
        "MAX_EXPOSURE_PCT",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, row: dict[str, Any], *, max_lines: int = 200) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > max_lines:
            path.write_text("\n".join(lines[-max_lines:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def _halt_payload(reason: str, by: str = "ops_copilot") -> dict[str, Any]:
    return {
        "tripped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "by": by,
        "reason": (reason or "ops_halt")[:300],
    }


def set_halt(
    vibe: Path,
    alpaca: Path,
    *,
    venue: str,
    halt: bool,
    reason: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    if not confirm:
        return {"ok": False, "error": "confirm_required", "hint": "Pass confirm=true after user OK"}
    venue = (venue or "all").lower().strip()
    if venue not in ("binance", "alpaca", "all"):
        return {"ok": False, "error": "bad_venue", "venue": venue}
    touched: list[str] = []
    if venue in ("binance", "all"):
        global_h = vibe / "live" / "HALT"
        broker_h = vibe / "live" / "binance" / "HALT"
        if halt:
            for p in (global_h, broker_h):
                _write_json(p, _halt_payload(reason))
                touched.append(str(p))
        else:
            for p in (global_h, broker_h):
                if p.exists():
                    p.unlink(missing_ok=True)
                    touched.append(f"cleared:{p}")
    if venue in ("alpaca", "all"):
        p = alpaca / "HALT"
        if halt:
            _write_json(p, _halt_payload(reason))
            touched.append(str(p))
        else:
            if p.exists():
                p.unlink(missing_ok=True)
                touched.append(f"cleared:{p}")
    return {"ok": True, "halt": halt, "venue": venue, "touched": touched, "reason": reason}


def set_mode(
    vibe: Path,
    alpaca: Path,
    *,
    venue: str,
    mode: str,
    locked: bool = True,
    reason: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    if not confirm:
        return {"ok": False, "error": "confirm_required"}
    venue = (venue or "").lower().strip()
    mode = (mode or "").lower().strip()
    if venue == "binance":
        if mode not in BINANCE_MODES:
            return {"ok": False, "error": "bad_mode", "allowed": sorted(BINANCE_MODES)}
        path = vibe / "strategy_mode.json"
        doc = _read_json(path)
        now = time.time()
        doc.update(
            {
                "mode": mode,
                "locked": bool(locked),
                "locked_by": "ops_copilot" if locked else None,
                "reason": reason or f"ops_set_mode:{mode}",
                "since_ts": now,
                "last_flip_ts": now,
                "updated_ts": now,
            }
        )
        _write_json(path, doc)
        return {"ok": True, "venue": venue, "mode": mode, "locked": locked, "path": str(path)}
    if venue == "alpaca":
        if mode not in ALPACA_MODES:
            return {"ok": False, "error": "bad_mode", "allowed": sorted(ALPACA_MODES)}
        path = alpaca / "ops_control.json"
        doc = _read_json(path)
        doc.update(
            {
                "force_mode": mode,
                "locked": bool(locked),
                "reason": reason or f"ops_set_mode:{mode}",
                "updated_ts": time.time(),
                "by": "ops_copilot",
            }
        )
        _write_json(path, doc)
        # Mirror into state for UI digests
        st_path = alpaca / "state.json"
        st = _read_json(st_path)
        st["active_mode"] = mode
        st["mode_title"] = mode
        st["ops_force_mode"] = mode
        _write_json(st_path, st)
        return {"ok": True, "venue": venue, "mode": mode, "locked": locked, "path": str(path)}
    return {"ok": False, "error": "bad_venue", "hint": "binance|alpaca"}


def clear_mode_lock(
    vibe: Path,
    alpaca: Path,
    *,
    venue: str,
    confirm: bool = False,
) -> dict[str, Any]:
    if not confirm:
        return {"ok": False, "error": "confirm_required"}
    venue = (venue or "").lower().strip()
    if venue == "binance":
        path = vibe / "strategy_mode.json"
        doc = _read_json(path)
        doc["locked"] = False
        doc["locked_by"] = None
        doc["reason"] = "ops_unlock"
        doc["updated_ts"] = time.time()
        _write_json(path, doc)
        return {"ok": True, "venue": venue, "locked": False}
    if venue == "alpaca":
        path = alpaca / "ops_control.json"
        doc = _read_json(path)
        doc["force_mode"] = None
        doc["locked"] = False
        doc["reason"] = "ops_unlock"
        doc["updated_ts"] = time.time()
        _write_json(path, doc)
        return {"ok": True, "venue": venue, "locked": False}
    return {"ok": False, "error": "bad_venue"}


def set_notify_filter(*, vibe: Path, mode: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return {"ok": False, "error": "confirm_required"}
    mode = (mode or "").lower().strip()
    if mode == "both":
        mode = "all"
    if mode not in NOTIFY_MODES:
        return {"ok": False, "error": "bad_mode", "allowed": sorted(NOTIFY_MODES)}
    path = vibe / "telegram_notify_prefs.json"
    doc = {"mode": mode, "sales_test": False, "updated_ts": time.time(), "by": "ops_copilot"}
    _write_json(path, doc)
    return {"ok": True, "mode": mode, "path": str(path)}


def set_knobs(
    vibe: Path,
    alpaca: Path,
    *,
    venue: str,
    knobs: dict[str, Any],
    confirm: bool = False,
) -> dict[str, Any]:
    if not confirm:
        return {"ok": False, "error": "confirm_required"}
    venue = (venue or "").lower().strip()
    if venue not in ("binance", "alpaca"):
        return {"ok": False, "error": "bad_venue"}
    clean: dict[str, Any] = {}
    for k, v in (knobs or {}).items():
        key = str(k).upper()
        if key not in KNOB_KEYS:
            continue
        try:
            clean[key] = float(v) if not isinstance(v, bool) else v
        except (TypeError, ValueError):
            continue
    if not clean:
        return {"ok": False, "error": "no_valid_knobs", "allowed": sorted(KNOB_KEYS)}
    home = vibe if venue == "binance" else alpaca
    path = home / "v6_knobs_overlay.json"
    prev = _read_json(path)
    merged = {**(prev.get("knobs") or {}), **clean}
    doc = {
        "knobs": merged,
        "updated_ts": time.time(),
        "by": "ops_copilot",
        "venue": venue,
    }
    _write_json(path, doc)
    return {"ok": True, "venue": venue, "knobs": merged, "path": str(path)}


def enqueue_intent(
    vibe: Path,
    alpaca: Path,
    *,
    venue: str,
    action: str,
    symbol: str | None = None,
    usd: float | None = None,
    confirm: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    if not confirm:
        return {"ok": False, "error": "confirm_required"}
    venue = (venue or "").lower().strip()
    action = (action or "").lower().strip()
    if venue not in ("binance", "alpaca"):
        return {"ok": False, "error": "bad_venue"}
    if action not in ("buy", "sell", "close", "close_all"):
        return {"ok": False, "error": "bad_action", "allowed": ["buy", "sell", "close", "close_all"]}
    if action in ("buy", "sell", "close") and not symbol:
        return {"ok": False, "error": "symbol_required"}
    if action == "buy" and (usd is None or float(usd) <= 0):
        return {"ok": False, "error": "usd_required"}
    home = vibe if venue == "binance" else alpaca
    path = home / "ops_intents.jsonl"
    row = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "action": action,
        "symbol": (symbol or "").upper().replace("-", "/").replace("USDT", "/USDT")
        if symbol
        else None,
        "usd": None if usd is None else float(usd),
        "reason": reason[:200],
        "by": "ops_copilot",
        "status": "queued",
    }
    # Normalize symbols: BTCUSDT → BTC/USDT for binance; BTC/USD for alpaca
    if row["symbol"]:
        sym = str(row["symbol"]).upper().replace(" ", "")
        if venue == "binance":
            if "/" not in sym and sym.endswith("USDT"):
                sym = f"{sym[:-4]}/USDT"
            elif sym.endswith("/USD"):
                sym = sym.replace("/USD", "/USDT")
        else:
            if "/" not in sym and sym.endswith("USDT"):
                sym = f"{sym[:-4]}/USD"
            elif sym.endswith("/USDT"):
                sym = sym.replace("/USDT", "/USD")
            elif "/" not in sym:
                sym = f"{sym}/USD"
        row["symbol"] = sym
    _append_jsonl(path, row)
    return {"ok": True, "intent": row, "path": str(path)}


def control_status(vibe: Path, alpaca: Path) -> dict[str, Any]:
    bn_mode = _read_json(vibe / "strategy_mode.json")
    al_ctl = _read_json(alpaca / "ops_control.json")
    prefs = _read_json(vibe / "telegram_notify_prefs.json")
    return {
        "binance": {
            "mode": bn_mode.get("mode"),
            "locked": bool(bn_mode.get("locked")),
            "halt": (vibe / "live" / "HALT").exists()
            or (vibe / "live" / "binance" / "HALT").exists(),
            "knobs_overlay": _read_json(vibe / "v6_knobs_overlay.json").get("knobs") or {},
            "queued_intents": _queued_count(vibe / "ops_intents.jsonl"),
        },
        "alpaca": {
            "force_mode": al_ctl.get("force_mode"),
            "locked": bool(al_ctl.get("locked")),
            "halt": (alpaca / "HALT").exists(),
            "knobs_overlay": _read_json(alpaca / "v6_knobs_overlay.json").get("knobs") or {},
            "queued_intents": _queued_count(alpaca / "ops_intents.jsonl"),
        },
        "notify_filter": prefs.get("mode") or "all",
    }


def _queued_count(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "queued":
            n += 1
    return n
