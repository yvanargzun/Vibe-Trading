#!/usr/bin/env python3
"""Dynamic short-term goals for trading bots (daily / weekly / streak).

Replaces the primary 2x narrative with actionable near-term targets that
scale with account size and venue.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

DAY_TZ = ZoneInfo("America/Mexico_City")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    return datetime.now(DAY_TZ)


def utc_day() -> str:
    return utc_now().date().isoformat()


def goal_day(venue: str = "alpaca") -> str:
    """Binance live book uses Ciudad de Mexico day boundary; others UTC."""
    if (venue or "").lower() == "binance":
        return local_now().date().isoformat()
    return utc_day()


def utc_week() -> str:
    # ISO week: 2026-W31
    d = utc_now().date()
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def goal_week(venue: str = "alpaca") -> str:
    if (venue or "").lower() == "binance":
        d = local_now().date()
        iso = d.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return utc_week()


def pick_profile(equity: float, venue: str = "alpaca") -> str:
    v = (venue or "").lower()
    if v == "binance" or equity < 80:
        return "live_micro"
    if equity < 5000:
        return "small_account"
    return "paper_scalp"


# pct of day/week open equity; floors keep tiny books meaningful
PROFILES: dict[str, dict[str, float]] = {
    # Binance-sized live (~$15–50): aggressive % but small $
    "live_micro": {
        "daily_pct": 0.025,  # +2.5%/day
        "weekly_pct": 0.10,  # +10%/week
        "daily_floor": 0.35,
        "weekly_floor": 1.25,
        "daily_cap": 3.0,
        "weekly_cap": 12.0,
    },
    "small_account": {
        "daily_pct": 0.01,
        "weekly_pct": 0.04,
        "daily_floor": 2.0,
        "weekly_floor": 8.0,
        "daily_cap": 40.0,
        "weekly_cap": 160.0,
    },
    # Alpaca paper ~$100k: modest % = real $ progress without fantasy 2x
    "paper_scalp": {
        "daily_pct": 0.0015,  # +0.15%/day ≈ $150 on 100k
        "weekly_pct": 0.008,  # +0.8%/week ≈ $800
        "daily_floor": 75.0,
        "weekly_floor": 400.0,
        "daily_cap": 400.0,
        "weekly_cap": 2000.0,
    },
}


def _clamp_target(raw: float, floor: float, cap: float) -> float:
    return max(floor, min(cap, raw))


def compute_targets(open_eq: float, profile: str) -> tuple[float, float]:
    cfg = PROFILES.get(profile) or PROFILES["paper_scalp"]
    daily = _clamp_target(
        open_eq * cfg["daily_pct"], cfg["daily_floor"], cfg["daily_cap"]
    )
    weekly = _clamp_target(
        open_eq * cfg["weekly_pct"], cfg["weekly_floor"], cfg["weekly_cap"]
    )
    return round(daily, 2), round(weekly, 2)


def ensure_goals(
    state: dict,
    equity: float,
    *,
    venue: str = "alpaca",
) -> dict[str, Any]:
    """Initialize / roll day-week windows. Mutates state['goals']."""
    profile = pick_profile(equity, venue)
    g = dict(state.get("goals") or {})
    day = goal_day(venue)
    week = goal_week(venue)

    # Day roll
    if g.get("day") != day:
        # close previous day into streak
        if g.get("day") and g.get("day_open_equity"):
            prev_open = float(g["day_open_equity"])
            # approximate close = current equity at roll (good enough)
            day_pnl = equity - prev_open
            g["last_day_pnl"] = round(day_pnl, 4)
            if day_pnl > 0:
                g["streak_green_days"] = int(g.get("streak_green_days") or 0) + 1
            else:
                g["streak_green_days"] = 0
        g["day"] = day
        g["day_open_equity"] = round(equity, 4)
        g["daily_hit"] = False
        g["daily_celebrated"] = False

    # Week roll
    if g.get("week") != week:
        g["week"] = week
        g["week_open_equity"] = round(equity, 4)
        g["weekly_hit"] = False
        g["weekly_celebrated"] = False

    g.setdefault("day_open_equity", round(equity, 4))
    g.setdefault("week_open_equity", round(equity, 4))
    g.setdefault("streak_green_days", 0)
    g.setdefault("daily_hits_total", 0)
    g.setdefault("weekly_hits_total", 0)

    # Keep long-term baseline for soft reference only
    if not state.get("double_baseline"):
        state["double_baseline"] = round(equity, 4)

    daily_t, weekly_t = compute_targets(float(g["day_open_equity"]), profile)
    # weekly target from week open
    _, weekly_t = compute_targets(float(g["week_open_equity"]), profile)
    daily_t, _ = compute_targets(float(g["day_open_equity"]), profile)

    g["profile"] = profile
    g["daily_target_usd"] = daily_t
    g["weekly_target_usd"] = weekly_t
    g["venue"] = venue
    state["goals"] = g
    return g


def goal_snapshot(state: dict, equity: float) -> dict[str, Any]:
    g = state.get("goals") or {}
    day_open = float(g.get("day_open_equity") or equity)
    week_open = float(g.get("week_open_equity") or equity)
    daily_t = float(g.get("daily_target_usd") or 0)
    weekly_t = float(g.get("weekly_target_usd") or 0)
    day_pnl = equity - day_open
    week_pnl = equity - week_open
    daily_prog = 0.0 if daily_t <= 0 else max(0.0, min(150.0, day_pnl / daily_t * 100.0))
    weekly_prog = 0.0 if weekly_t <= 0 else max(0.0, min(150.0, week_pnl / weekly_t * 100.0))
    return {
        "profile": g.get("profile"),
        "day_pnl": round(day_pnl, 4),
        "week_pnl": round(week_pnl, 4),
        "daily_target": daily_t,
        "weekly_target": weekly_t,
        "daily_prog": round(daily_prog, 1),
        "weekly_prog": round(weekly_prog, 1),
        "daily_hit": bool(g.get("daily_hit")) or day_pnl >= daily_t > 0,
        "weekly_hit": bool(g.get("weekly_hit")) or week_pnl >= weekly_t > 0,
        "streak": int(g.get("streak_green_days") or 0),
        "day_open": day_open,
        "week_open": week_open,
    }


def progress_lines(state: dict, equity: float) -> list[str]:
    snap = goal_snapshot(state, equity)
    day_sign = "+" if snap["day_pnl"] >= 0 else ""
    week_sign = "+" if snap["week_pnl"] >= 0 else ""
    lines = [
        f"Tu billetera aprox: ${equity:.2f}",
        (
            f"Objetivo de hoy: {day_sign}${snap['day_pnl']:.2f} / "
            f"+${snap['daily_target']:.2f} ({snap['daily_prog']:.0f}%)"
        ),
        (
            f"Objetivo de la semana: {week_sign}${snap['week_pnl']:.2f} / "
            f"+${snap['weekly_target']:.2f} ({snap['weekly_prog']:.0f}%)"
        ),
    ]
    if snap["streak"] > 0:
        lines.append(f"Racha: {snap['streak']} dia(s) en verde")
    if snap["daily_hit"]:
        lines.append("Hoy: meta diaria cumplida")
    elif snap["weekly_hit"]:
        lines.append("Esta semana: meta semanal cumplida")
    return lines


def digest_goal_block(state: dict, equity: float) -> list[str]:
    snap = goal_snapshot(state, equity)
    day_sign = "+" if snap["day_pnl"] >= 0 else ""
    week_sign = "+" if snap["week_pnl"] >= 0 else ""
    lines = [
        "",
        "Objetivos dinamicos (corto plazo)",
        (
            f"- Hoy: {day_sign}${snap['day_pnl']:.2f} de +${snap['daily_target']:.2f} "
            f"({snap['daily_prog']:.0f}%)"
        ),
        (
            f"- Semana: {week_sign}${snap['week_pnl']:.2f} de +${snap['weekly_target']:.2f} "
            f"({snap['weekly_prog']:.0f}%)"
        ),
    ]
    if snap["streak"] > 0:
        lines.append(f"- Racha verde: {snap['streak']} dia(s)")
    if snap["daily_hit"]:
        lines.append("- Estado: meta de HOY cumplida")
    elif snap["daily_prog"] >= 70:
        lines.append("- Estado: cerca de la meta de hoy")
    else:
        lines.append("- Estado: trabajando la meta de hoy")
    return lines


def evaluate_hits(
    state: dict,
    equity: float,
) -> list[dict[str, Any]]:
    """Mark daily/weekly hits; return celebration events (at most one each)."""
    g = dict(state.get("goals") or {})
    snap = goal_snapshot(state, equity)
    events: list[dict[str, Any]] = []

    if snap["daily_target"] > 0 and snap["day_pnl"] >= snap["daily_target"] and not g.get(
        "daily_celebrated"
    ):
        g["daily_hit"] = True
        g["daily_celebrated"] = True
        g["daily_hits_total"] = int(g.get("daily_hits_total") or 0) + 1
        events.append(
            {
                "kind": "daily",
                "pnl": snap["day_pnl"],
                "target": snap["daily_target"],
                "hits": g["daily_hits_total"],
            }
        )

    if snap["weekly_target"] > 0 and snap["week_pnl"] >= snap["weekly_target"] and not g.get(
        "weekly_celebrated"
    ):
        g["weekly_hit"] = True
        g["weekly_celebrated"] = True
        g["weekly_hits_total"] = int(g.get("weekly_hits_total") or 0) + 1
        events.append(
            {
                "kind": "weekly",
                "pnl": snap["week_pnl"],
                "target": snap["weekly_target"],
                "hits": g["weekly_hits_total"],
            }
        )

    state["goals"] = g
    return events


def format_goal_hit_tg(venue_tag: str, event: dict[str, Any], equity: float) -> str:
    if event["kind"] == "daily":
        title = "META DEL DIA"
        body = (
            f"Ganaste ~${event['pnl']:.2f} hoy (meta era +${event['target']:.2f}). "
            f"Metas diarias cumplidas: {event['hits']}."
        )
        nxt = "Siguiente: proteger ganancias y apuntar a la meta de la semana."
    else:
        title = "META DE LA SEMANA"
        body = (
            f"Ganaste ~${event['pnl']:.2f} esta semana (meta era +${event['target']:.2f}). "
            f"Metas semanales cumplidas: {event['hits']}."
        )
        nxt = "Siguiente: el bot reinicia el foco al objetivo diario; sin prisa."
    return "\n".join(
        [
            f"[{venue_tag}] {title} · cumplida",
            body,
            f"Billetera ahora: ${equity:.2f}",
            nxt,
        ]
    )
