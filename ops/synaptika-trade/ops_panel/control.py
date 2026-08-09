#!/usr/bin/env python3
"""Mutable control plane for Ops Copiloto → Binance + Alpaca paper.

Writes control files under VIBE_HOME / ALPACA_HOME. Trading loops apply them
on the next tick (mode/knobs/HALT) or execute queued intents (buy/sell/close).
"""

from __future__ import annotations

import json
import re
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
NOTIFY_MODES = frozenset({"vibe", "scalp15", "scalper", "fb", "all"})
KNOB_KEYS = frozenset(
    {
        "ORDER_USD",
        "TP",
        "SL",
        "TRAIL_ACT",
        "TRAIL_GB",
        "TIME_STOP_HOURS",
        "TIME_MIN_PNL",
        "MAX_BUYS_PER_DAY",
        "MAX_OPEN_LEGS",
        "MIN_BUY_SCORE",
        "MIN_BUY_SCORE_BEAR",
        "MIN_BUY_SCORE_GRACE",
        "SL_MICRO",
        "TIME_STOP_HOURS_MICRO",
        "TIME_MIN_PNL_MICRO",
        "EARLY_EXIT_H",
        "EARLY_EXIT_PNL",
        "GRACE_MAX_BUYS",
        "GRACE_MAX_BUYS_GREEN",
        "DAY_LOSS_HALT_PCT",
        "DAY_LOSS_HALT",
        "MAX_TRADES_PER_DAY",
        "COOLDOWN_HOURS",
        "MAX_EXPOSURE_PCT",
        "SLEEVE_CORE",
        "SLEEVE_BURST",
    }
)
SLEEVE_FIELDS = frozenset(
    {
        "order_pct",
        "order_min",
        "order_max",
        "max_exposure_pct",
        "tp",
        "sl",
        "trail_act",
        "trail_gb",
        "time_stop_h",
        "time_min_pnl",
        "time_cut_pnl",
        "cooldown_h",
        "min_score",
        "min_score_bear",
        "max_buys_day",
        "max_trades_day",
        "max_legs",
        "max_major_buys",
        "smart_time",
        "rotate",
        "liquid_only",
        "early_exit_h",
        "early_exit_pnl",
        "extend_threshold",
        "post_loss_cooldown_h",
        "max_consecutive_losses",
        "loss_streak_block_h",
    }
)


def _pct(v: Any) -> float:
    """Coerce knobs that may be fraction (0.012) or percent points (1.2)."""
    if isinstance(v, str):
        s = v.strip().replace("+", "").replace("%", "").strip()
        x = float(s)
        # Strings like "1.2%" / "+1.5%" → always percent points
        if "%" in str(v):
            return x / 100.0
    else:
        x = float(v)
    # Bare numbers: |x|>0.5 ⇒ percent points (1.2 → 1.2%)
    if abs(x) > 0.5:
        return x / 100.0
    return x


def _pct_from_match(num: str) -> float:
    """Regex capture of a percent value (always percent points)."""
    return float(num) / 100.0


def _hours(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace(" ", "")
    if s.endswith("/asset"):
        s = s[: -len("/asset")]
    if s.endswith("h"):
        s = s[:-1]
    return float(s)


def _merge_sleeve(dst: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(dst)
    for k, v in patch.items():
        key = str(k).lower().strip()
        if key not in SLEEVE_FIELDS:
            continue
        if key in {
            "max_buys_day",
            "max_trades_day",
            "max_legs",
            "max_major_buys",
            "max_consecutive_losses",
        }:
            out[key] = int(v)
        elif key in {"smart_time", "rotate", "liquid_only"}:
            out[key] = bool(v)
        else:
            out[key] = float(v)
    return out


def normalize_knobs(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Map copiloto aliases + canonical keys into overlay shape."""
    src = dict(raw or {})
    # lowercase alias lookup without destroying casing of nested
    aliases = {str(k): v for k, v in src.items()}
    lower = {str(k).lower(): v for k, v in src.items()}
    out: dict[str, Any] = {}
    core: dict[str, Any] = {}
    burst: dict[str, Any] = {}

    for key in KNOB_KEYS:
        if key in aliases and key not in ("SLEEVE_CORE", "SLEEVE_BURST"):
            try:
                out[key] = float(aliases[key])
            except (TypeError, ValueError):
                pass

    if isinstance(aliases.get("SLEEVE_CORE"), dict):
        core = _merge_sleeve(core, aliases["SLEEVE_CORE"])
    if isinstance(aliases.get("SLEEVE_BURST"), dict):
        burst = _merge_sleeve(burst, aliases["SLEEVE_BURST"])

    # --- Proposal aliases (copiloto natural names) ---
    if "clip_usd_burst" in lower or "order_usd_burst" in lower:
        clip = float(lower.get("clip_usd_burst", lower.get("order_usd_burst")))
        burst = _merge_sleeve(burst, {"order_min": clip, "order_max": clip, "order_pct": 0.0001})
    if "burst_max_notional_pct" in lower:
        burst = _merge_sleeve(
            burst, {"max_exposure_pct": _pct(lower["burst_max_notional_pct"])}
        )
    if "sl_pct_burst" in lower:
        burst = _merge_sleeve(burst, {"sl": _pct(lower["sl_pct_burst"])})
    if "trail_burst" in lower:
        tb = lower["trail_burst"]
        if isinstance(tb, dict):
            act = tb.get("tp", tb.get("trail_act", tb.get("act")))
            gb = tb.get("sl", tb.get("trail_gb", tb.get("gb")))
            patch = {}
            if act is not None:
                patch["trail_act"] = _pct(act if isinstance(act, str) else f"{act}%")
            if gb is not None:
                patch["trail_gb"] = abs(_pct(gb if isinstance(gb, str) else f"{gb}%"))
            burst = _merge_sleeve(burst, patch)
    if "score_min_burst" in lower:
        burst = _merge_sleeve(burst, {"min_score": float(lower["score_min_burst"])})
    if "score_min_core" in lower:
        core = _merge_sleeve(core, {"min_score": float(lower["score_min_core"])})
        out.setdefault("MIN_BUY_SCORE", float(lower["score_min_core"]))
    if "cooldown_burst" in lower:
        burst = _merge_sleeve(burst, {"cooldown_h": _hours(lower["cooldown_burst"])})
    if "cooldown_burst_post_loss" in lower:
        burst = _merge_sleeve(
            burst, {"post_loss_cooldown_h": _hours(lower["cooldown_burst_post_loss"])}
        )
    if "burst_max_consecutive_losses" in lower:
        burst = _merge_sleeve(
            burst, {"max_consecutive_losses": int(lower["burst_max_consecutive_losses"])}
        )
    if "time_stop_core" in lower:
        # e.g. "3h si PnL < +2.0%" — extract leading hours + optional pnl
        s = str(lower["time_stop_core"])
        hm = re.search(r"([0-9.]+)\s*h", s, re.I)
        pm = re.search(r"([+-]?[0-9.]+)\s*%", s)
        patch: dict[str, Any] = {}
        if hm:
            patch["time_stop_h"] = float(hm.group(1))
        if pm:
            patch["time_min_pnl"] = _pct_from_match(pm.group(1))
            out.setdefault("TIME_MIN_PNL", patch["time_min_pnl"])
        if patch:
            core = _merge_sleeve(core, patch)
            if "time_stop_h" in patch:
                out.setdefault("TIME_STOP_HOURS", patch["time_stop_h"])
    if "time_stop_early_core" in lower:
        s = str(lower["time_stop_early_core"])
        pm = re.search(r"([+-]?[0-9.]+)\s*%", s)
        hm = re.search(r"([0-9.]+)\s*h", s, re.I)
        core = _merge_sleeve(
            core,
            {
                "early_exit_pnl": _pct_from_match(pm.group(1)) if pm else -0.003,
                "early_exit_h": float(hm.group(1)) if hm else 1.0,
            },
        )
    if "time_stop_extension_threshold" in lower:
        raw_ext = lower["time_stop_extension_threshold"]
        if isinstance(raw_ext, str) and "%" in raw_ext:
            core = _merge_sleeve(core, {"extend_threshold": _pct(raw_ext)})
        else:
            core = _merge_sleeve(core, {"extend_threshold": _pct(raw_ext)})
    if "time_stop" in lower and "TIME_STOP_HOURS" not in out:
        s = str(lower["time_stop"])
        hm = re.search(r"([0-9.]+)\s*h", s, re.I)
        pm = re.search(r"([+-]?[0-9.]+)\s*%", s)
        if hm:
            out["TIME_STOP_HOURS"] = float(hm.group(1))
        if pm:
            out["TIME_MIN_PNL"] = _pct_from_match(pm.group(1))
    if "score_min" in lower and "MIN_BUY_SCORE" not in out:
        out["MIN_BUY_SCORE"] = float(lower["score_min"])
    if "trail" in lower and "TRAIL_ACT" not in out:
        tr = lower["trail"]
        if isinstance(tr, dict):
            act = tr.get("tp", tr.get("trail_act"))
            gb = tr.get("sl", tr.get("trail_gb"))
            if act is not None:
                out["TRAIL_ACT"] = _pct(act if isinstance(act, str) else f"{act}%")
            if gb is not None:
                out["TRAIL_GB"] = abs(_pct(gb if isinstance(gb, str) else f"{gb}%"))

    if core:
        out["SLEEVE_CORE"] = core
    if burst:
        out["SLEEVE_BURST"] = burst
    return out


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
    if venue not in ("binance", "alpaca", "alpaca_scalp15", "all"):
        return {"ok": False, "error": "bad_venue", "venue": venue}
    touched: list[str] = []
    if venue in ("binance", "all"):
        global_h = vibe / "live" / "HALT"
        broker_h = vibe / "live" / "binance" / "HALT"
        # also root HALT used by vibe-autotrade loop
        root_h = vibe / "HALT"
        if halt:
            for p in (global_h, broker_h, root_h):
                _write_json(p, _halt_payload(reason))
                touched.append(str(p))
        else:
            for p in (global_h, broker_h, root_h):
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
    if venue in ("alpaca_scalp15", "all"):
        import os

        home = Path(os.environ.get("ALPACA_SCALP15_HOME", "/data/alpaca_scalp15"))
        p = home / "HALT"
        p.parent.mkdir(parents=True, exist_ok=True)
        if halt:
            _write_json(p, _halt_payload(reason))
            touched.append(str(p))
        else:
            if p.exists():
                p.unlink(missing_ok=True)
                touched.append(f"cleared:{p}")
    return {"ok": True, "halt": halt, "venue": venue, "touched": touched, "reason": reason}


def _load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _telegram_mode_notice(
    vibe: Path,
    *,
    venue: str,
    mode: str,
    locked: bool,
    reason: str,
) -> None:
    """Best-effort Telegram ping when Ops forces a mode (Hermes/Vibe chat)."""
    try:
        import urllib.parse
        import urllib.request

        env = _load_dotenv(vibe / ".env")
        token = env.get("TELEGRAM_BOT_TOKEN") or ""
        chat = env.get("TELEGRAM_CHAT_ID") or ""
        if not token or not chat:
            return
        lock_txt = "LOCKED" if locked else "unlocked"
        text = (
            f"[Ops] {venue} modo → {mode} ({lock_txt})\n"
            f"Por qué: {(reason or 'ops_set_mode')[:180]}\n"
            f"Visible en Ops + digests Telegram. Hermes: vibe-status"
        )
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": chat, "text": text[:3500]}
        ).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=12) as resp:
            resp.read()
    except Exception:
        pass


def _persist_binance_situations_from_mode(vibe: Path, doc: dict[str, Any]) -> None:
    """Keep Ops situations file aligned after Ops set_mode (incl. locked)."""
    try:
        mode = str(doc.get("mode") or "")
        reason = str(doc.get("reason") or "")
        locked = bool(doc.get("locked"))
        feats = doc.get("features") or {}
        sits: list[dict[str, str]] = []
        if locked:
            sits.append(
                {
                    "level": "ok",
                    "code": "mode_locked",
                    "text": (
                        f"Modo forzado {mode} (locked"
                        f"{(' por ' + str(doc.get('locked_by'))) if doc.get('locked_by') else ''})"
                    ),
                }
            )
        if mode in ("v6_primary", "defensive"):
            sits.append(
                {
                    "level": "ok",
                    "code": "mode_active",
                    "text": f"Binance activo · mode={mode}",
                }
            )
        if "need_recharge" in reason or "day_edge" in reason:
            sits.append({"level": "warn", "code": "orch_reason", "text": reason[:160]})
        if not sits:
            sits.append({"level": "ok", "code": "clear", "text": "Sin flags especiales"})
        payload = {
            "ts": time.time(),
            "mode": mode,
            "reason": reason,
            "locked": locked,
            "situations": sits,
            "source": "ops_set_mode",
        }
        # lightly include equity if present
        if feats.get("equity") is not None:
            payload["equity"] = feats.get("equity")
        _write_json(vibe / "strategy_situations.json", payload)
    except Exception:
        pass


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
        _persist_binance_situations_from_mode(vibe, doc)
        _telegram_mode_notice(
            vibe,
            venue="Binance",
            mode=mode,
            locked=bool(locked),
            reason=str(doc.get("reason") or ""),
        )
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
    if mode == "scalper":
        mode = "scalp15"
    if mode not in NOTIFY_MODES:
        return {"ok": False, "error": "bad_mode", "allowed": sorted(NOTIFY_MODES - {"scalper"})}
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
    if venue not in ("binance", "alpaca", "alpaca_scalp15"):
        return {"ok": False, "error": "bad_venue"}
    clean = normalize_knobs(knobs)
    # Drop empty / invalid
    final: dict[str, Any] = {}
    for k, v in clean.items():
        if k in ("SLEEVE_CORE", "SLEEVE_BURST"):
            if isinstance(v, dict) and v:
                final[k] = v
            continue
        if k not in KNOB_KEYS:
            continue
        try:
            final[k] = float(v) if not isinstance(v, bool) else v
        except (TypeError, ValueError):
            continue
    if not final:
        return {
            "ok": False,
            "error": "no_valid_knobs",
            "allowed": sorted(KNOB_KEYS),
            "hint": "Use ORDER_USD/SL/... or sleeve aliases (clip_usd_burst, score_min_burst, …)",
        }
    if venue == "binance":
        home = vibe
    elif venue == "alpaca_scalp15":
        import os

        home = Path(os.environ.get("ALPACA_SCALP15_HOME", "/data/alpaca_scalp15"))
    else:
        home = alpaca
    path = home / "v6_knobs_overlay.json"
    prev = _read_json(path)
    merged = {**(prev.get("knobs") or {}), **final}
    # Deep-merge sleeve dicts
    for sk in ("SLEEVE_CORE", "SLEEVE_BURST"):
        if sk in final:
            merged[sk] = {
                **dict((prev.get("knobs") or {}).get(sk) or {}),
                **final[sk],
            }
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
    import os

    bn_mode = _read_json(vibe / "strategy_mode.json")
    al_ctl = _read_json(alpaca / "ops_control.json")
    prefs = _read_json(vibe / "telegram_notify_prefs.json")
    s15 = Path(os.environ.get("ALPACA_SCALP15_HOME", "/data/alpaca_scalp15"))
    return {
        "binance": {
            "mode": bn_mode.get("mode"),
            "locked": bool(bn_mode.get("locked")),
            "halt": (vibe / "live" / "HALT").exists()
            or (vibe / "live" / "binance" / "HALT").exists()
            or (vibe / "HALT").exists(),
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
        "alpaca_scalp15": {
            "halt": (s15 / "HALT").exists(),
            "knobs_overlay": _read_json(s15 / "v6_knobs_overlay.json").get("knobs") or {},
            "queued_intents": _queued_count(s15 / "ops_intents.jsonl"),
            "home": str(s15),
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
