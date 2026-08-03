#!/usr/bin/env python3
"""Patch Binance telegram digest to attach equity chart."""

from __future__ import annotations

import shutil
from pathlib import Path

SRC_CHART = Path("/root/.alpaca-paper/equity_chart.py")
DST_CHART = Path("/root/.vibe-trading/equity_chart.py")
MONITOR = Path("/root/.vibe-trading/telegram_dynamic_monitor.py")
LOOP = Path("/root/.vibe-trading/vibe_autotrade_loop.py")


def patch_monitor() -> None:
    text = MONITOR.read_text(encoding="utf-8")
    if "equity_chart" not in text:
        if "import dynamic_goals as goals" in text:
            text = text.replace(
                "import dynamic_goals as goals\n",
                "import dynamic_goals as goals\nimport equity_chart as equity_chart\n",
                1,
            )
        else:
            text = text.replace(
                "from pathlib import Path\n",
                "from pathlib import Path\n"
                "import sys as _sys\n"
                "_sys.path.insert(0, \"/root/.vibe-trading\")\n"
                "import equity_chart as equity_chart\n",
                1,
            )

    old = '''    ok = tg(token, chat, digest)
    print("STATUS_SENT", ok, f"chg={chg:.2f}% thr={thr:.2f}% open={ost['open_count']} bytes={len(digest)}")'''

    new = '''    # Equity chart + digest (photo caption). Fallback to text if chart fails.
    chart_ok = False
    try:
        st_goals = {}
        try:
            st_goals = json.loads(Path("/root/.vibe-trading/autotrade_state.json").read_text(encoding="utf-8"))
        except Exception:
            st_goals = {}
        g = st_goals.get("goals") or {}
        snap = st_goals.get("goal_snap") or {}
        caption = digest if len(digest) <= 1024 else digest[:1020] + "..."
        chart_ok, _ = equity_chart.build_and_send(
            history_path=Path("/root/.vibe-trading/equity_history.json"),
            chart_path=Path("/root/.vibe-trading/equity_chart.png"),
            token=token,
            chat=chat,
            venue_tag="Binance",
            equity=total,
            caption=caption,
            day_open=float(g.get("day_open_equity") or snap.get("day_open") or total),
            daily_target=float(g.get("daily_target_usd") or snap.get("daily_target") or 0),
            week_open=float(g.get("week_open_equity") or snap.get("week_open") or total),
            weekly_target=float(g.get("weekly_target_usd") or snap.get("weekly_target") or 0),
        )
    except Exception as exc:  # noqa: BLE001
        print("CHART_FAIL", exc)
        chart_ok = False
    ok = chart_ok
    if not chart_ok:
        ok = tg(token, chat, digest)
    elif len(digest) > 1024:
        tg(token, chat, digest)
    print(
        "STATUS_SENT",
        ok,
        f"chart={chart_ok} chg={chg:.2f}% thr={thr:.2f}% open={ost['open_count']} bytes={len(digest)}",
    )'''

    if old in text:
        text = text.replace(old, new, 1)
    elif "chart_ok" in text:
        print("monitor chart already patched")
    else:
        print("WARN monitor send pattern miss")

    MONITOR.write_text(text, encoding="utf-8")
    print("monitor chart patched")


def patch_loop_goal_markers() -> None:
    text = LOOP.read_text(encoding="utf-8")
    if "record_goal_marker" in text:
        print("loop markers already patched")
        return
    if "import dynamic_goals as goals" in text and "import equity_chart" not in text:
        text = text.replace(
            "import dynamic_goals as goals\n",
            "import dynamic_goals as goals\nimport equity_chart as equity_chart\n",
            1,
        )
    old = '''    for event in goals.evaluate_hits(state, eq):
        save_state(state)
        tg(goals.format_goal_hit_tg("Binance", event, eq))
        log(f"GOAL_HIT {event['kind']} pnl={event['pnl']:.2f}")'''
    new = '''    for event in goals.evaluate_hits(state, eq):
        save_state(state)
        tg(goals.format_goal_hit_tg("Binance", event, eq))
        log(f"GOAL_HIT {event['kind']} pnl={event['pnl']:.2f}")
        try:
            hist = Path("/root/.vibe-trading/equity_history.json")
            equity_chart.record_goal_marker(
                hist,
                kind=str(event["kind"]),
                equity=eq,
                label="Meta dia" if event["kind"] == "daily" else "Meta semana",
            )
            png = equity_chart.render_chart(
                hist,
                Path("/root/.vibe-trading/equity_chart_goal.png"),
                venue_tag="Binance",
                equity_now=eq,
                day_open=float((state.get("goals") or {}).get("day_open_equity") or eq),
                daily_target=float((state.get("goals") or {}).get("daily_target_usd") or 0),
                week_open=float((state.get("goals") or {}).get("week_open_equity") or eq),
                weekly_target=float((state.get("goals") or {}).get("weekly_target_usd") or 0),
            )
            env = load_env()
            if png and env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
                equity_chart.send_photo(
                    env["TELEGRAM_BOT_TOKEN"],
                    env["TELEGRAM_CHAT_ID"],
                    png,
                    caption=goals.format_goal_hit_tg("Binance", event, eq)[:1024],
                )
        except Exception as exc:  # noqa: BLE001
            log(f"GOAL_CHART_FAIL {exc}")'''
    if old in text:
        text = text.replace(old, new, 1)
        # ensure Path imported - already is
        LOOP.write_text(text, encoding="utf-8")
        print("loop goal chart patched")
    else:
        print("WARN loop goal hit pattern miss")
        LOOP.write_text(text, encoding="utf-8")


def main() -> None:
    shutil.copy2(SRC_CHART, DST_CHART)
    print("copied equity_chart.py")
    patch_monitor()
    patch_loop_goal_markers()
    print("done")


if __name__ == "__main__":
    main()
