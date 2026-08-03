#!/usr/bin/env python3
from pathlib import Path
import shutil

shutil.copy2("/root/.alpaca-paper/equity_chart.py", "/root/.vibe-trading/equity_chart.py")
# also copy from /tmp if alpaca copy happens after
if Path("/tmp/equity_chart.py").exists():
    shutil.copy2("/tmp/equity_chart.py", "/root/.alpaca-paper/equity_chart.py")
    shutil.copy2("/tmp/equity_chart.py", "/root/.vibe-trading/equity_chart.py")

MONITOR = Path("/root/.vibe-trading/telegram_dynamic_monitor.py")
text = MONITOR.read_text(encoding="utf-8")

old = '''    # Equity chart + digest (photo caption). Fallback to text if chart fails.
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
    )
'''

new = '''    # Text alert first, then chart BELOW.
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
            chart_caption="[Binance] Capital en el tiempo",
        )
    except Exception as exc:  # noqa: BLE001
        print("CHART_FAIL", exc)
        text_ok = tg(token, chat, digest)
        chart_ok = False
    if not text_ok and not chart_ok:
        text_ok = tg(token, chat, digest)
    ok = text_ok or chart_ok
    print(
        "STATUS_SENT",
        ok,
        f"text={text_ok} chart={chart_ok} chg={chg:.2f}% thr={thr:.2f}% open={ost['open_count']} bytes={len(digest)}",
    )
'''

if old not in text:
    raise SystemExit("OLD BLOCK NOT FOUND")
MONITOR.write_text(text.replace(old, new, 1), encoding="utf-8")
print("OK patched")

LOOP = Path("/root/.vibe-trading/vibe_autotrade_loop.py")
lt = LOOP.read_text(encoding="utf-8")
old_cap = 'caption=goals.format_goal_hit_tg("Binance", event, eq)[:1024],'
new_cap = 'caption="[Binance] Grafica · meta " + str(event["kind"]),'
if old_cap in lt:
    LOOP.write_text(lt.replace(old_cap, new_cap, 1), encoding="utf-8")
    print("OK loop caption")
else:
    print("loop caption skip")
