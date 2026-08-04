#!/usr/bin/env python3
"""Structured phase log for smart-fast-v6 — easier post-mortem on errors.

Writes JSONL rows to HOME/v6_cycles.jsonl. Each tick has:
  CYCLE_START → PHASE_* → DECISION → CYCLE_END | CYCLE_ERROR

CLI:
  python3 v6_trace.py dump [N]
  python3 v6_trace.py last-error
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

HOME = Path(os.environ.get("VIBE_TRADING_HOME", "/root/.vibe-trading"))
CYCLES_PATH = HOME / "v6_cycles.jsonl"
MAX_LINES = 800
CDMX = ZoneInfo("America/Mexico_City")

_active: "CycleTrace | None" = None


def _append(row: dict[str, Any]) -> None:
    CYCLES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CYCLES_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    try:
        lines = CYCLES_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LINES:
            CYCLES_PATH.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def cdmx_now() -> str:
    return datetime.now(CDMX).strftime("%Y-%m-%d %H:%M:%S%z")


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CycleTrace:
    """One autotrade tick: phases + final decision for grep/jq."""

    def __init__(self, log_fn: Any | None = None) -> None:
        self.cycle_id = uuid.uuid4().hex[:10]
        self.t0 = time.time()
        self.log_fn = log_fn
        self.phases: list[str] = []
        self.decision: dict[str, Any] = {
            "action": "HOLD",
            "symbol": None,
            "reason": "tick_start",
        }
        self.ctx: dict[str, Any] = {}

    def _emit(self, kind: str, **fields: Any) -> None:
        row = {
            "ts": time.time(),
            "ts_utc": utc_iso(),
            "ts_cdmx": cdmx_now(),
            "cycle_id": self.cycle_id,
            "kind": kind,
            **fields,
        }
        _append(row)
        if self.log_fn:
            bits = [f"TRACE[{self.cycle_id}] {kind}"]
            for k, v in fields.items():
                if k in ("exc_tb",) or v is None:
                    continue
                s = str(v)
                if len(s) > 160:
                    s = s[:157] + "..."
                bits.append(f"{k}={s}")
            self.log_fn(" ".join(bits))

    def start(self, **ctx: Any) -> None:
        self.ctx.update(ctx)
        self._emit("CYCLE_START", **ctx)

    def phase(self, name: str, **fields: Any) -> None:
        self.phases.append(name)
        self._emit("PHASE", phase=name, **fields)

    def skip(self, reason: str, **fields: Any) -> None:
        self.decision = {"action": "SKIP", "symbol": fields.get("symbol"), "reason": reason}
        self._emit("SKIP", reason=reason, **fields)

    def decide(self, action: str, *, reason: str, symbol: str | None = None, **fields: Any) -> None:
        self.decision = {"action": action, "symbol": symbol, "reason": reason}
        self._emit("DECISION", action=action, symbol=symbol, reason=reason, **fields)

    def error(self, phase: str, exc: BaseException) -> None:
        tb = traceback.format_exc()
        self.decision = {
            "action": "ERROR",
            "symbol": None,
            "reason": f"{phase}:{type(exc).__name__}:{exc}",
        }
        self._emit(
            "CYCLE_ERROR",
            phase=phase,
            exc_type=type(exc).__name__,
            exc=str(exc)[:500],
            exc_tb=tb[-3000:],
            elapsed_ms=int((time.time() - self.t0) * 1000),
        )

    def end(self, **fields: Any) -> None:
        payload = {
            **self.ctx,
            **fields,
            "phases": list(self.phases),
            "decision": dict(self.decision),
            "elapsed_ms": int((time.time() - self.t0) * 1000),
        }
        self._emit("CYCLE_END", **payload)

    def status_block(self) -> str:
        d = self.decision
        return (
            "## CICLO\n"
            f"id={self.cycle_id} ts_cdmx={cdmx_now()}\n"
            f"modo={self.ctx.get('modo_orch')} sleeve={self.ctx.get('sleeve_usd')} "
            f"usable={self.ctx.get('usable_usdt')} day_pnl={self.ctx.get('day_pnl_pct')}\n"
            f"posiciones={self.ctx.get('posiciones')}\n"
            "## REGIMEN\n"
            f"{self.ctx.get('regime')} btc24={self.ctx.get('btc_chg24')}\n"
            "## DECISION\n"
            f"{d.get('action')} {d.get('symbol') or '-'} · {d.get('reason')}\n"
        )


def current() -> CycleTrace | None:
    return _active


@contextmanager
def cycle(log_fn: Any | None = None, **start_ctx: Any) -> Iterator[CycleTrace]:
    global _active
    tr = CycleTrace(log_fn=log_fn)
    _active = tr
    tr.start(**start_ctx)
    try:
        yield tr
    except Exception as exc:  # noqa: BLE001
        tr.error(tr.phases[-1] if tr.phases else "unknown", exc)
        raise
    finally:
        if _active is tr:
            _active = None


def dump_text(n: int = 8) -> str:
    rows: list[dict[str, Any]] = []
    if CYCLES_PATH.exists():
        for line in CYCLES_PATH.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    ends = [r for r in rows if r.get("kind") in ("CYCLE_END", "CYCLE_ERROR")][-n:]
    out: list[str] = []
    for r in ends:
        cid = r.get("cycle_id")
        related = [x for x in rows if x.get("cycle_id") == cid]
        out.append("=" * 60)
        out.append(
            f"{r.get('kind')} id={cid} cdmx={r.get('ts_cdmx')} "
            f"elapsed_ms={r.get('elapsed_ms')}"
        )
        if r.get("kind") == "CYCLE_ERROR":
            out.append(f"  ERROR phase={r.get('phase')} {r.get('exc_type')}: {r.get('exc')}")
            if r.get("exc_tb"):
                out.append(r["exc_tb"][-800:])
        dec = r.get("decision") or {}
        out.append(
            f"  decision={dec.get('action')} {dec.get('symbol') or '-'} · {dec.get('reason')}"
        )
        out.append(
            f"  modo={r.get('modo_orch')} regime={r.get('regime')} "
            f"sleeve={r.get('sleeve_usd')} usable={r.get('usable_usdt')} "
            f"day_pnl={r.get('day_pnl_pct')} made={r.get('made')}"
        )
        phases = [x.get("phase") for x in related if x.get("kind") == "PHASE"]
        if phases:
            out.append(f"  phases: {' → '.join(str(p) for p in phases)}")
        skips = [x for x in related if x.get("kind") == "SKIP"]
        for s in skips:
            out.append(f"  skip: {s.get('reason')} {s.get('detail') or ''}".rstrip())
    return "\n".join(out) if out else "(no cycles yet)"


def last_error_text() -> str:
    if not CYCLES_PATH.exists():
        return "(no cycles file)"
    last = None
    for line in CYCLES_PATH.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == "CYCLE_ERROR":
            last = row
    if not last:
        return "(no CYCLE_ERROR rows)"
    return (
        f"cycle_id={last.get('cycle_id')} cdmx={last.get('ts_cdmx')}\n"
        f"phase={last.get('phase')} {last.get('exc_type')}: {last.get('exc')}\n"
        f"{last.get('exc_tb') or ''}"
    )


def main() -> None:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "dump").lower()
    if cmd == "last-error":
        print(last_error_text())
        return
    n = 8
    if len(sys.argv) > 2:
        try:
            n = int(sys.argv[2])
        except ValueError:
            pass
    elif cmd.isdigit():
        n = int(cmd)
    print(dump_text(n))


if __name__ == "__main__":
    main()
