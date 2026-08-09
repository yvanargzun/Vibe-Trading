#!/usr/bin/env python3
"""Adaptive self-learning for Binance smart-fast-v6.

Every close feedback can produce a knob delta written to v6_knobs_overlay.json
(by=adaptive_tuner). Full-auto by default with clamps + daily apply budget.

Env:
  ADAPTIVE_TUNER_APPLY=1|0   (default 1)
  ADAPTIVE_TUNER_NOTIFY=1|0  (default 1)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

HOME = Path(os.environ.get("VIBE_TRADING_HOME", "/root/.vibe-trading"))
OVERLAY_PATH = HOME / "v6_knobs_overlay.json"
JOURNAL_PATH = HOME / "learning_journal.jsonl"
STATE_PATH = HOME / "adaptive_tuner_state.json"
FEEDBACK_PATH = HOME / "strategy_feedback.jsonl"
MODE_PATH = HOME / "strategy_mode.json"
CDMX = ZoneInfo("America/Mexico_City")

APPLY = os.environ.get("ADAPTIVE_TUNER_APPLY", "1").strip() not in ("0", "false", "no")
NOTIFY = os.environ.get("ADAPTIVE_TUNER_NOTIFY", "1").strip() not in ("0", "false", "no")
MAX_APPLIES_PER_DAY = int(os.environ.get("ADAPTIVE_TUNER_MAX_DAY", "12"))
COOLDOWN_SEC = float(os.environ.get("ADAPTIVE_TUNER_COOLDOWN", "90"))
JOURNAL_MAX = 1200

# Safe clamps for overlay keys consumed by v6_config.apply_overlay
CLAMPS: dict[str, tuple[float, float]] = {
    "ORDER_USD": (4.0, 8.0),
    "TP": (0.018, 0.045),
    "SL": (0.010, 0.028),
    "TRAIL_ACT": (0.012, 0.030),
    "TRAIL_GB": (0.006, 0.018),
    "TIME_STOP_HOURS": (2.0, 6.0),
    "TIME_MIN_PNL": (0.006, 0.025),
    "MAX_BUYS_PER_DAY": (2.0, 6.0),
    "MAX_OPEN_LEGS": (1.0, 2.0),
    "MIN_BUY_SCORE": (2.80, 4.80),
    "MIN_BUY_SCORE_BEAR": (3.20, 5.20),
    "MIN_BUY_SCORE_GRACE": (2.40, 4.00),
    "COOLDOWN_HOURS": (2.0, 8.0),
    "SL_MICRO": (0.008, 0.020),
    "TIME_STOP_HOURS_MICRO": (1.5, 4.0),
    "TIME_MIN_PNL_MICRO": (0.004, 0.015),
    "EARLY_EXIT_H": (0.4, 1.5),
    "EARLY_EXIT_PNL": (-0.010, -0.001),
    "GRACE_MAX_BUYS": (1.0, 2.0),
    "GRACE_MAX_BUYS_GREEN": (1.0, 3.0),
}

# Soft baselines to decay toward on "mantener"
BASELINE: dict[str, float] = {
    "ORDER_USD": 5.5,
    "TP": 0.030,
    "SL": 0.018,
    "TRAIL_ACT": 0.020,
    "TRAIL_GB": 0.010,
    "TIME_STOP_HOURS": 4.0,
    "TIME_MIN_PNL": 0.015,
    "MAX_BUYS_PER_DAY": 4.0,
    "MAX_OPEN_LEGS": 1.0,
    "MIN_BUY_SCORE": 3.50,
    "MIN_BUY_SCORE_BEAR": 4.00,
    "MIN_BUY_SCORE_GRACE": 2.90,
    "COOLDOWN_HOURS": 4.0,
    "SL_MICRO": 0.012,
    "TIME_STOP_HOURS_MICRO": 2.5,
    "TIME_MIN_PNL_MICRO": 0.008,
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, doc: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _append_journal(row: dict[str, Any]) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    try:
        lines = JOURNAL_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > JOURNAL_MAX:
            JOURNAL_PATH.write_text("\n".join(lines[-JOURNAL_MAX:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def recent_learning(limit: int = 40) -> list[dict[str, Any]]:
    if not JOURNAL_PATH.exists():
        return []
    try:
        lines = JOURNAL_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(limit, 1) :]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


def _clamp(key: str, value: float) -> float:
    lo, hi = CLAMPS.get(key, (value, value))
    return max(lo, min(hi, float(value)))


def _current_knobs() -> dict[str, float]:
    """Effective knobs = baseline + overlay."""
    out = dict(BASELINE)
    ov = _read_json(OVERLAY_PATH)
    for k, v in (ov.get("knobs") or {}).items():
        if k in CLAMPS:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                continue
    return out


def _state() -> dict[str, Any]:
    st = _read_json(STATE_PATH)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if st.get("applies_day") != day:
        st["applies_day"] = day
        st["applies_today"] = 0
    return st


def _save_state(st: dict[str, Any]) -> None:
    st["updated_ts"] = time.time()
    _write_json(STATE_PATH, st)


def _equity() -> float:
    feats = (_read_json(MODE_PATH).get("features") or {})
    try:
        return float(feats.get("equity") or 0)
    except (TypeError, ValueError):
        return 0.0


def propose_patch(feedback: dict[str, Any], knobs: dict[str, float]) -> tuple[dict[str, float], str]:
    """Map one feedback row → absolute knob targets + human reason."""
    action = str(feedback.get("action") or "mantener")
    title = str(feedback.get("title") or "").lower()
    detail = str(feedback.get("detail") or "").lower()
    kind = str(feedback.get("exit_kind") or feedback.get("exit_reason") or "").lower()
    result = str(feedback.get("result") or "").lower()
    pnl = feedback.get("pnl_pct")
    try:
        pnl_f = float(pnl) if pnl is not None else None
    except (TypeError, ValueError):
        pnl_f = None
    eq = float(feedback.get("equity") or _equity() or 0)
    patch: dict[str, float] = {}
    reasons: list[str] = []

    def bump(key: str, delta: float) -> None:
        cur = float(knobs.get(key, BASELINE.get(key, 0)))
        patch[key] = _clamp(key, cur + delta)

    def setv(key: str, value: float) -> None:
        patch[key] = _clamp(key, value)

    def decay(key: str, frac: float = 0.15) -> None:
        """Move a bit toward baseline (keep learning alive on 'mantener')."""
        cur = float(knobs.get(key, BASELINE.get(key, 0)))
        base = float(BASELINE.get(key, cur))
        setv(key, cur + (base - cur) * frac)

    if action == "ajustar":
        if "sl" in title or "stop" in kind or "sl tocado" in title:
            bump("MIN_BUY_SCORE", 0.15)
            bump("MIN_BUY_SCORE_BEAR", 0.10)
            bump("MIN_BUY_SCORE_GRACE", 0.10)
            bump("SL", -0.001)
            bump("SL_MICRO", -0.001)
            reasons.append("SL/loss → más selectivo + SL un poco más justado")
        elif "time" in title or "time" in kind:
            bump("TIME_STOP_HOURS", -0.25)
            bump("TIME_STOP_HOURS_MICRO", -0.15)
            bump("MIN_BUY_SCORE", 0.10)
            bump("COOLDOWN_HOURS", 0.25)
            reasons.append("Time-stop en rojo → menos paciencia + cooldown")
        elif "fee" in title or "fee" in detail:
            bump("MIN_BUY_SCORE", 0.08)
            bump("MAX_BUYS_PER_DAY", -1)
            setv("GRACE_MAX_BUYS", 1)
            reasons.append("Fee pressure → menos buys, más score")
        elif "recarga" in title or "need_recharge" in detail or "recharge" in title:
            # Never size up on micro; tighten hunting
            bump("MIN_BUY_SCORE_GRACE", 0.05)
            setv("GRACE_MAX_BUYS", 1)
            setv("MAX_BUYS_PER_DAY", min(float(knobs.get("MAX_BUYS_PER_DAY", 4)), 3))
            reasons.append("Micro equity → grace estricta, sin size-up")
        elif "funding" in title or "unlock" in title or "polvo" in detail:
            bump("TIME_STOP_HOURS_MICRO", -0.1)
            bump("EARLY_EXIT_H", -0.05)
            reasons.append("Funding/unlock noise → exits micro un poco más cortos")
        elif result == "loss":
            bump("MIN_BUY_SCORE", 0.12)
            bump("COOLDOWN_HOURS", 0.5)
            reasons.append("Pérdida → score↑ + cooldown↑")
        else:
            bump("MIN_BUY_SCORE", 0.08)
            reasons.append("Ajuste genérico: score↑")
    else:
        # mantener — still apply a tiny adaptive drift so every feedback changes something
        if result == "win" and pnl_f is not None and pnl_f >= 0.015:
            bump("MIN_BUY_SCORE", -0.05)
            decay("COOLDOWN_HOURS", 0.20)
            reasons.append("Win sólido → leve afloje de score + decay cooldown")
        elif result == "win":
            decay("MIN_BUY_SCORE", 0.10)
            decay("SL", 0.08)
            reasons.append("Win → decay suave hacia baseline")
        else:
            decay("MIN_BUY_SCORE", 0.08)
            decay("TIME_STOP_HOURS", 0.08)
            reasons.append("Mantener → decay suave (aprendizaje continuo)")

    # Hard safety: never raise ORDER on micro book
    if eq > 0 and eq < 50 and "ORDER_USD" in patch:
        if patch["ORDER_USD"] > float(knobs.get("ORDER_USD", 5.5)):
            patch.pop("ORDER_USD", None)
            reasons.append("bloqueado ORDER↑ con equity<$50")

    # Drop no-op patches (same after clamp within epsilon)
    cleaned: dict[str, float] = {}
    for k, v in patch.items():
        cur = float(knobs.get(k, BASELINE.get(k, v)))
        if abs(v - cur) >= 1e-6:
            cleaned[k] = round(v, 6) if abs(v) < 10 else round(v, 4)

    reason = "; ".join(reasons) if reasons else "sin regla"
    return cleaned, reason


def _merge_overlay(patch: dict[str, float], *, by: str, note: str) -> dict[str, Any]:
    prev = _read_json(OVERLAY_PATH)
    knobs = dict(prev.get("knobs") or {})
    before = {k: knobs.get(k, BASELINE.get(k)) for k in patch}
    for k, v in patch.items():
        knobs[k] = v
    doc = {
        "knobs": knobs,
        "updated_ts": time.time(),
        "by": by,
        "note": note[:240],
        "source": "adaptive_tuner",
    }
    _write_json(OVERLAY_PATH, doc)
    return {"before": before, "after": {k: knobs[k] for k in patch}, "doc": doc}


def _notify(text: str) -> None:
    if not NOTIFY:
        return
    try:
        from telegram_notify_prefs import send_text

        send_text(text[:3500], channel="vibe", dedupe=False)
    except Exception:
        try:
            from telegram_notify_prefs import tg_api, load_env

            chat = load_env().get("TELEGRAM_CHAT_ID")
            if chat:
                tg_api("sendMessage", {"chat_id": chat, "text": text[:3500]})
        except Exception as exc:  # noqa: BLE001
            print(f"LEARN_TG_FAIL {exc}", flush=True)


def learn_from_feedback(feedback: dict[str, Any] | None, *, force: bool = False) -> dict[str, Any]:
    """Apply learning from one feedback row. Always journals; applies if policy allows."""
    if not feedback:
        return {"ok": False, "error": "no_feedback"}

    st = _state()
    now = time.time()
    if not force:
        if now - float(st.get("last_apply_ts") or 0) < COOLDOWN_SEC:
            return {"ok": False, "error": "cooldown", "wait_s": COOLDOWN_SEC}
        if int(st.get("applies_today") or 0) >= MAX_APPLIES_PER_DAY:
            return {"ok": False, "error": "daily_cap", "cap": MAX_APPLIES_PER_DAY}

    knobs = _current_knobs()
    patch, reason = propose_patch(feedback, knobs)
    fb_id = f"{feedback.get('ts')}:{feedback.get('symbol')}:{feedback.get('action')}"

    if not patch:
        row = {
            "ts": now,
            "ts_cdmx": datetime.now(CDMX).strftime("%Y-%m-%d %H:%M:%S"),
            "applied": False,
            "action": feedback.get("action"),
            "symbol": feedback.get("symbol"),
            "title": feedback.get("title"),
            "reason": reason,
            "patch": {},
            "feedback_id": fb_id,
            "note": "sin delta (ya en límite/clamp)",
        }
        _append_journal(row)
        # Still count as learning touch: tiny journal-only "heartbeat" bump of timestamp
        st["last_feedback_id"] = fb_id
        st["last_learn_ts"] = now
        _save_state(st)
        return {"ok": True, "applied": False, "row": row}

    if not APPLY:
        row = {
            "ts": now,
            "ts_cdmx": datetime.now(CDMX).strftime("%Y-%m-%d %H:%M:%S"),
            "applied": False,
            "action": feedback.get("action"),
            "symbol": feedback.get("symbol"),
            "title": feedback.get("title"),
            "reason": reason,
            "patch": patch,
            "feedback_id": fb_id,
            "note": "ADAPTIVE_TUNER_APPLY=0 (solo propuesto)",
        }
        _append_journal(row)
        st["last_feedback_id"] = fb_id
        st["last_learn_ts"] = now
        _save_state(st)
        return {"ok": True, "applied": False, "row": row}

    merged = _merge_overlay(patch, by="adaptive_tuner", note=reason)
    row = {
        "ts": now,
        "ts_cdmx": datetime.now(CDMX).strftime("%Y-%m-%d %H:%M:%S"),
        "applied": True,
        "action": feedback.get("action"),
        "symbol": feedback.get("symbol"),
        "title": feedback.get("title"),
        "reason": reason,
        "patch": patch,
        "before": merged["before"],
        "after": merged["after"],
        "feedback_id": fb_id,
        "equity": feedback.get("equity") or _equity(),
    }
    _append_journal(row)
    st["last_feedback_id"] = fb_id
    st["last_apply_ts"] = now
    st["last_learn_ts"] = now
    st["applies_today"] = int(st.get("applies_today") or 0) + 1
    _save_state(st)

    deltas = ", ".join(f"{k}:{merged['before'].get(k)}→{v}" for k, v in patch.items())
    _notify(
        f"[Aprende · Binance] AUTO\n"
        f"{feedback.get('symbol')} · {feedback.get('action')} · {feedback.get('title')}\n"
        f"{reason}\n"
        f"Knobs: {deltas}"
    )
    print(f"LEARN_APPLY {feedback.get('symbol')} {patch}", flush=True)
    return {"ok": True, "applied": True, "row": row}


def maybe_tune(*, force: bool = False) -> dict[str, Any]:
    """Process newest unlearned feedback row (for orch tick / cron)."""
    if not FEEDBACK_PATH.exists():
        return {"ok": False, "error": "no_feedback_file"}
    try:
        lines = FEEDBACK_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if not lines:
        return {"ok": False, "error": "empty"}
    try:
        latest = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad_json"}
    st = _state()
    fb_id = f"{latest.get('ts')}:{latest.get('symbol')}:{latest.get('action')}"
    if not force and st.get("last_feedback_id") == fb_id:
        return {"ok": True, "skipped": "already_learned", "feedback_id": fb_id}
    return learn_from_feedback(latest, force=force)


if __name__ == "__main__":
    import pprint

    pprint.pp(maybe_tune(force=True))
