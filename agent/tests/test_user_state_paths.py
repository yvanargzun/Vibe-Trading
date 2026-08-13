"""Portable runtime root resolution (``vibe_workspace/`` under project).

Session/run/upload history lives under the project-local workspace so the tree
can be Robocopy'd without depending on ``~/.vibe-trading``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.paths import get_project_anchor, get_runtime_root


def _patch_runtime_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Point all path helpers at ``root`` without using host env vars."""
    from src.config import paths as pathmod

    root.mkdir(parents=True, exist_ok=True)

    def _root(config_path: Path | None = None) -> Path:
        if config_path is not None:
            return Path(config_path).expanduser().resolve().parent
        return root

    monkeypatch.setattr(pathmod, "get_runtime_root", _root)
    # Callers that imported the symbol by name also need the patch.
    monkeypatch.setattr("src.config.paths.get_runtime_root", _root)


def test_runtime_root_defaults_to_project_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBE_TRADING_HOME", raising=False)

    assert get_runtime_root() == get_project_anchor() / "vibe_workspace"
    assert get_runtime_root().is_dir()


def test_explicit_config_path_beats_default(tmp_path: Path) -> None:
    config_path = tmp_path / "explicit" / "agent.json"

    assert get_runtime_root(config_path) == config_path.parent


def test_legacy_swarm_runs_kept_in_run_root_allowlist() -> None:
    """Un-migrated legacy swarm runs must stay reachable, like legacy runs/uploads."""
    from src.tools.path_utils import _agent_root, _default_run_roots

    roots = [p.resolve() for p in _default_run_roots()]

    assert (_agent_root() / ".swarm" / "runs").resolve() in roots


def test_state_dir_helpers_live_under_runtime_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.config.paths import (
        get_data_dir,
        get_kv_cache_dir,
        get_logs_dir,
        get_runs_dir,
        get_sessions_dir,
        get_swarm_runs_dir,
        get_uploads_dir,
    )

    root = tmp_path / "state-root"
    _patch_runtime_root(monkeypatch, root)

    assert get_sessions_dir() == root / "sessions"
    assert get_runs_dir() == root / "runs"
    assert get_swarm_runs_dir() == root / "swarm" / "runs"
    assert get_uploads_dir() == root / "uploads"
    assert get_data_dir() == root / "data"
    assert get_logs_dir() == root / "logs"
    assert get_kv_cache_dir() == root / "kv_cache"


def test_atomic_write_text_roundtrip(tmp_path: Path) -> None:
    from src.config.paths import atomic_write_text

    target = tmp_path / "nested" / "state.json"
    atomic_write_text(target, '{"ok": true}\n')
    assert target.read_text(encoding="utf-8") == '{"ok": true}\n'
    assert not list(tmp_path.rglob("*.tmp"))


def test_legacy_cli_constants_derive_from_runtime_root() -> None:
    from cli import _legacy
    from src.config import paths

    assert _legacy.SESSIONS_DIR == paths.get_sessions_dir()
    assert _legacy.RUNS_DIR == paths.get_runs_dir()
    assert _legacy.SWARM_DIR == paths.get_swarm_runs_dir()
    assert _legacy.UPLOADS_DIR == paths.get_uploads_dir()


def test_agent_loop_constants_derive_from_runtime_root() -> None:
    from src.agent import loop
    from src.config import paths

    assert loop.RUNS_DIR == paths.get_runs_dir()
    assert loop.SESSIONS_DIR == paths.get_sessions_dir()


def test_api_constants_derive_from_runtime_root() -> None:
    from src.api import helpers, uploads_routes
    from src.config import paths

    assert helpers.RUNS_DIR == paths.get_runs_dir()
    assert helpers.SESSIONS_DIR == paths.get_sessions_dir()
    assert helpers.UPLOADS_DIR == paths.get_uploads_dir()
    assert uploads_routes.UPLOADS_DIR == paths.get_uploads_dir()


def test_api_swarm_runtime_uses_swarm_runs_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The HTTP API swarm store must share swarm_runs_root() with CLI/MCP."""
    from src.api import swarm_routes
    from src.swarm.store import swarm_runs_root

    monkeypatch.setattr(swarm_routes, "_swarm_runtime", None)
    monkeypatch.setattr(
        "src.config.load_swarm_agent_config", lambda *a, **k: object()
    )

    runtime = swarm_routes._get_swarm_runtime()

    assert runtime._store.base_dir == swarm_runs_root()
    from src.config.accessor import get_env_config
    from src.swarm.speed import resolve_max_workers

    assert runtime._max_workers == resolve_max_workers(
        get_env_config().swarm.swarm_max_workers
    )


def test_swarm_runs_root_derives_from_runtime_root() -> None:
    from src.config import paths
    from src.swarm.store import swarm_runs_root

    assert swarm_runs_root() == paths.get_swarm_runs_dir()


def test_trace_lookup_searches_runtime_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.agent.trace import TraceWriter

    root = tmp_path / "state-root"
    _patch_runtime_root(monkeypatch, root)
    trace_dir = root / "sessions" / "sess-1"
    trace_dir.mkdir(parents=True)
    (trace_dir / "trace.jsonl").write_text("", encoding="utf-8")

    assert TraceWriter.find_trace_dir("sess-1") == trace_dir


def test_upload_handle_resolves_to_runtime_uploads_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.tools.path_utils import _import_candidate

    root = tmp_path / "state-root"
    _patch_runtime_root(monkeypatch, root)

    assert _import_candidate("uploads/report.pdf") == root / "uploads" / "report.pdf"
    assert (
        _import_candidate("agent/uploads/report.pdf")
        == root / "uploads" / "report.pdf"
    )


def test_sandbox_roots_follow_runtime_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.tools.path_utils import allowed_file_roots, allowed_write_roots

    root = tmp_path / "state-root"
    _patch_runtime_root(monkeypatch, root)

    file_roots = allowed_file_roots()
    write_roots = allowed_write_roots()

    assert (root / "uploads").resolve() in file_roots
    assert (root / "runs").resolve() in file_roots
    assert (root / "uploads").resolve() in write_roots
    assert (root / "runs").resolve() in write_roots


def test_sessions_db_and_goal_db_follow_runtime_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The FTS index and goal store must live beside the sessions they index."""
    import importlib

    from src.goal import store as goal_store
    from src.session import search

    root = tmp_path / "state-root"
    _patch_runtime_root(monkeypatch, root)
    try:
        assert importlib.reload(search)._DB_PATH == root / "sessions.db"
        assert (
            importlib.reload(goal_store)._DEFAULT_DB_PATH == root / "sessions.db"
        )
    finally:
        importlib.reload(search)
        importlib.reload(goal_store)


def test_banner_session_probe_follows_runtime_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import importlib
    import sqlite3

    cli_main = importlib.import_module("cli.main")

    root = tmp_path / "state-root"
    _patch_runtime_root(monkeypatch, root)
    with sqlite3.connect(str(root / "sessions.db")) as conn:
        conn.execute("CREATE TABLE sessions (id TEXT)")
        conn.execute("INSERT INTO sessions VALUES ('a'), ('b')")

    assert cli_main._probe_session_count() == 2


def test_welcome_panel_reports_runtime_root_as_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rich.console import Console

    from cli import _legacy

    monkeypatch.setattr(_legacy, "get_runtime_root", lambda: Path("/RTROOT"))

    console = Console(width=200, record=True)
    console.print(_legacy._build_welcome_panel(term_width=120))

    assert "/RTROOT" in console.export_text()


def test_state_dir_helpers_create_portable_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Portable mode eagerly creates workspace dirs so sync tools see them."""
    from src.config.paths import get_runs_dir, get_sessions_dir

    root = tmp_path / "state-root"
    _patch_runtime_root(monkeypatch, root)

    assert get_sessions_dir() == root / "sessions"
    assert get_runs_dir() == root / "runs"
    assert (root / "sessions").is_dir()
    assert (root / "runs").is_dir()
