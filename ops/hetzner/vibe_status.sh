#!/usr/bin/env bash
# Quick Binance strategy status for Hermes / operators.
set -euo pipefail
HOME_DIR="${VIBE_HOME:-/root/.vibe-trading}"
python3 - <<'PY' "$HOME_DIR"
import json, sys
from pathlib import Path
home = Path(sys.argv[1])
mode = {}
st = {}
try:
    mode = json.loads((home / "strategy_mode.json").read_text())
except Exception as e:
    print(f"mode_err={e}")
try:
    st = json.loads((home / "autotrade_state.json").read_text())
except Exception as e:
    print(f"state_err={e}")
m = mode.get("mode") or "?"
locked = bool(mode.get("locked"))
reason = str(mode.get("reason") or "")[:160]
eq = float(st.get("equity") or (mode.get("features") or {}).get("equity") or 0)
usable = float((mode.get("features") or {}).get("usable_usdt") or 0)
print("[Binance · smart-fast-v6]")
print(f"mode={m} locked={locked} by={mode.get('locked_by')}")
print(f"active={m in ('v6_primary','defensive')}")
print(f"equity=${eq:.2f} usable=${usable:.2f}")
print(f"regime={st.get('regime')} buys_today={st.get('buys_today')} last={st.get('last_symbol')}")
print(f"reason={reason or '—'}")
halt = (home / "HALT").exists()
print(f"halt={halt}")
# learning snapshot
try:
    journal = home / "learning_journal.jsonl"
    ov = json.loads((home / "v6_knobs_overlay.json").read_text()) if (home / "v6_knobs_overlay.json").exists() else {}
    stt = json.loads((home / "adaptive_tuner_state.json").read_text()) if (home / "adaptive_tuner_state.json").exists() else {}
    print(f"learn_applies_today={stt.get('applies_today')} overlay_by={ov.get('by')} knobs={len(ov.get('knobs') or {})}")
    if journal.exists():
        lines = journal.read_text(encoding="utf-8").splitlines()
        if lines:
            last = json.loads(lines[-1])
            print(f"last_learn applied={last.get('applied')} {last.get('reason')}")
except Exception as e:
    print(f"learn_err={e}")
PY
