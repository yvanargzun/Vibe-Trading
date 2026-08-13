"""Cross-task tool result cache for a single swarm run.

Caches idempotent read-only tool payloads under ``run_dir/tool_cache/`` so
parallel or sequential workers in the same run reuse market/skill fetches.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _stable_key(tool_name: str, arguments: dict[str, Any]) -> str:
    # Strip worker-private path injection; same logical call must share a key.
    cleaned = {
        k: v
        for k, v in sorted(arguments.items())
        if k not in {"run_dir", "artifact_dir"}
    }
    blob = json.dumps({"tool": tool_name, "args": cleaned}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class RunToolCache:
    """Process-safe (thread-safe) disk cache scoped to one swarm run directory."""

    def __init__(self, run_dir: Path) -> None:
        self._dir = Path(run_dir) / "tool_cache"
        self._dir.mkdir(parents=True, exist_ok=True)

    def get(self, tool_name: str, arguments: dict[str, Any]) -> str | None:
        key = _stable_key(tool_name, arguments)
        path = self._dir / f"{tool_name}__{key}.json"
        with _lock:
            if not path.is_file():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                result = data.get("result")
                return result if isinstance(result, str) else None
            except Exception:
                logger.debug("tool cache read failed for %s", path, exc_info=True)
                return None

    def put(self, tool_name: str, arguments: dict[str, Any], result: str) -> None:
        key = _stable_key(tool_name, arguments)
        path = self._dir / f"{tool_name}__{key}.json"
        payload = {"tool": tool_name, "result": result}
        with _lock:
            try:
                from src.config.paths import atomic_write_text

                atomic_write_text(path, json.dumps(payload, ensure_ascii=False) + "\n")
            except Exception:
                try:
                    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
                except Exception:
                    logger.debug("tool cache write failed for %s", path, exc_info=True)


def get_run_tool_cache(run_dir: Path) -> RunToolCache:
    return RunToolCache(run_dir)
