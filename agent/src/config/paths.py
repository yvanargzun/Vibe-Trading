"""Path helpers for agent-level structured config.

Portable runtime layout
-----------------------
All dynamic state lives under ``<project>/vibe_workspace/`` so the repo can be
copied/synced with Robocopy without depending on ``~/.vibe-trading`` or host
env vars. Explicit ``config_path`` still pins the root to that file's parent
(for tests and one-off overrides).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_DEFAULT_FILENAMES = ("agent.json", "agent.yaml", "agent.yml")
_WORKSPACE_DIRNAME = "vibe_workspace"


def get_project_anchor() -> Path:
    """Return the Vibe-Trade project root (parent of ``agent/``).

    Resolved from this module's location so cwd changes do not move state.
    ``agent/src/config/paths.py`` → parents[3] == project root.
    """
    return Path(__file__).resolve().parents[3]


def get_runtime_root(config_path: Path | None = None) -> Path:
    """Return the portable runtime root for user-level agent state.

    Args:
        config_path: Optional explicit config file path. When provided, the
            runtime root is derived from that file's parent directory.

    Returns:
        Config parent when ``config_path`` is set; otherwise
        ``<project>/vibe_workspace`` (created if missing).
    """
    if config_path is not None:
        return config_path.expanduser().resolve().parent
    root = get_project_anchor() / _WORKSPACE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_sessions_dir() -> Path:
    """Return the directory holding chat session records."""
    p = get_runtime_root() / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_runs_dir() -> Path:
    """Return the directory holding run artifacts."""
    p = get_runtime_root() / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_swarm_runs_dir() -> Path:
    """Return the directory holding swarm run records."""
    p = get_runtime_root() / "swarm" / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_uploads_dir() -> Path:
    """Return the directory holding uploaded files."""
    p = get_runtime_root() / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_logs_dir() -> Path:
    """Return the portable logs directory."""
    p = get_runtime_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_kv_cache_dir() -> Path:
    """Return the on-disk KV-cache / inference offload directory."""
    p = get_runtime_root() / "kv_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_config_candidates(config_path: Path | None = None) -> list[Path]:
    """Return supported config path candidates in lookup order."""
    if config_path is not None:
        return [config_path.expanduser()]
    root = get_runtime_root()
    return [root / filename for filename in _DEFAULT_FILENAMES]


def get_config_path(config_path: Path | None = None) -> Path:
    """Return the active config file path."""
    candidates = get_config_candidates(config_path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def get_data_dir(config_path: Path | None = None) -> Path:
    """Return and create the portable data directory (duckdb, channel state).

    Args:
        config_path: Optional explicit config file path.

    Returns:
        ``<runtime_root>/data`` — created when missing.
    """
    data_dir = get_runtime_root(config_path) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_workspace_path() -> Path:
    """Return channel adapter workspace under the portable runtime root."""
    p = get_runtime_root() / "workspace"
    p.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Write text atomically so Robocopy/sync never sees a half-written file.

    Writes to a same-directory temp file, fsyncs, then ``os.replace``s onto
    the destination. The file handle is always closed before replace.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.",
        suffix=".tmp",
        dir=str(dest.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, dest)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """Write bytes atomically (same contract as :func:`atomic_write_text`)."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{dest.name}.",
        suffix=".tmp",
        dir=str(dest.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, dest)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise
