#!/usr/bin/env python3
from __future__ import annotations
import os, subprocess, time
from pathlib import Path
MONITOR = Path("/root/.vibe-trading/telegram_dynamic_monitor.py")
REPORT3 = Path("/root/.vibe-trading/telegram_3day_report.py")
LOG = Path("/root/.vibe-trading/telegram_monitor_loop.log")
PY = Path("/opt/vibe-trade/.venv/bin/python")
INTERVAL = 600


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
    print(line, end="", flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line)


def run_py(script: Path) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = "/opt/vibe-trade/agent:/root/.vibe-trading"
    env["PYTHONIOENCODING"] = "utf-8"
    env["VIBE_TRADING_HOME"] = "/root/.vibe-trading"
    r = subprocess.run(
        [str(PY), str(script)],
        cwd="/opt/vibe-trade",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    out = (r.stdout or "") + (r.stderr or "")
    for line in out.splitlines():
        print(line, flush=True)
        if line.startswith(
            (
                "AGENT_LOOP_TICK_trade_alert",
                "STATUS_SENT",
                "ALERT_SENT",
                "REPORT_SENT",
                "SKIP_NOT_DUE",
                "TG_SKIP",
            )
        ):
            log(line)
    return r.returncode


def main() -> None:
    log("LOOP_START interval=600s host=hetzner-primary")
    while True:
        try:
            code = run_py(MONITOR)
            if code != 0:
                log(f"TICK_FAIL code={code}")
            # Opportunistic 3-day novice report (no-op until due)
            try:
                run_py(REPORT3)
            except Exception as exc:  # noqa: BLE001
                log(f"REPORT3_EXC {exc}")
        except Exception as exc:  # noqa: BLE001
            log(f"TICK_EXC {exc}")
        log(f"SLEEP {INTERVAL}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
