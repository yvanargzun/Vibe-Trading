#!/usr/bin/env python3
"""Patch Binance vibe loop + telegram digest for dynamic short-term goals."""

from __future__ import annotations

import shutil
from pathlib import Path

GOALS_SRC = Path("/root/.alpaca-paper/dynamic_goals.py")
GOALS_DST = Path("/root/.vibe-trading/dynamic_goals.py")
LOOP = Path("/root/.vibe-trading/vibe_autotrade_loop.py")
MONITOR = Path("/root/.vibe-trading/telegram_dynamic_monitor.py")


def patch_loop() -> None:
    text = LOOP.read_text(encoding="utf-8")
    if "import dynamic_goals" not in text:
        needle = "from typing import Any\n"
        insert = (
            "from typing import Any\n\n"
            "import sys as _sys\n"
            "_sys.path.insert(0, \"/root/.vibe-trading\")\n"
            "import dynamic_goals as goals\n"
        )
        if needle in text:
            text = text.replace(needle, insert, 1)
        else:
            raise SystemExit("loop: cannot inject import")

    old_prog = '''def _progress_lines(state: dict | None = None) -> list[str]:
    st = state if state is not None else load_state()
    eq = book_equity()
    base = float(st.get("double_baseline") or 0)
    lines = [f"Tu billetera aprox: ${eq:.2f}"]
    if base > 0:
        prog = (eq / (base * 2.0)) * 100.0
        prog = max(0.0, min(100.0, prog))
        lines.append(f"Objetivo: duplicar hasta ~${base * 2:.2f} (vas al {prog:.0f}%)")
    return lines'''

    new_prog = '''def _progress_lines(state: dict | None = None) -> list[str]:
    st = state if state is not None else load_state()
    eq = book_equity()
    goals.ensure_goals(st, eq, venue="binance")
    save_state(st)
    return goals.progress_lines(st, eq)'''

    if old_prog in text:
        text = text.replace(old_prog, new_prog, 1)
    elif "goals.progress_lines" in text:
        print("loop progress already patched")
    else:
        print("WARN loop progress pattern miss")

    old_double = '''def check_double(state: dict) -> None:
    eq = book_equity()
    base = float(state.get("double_baseline") or 0)
    if base <= 0:
        state["double_baseline"] = round(eq, 4)
        save_state(state)
        log(f"BASELINE_SET {eq:.4f}")
        return
    state["equity"] = round(eq, 4)
    state["progress_to_double"] = round(eq / base, 4) if base else 0
    save_state(state)
    if eq >= base * 2:
        tg(format_double_tg(old_base=base, new_eq=eq))
        state["double_baseline"] = round(eq, 4)
        state["doubles"] = int(state.get("doubles") or 0) + 1
        save_state(state)
        log(f"DOUBLED -> new baseline {eq:.4f}")'''

    new_double = '''def check_double(state: dict) -> None:
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
        log(f"DOUBLED soft -> new baseline {eq:.4f}")'''

    if old_double in text:
        text = text.replace(old_double, new_double, 1)
    elif "GOAL_HIT" in text:
        print("loop check_double already patched")
    else:
        print("WARN loop check_double pattern miss")

    # soften double message title if still old
    text = text.replace(
        'f"[{VENUE_TAG}] META CUMPLIDA · duplicaste"',
        'f"[{VENUE_TAG}] META LEJANA · duplicaste"',
    )

    LOOP.write_text(text, encoding="utf-8")
    print("loop patched")


def patch_monitor() -> None:
    text = MONITOR.read_text(encoding="utf-8")
    if "import dynamic_goals" not in text:
        text = text.replace(
            "from pathlib import Path\n",
            "from pathlib import Path\n"
            "import sys as _sys\n"
            "_sys.path.insert(0, \"/root/.vibe-trading\")\n"
            "import dynamic_goals as goals\n",
            1,
        )

    old_block = '''    if baseline > 0:
        target = baseline * 2
        prog = max(0.0, min(100.0, (total / target) * 100.0))
        lines += [
            "",
            "Objetivo simple",
            f"- Quieres duplicar: de ${baseline:.2f} a ~${target:.2f}",
            f"- Avance: vas al {prog:.0f}% del camino",
        ]'''

    new_block = '''    try:
        # load autotrade state for goals window
        st_goals = {}
        try:
            st_goals = json.loads(Path("/root/.vibe-trading/autotrade_state.json").read_text(encoding="utf-8"))
        except Exception:
            st_goals = {"double_baseline": baseline}
        goals.ensure_goals(st_goals, total, venue="binance")
        try:
            Path("/root/.vibe-trading/autotrade_state.json").write_text(
                json.dumps(st_goals, indent=2) + "\\n", encoding="utf-8"
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
            ]'''

    if old_block in text:
        text = text.replace(old_block, new_block, 1)
    elif "digest_goal_block" in text:
        print("monitor goals already patched")
    else:
        print("WARN monitor block miss")

    MONITOR.write_text(text, encoding="utf-8")
    print("monitor patched")


def main() -> None:
    if not GOALS_SRC.exists():
        raise SystemExit(f"missing {GOALS_SRC}")
    shutil.copy2(GOALS_SRC, GOALS_DST)
    print("copied dynamic_goals.py")
    patch_loop()
    patch_monitor()
    print("done")


if __name__ == "__main__":
    main()
