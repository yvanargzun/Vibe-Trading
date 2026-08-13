"""Tests for swarm speed / VRAM policy helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.accessor import reset_env_config
from src.swarm.models import (
    RunStatus,
    SwarmAgentSpec,
    SwarmRun,
    SwarmTask,
    TaskStatus,
)
from src.swarm import speed
from src.swarm.tool_cache import RunToolCache
from src.providers import memory_mgmt


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch):
    reset_env_config()
    yield
    reset_env_config()


def test_resolve_max_workers_safe_always_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_VRAM_MODE", "safe")
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai")
    reset_env_config()
    assert speed.resolve_max_workers(8) == 1


def test_resolve_max_workers_balanced_local_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_VRAM_MODE", "balanced")
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "ollama")
    reset_env_config()
    assert speed.resolve_max_workers(8) == 1


def test_resolve_max_workers_balanced_remote_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SWARM_VRAM_MODE", "balanced")
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    reset_env_config()
    assert speed.resolve_max_workers(8) == 4


def test_trim_upstream_summaries_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_VRAM_MODE", "fast")
    reset_env_config()
    long = "x" * 20_000
    out = speed.trim_upstream_summaries({"a": long})
    assert len(out["a"]) < len(long)
    assert "trimmed" in out["a"]


def test_apply_speed_model_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_WORKER_MODEL", "tiny-worker")
    monkeypatch.setenv("SWARM_LEAD_MODEL", "big-lead")
    monkeypatch.setenv("SWARM_LEAD_AGENT_IDS", "portfolio_manager")
    reset_env_config()

    run = SwarmRun(
        id="swarm-test",
        preset_name="t",
        status=RunStatus.pending,
        user_vars={},
        agents=[
            SwarmAgentSpec(id="bull_advocate", role="Bull", system_prompt="x"),
            SwarmAgentSpec(id="portfolio_manager", role="PM", system_prompt="y"),
            SwarmAgentSpec(
                id="explicit",
                role="X",
                system_prompt="z",
                model_name="yaml-model",
            ),
        ],
        tasks=[
            SwarmTask(id="t1", agent_id="bull_advocate", prompt_template="a"),
            SwarmTask(
                id="t2",
                agent_id="portfolio_manager",
                prompt_template="b",
                depends_on=["t1"],
                status=TaskStatus.blocked,
            ),
        ],
        created_at="2026-01-01T00:00:00+00:00",
    )
    speed.apply_speed_model_overrides(run)
    by_id = {a.id: a for a in run.agents}
    assert by_id["bull_advocate"].model_name == "tiny-worker"
    assert by_id["portfolio_manager"].model_name == "big-lead"
    assert by_id["explicit"].model_name == "yaml-model"


def test_tool_cache_roundtrip(tmp_path: Path) -> None:
    cache = RunToolCache(tmp_path)
    args = {"symbol": "AAPL", "run_dir": "/tmp/x"}
    assert cache.get("get_market_data", args) is None
    cache.put("get_market_data", args, '{"ok": true}')
    # run_dir must not affect key
    assert cache.get("get_market_data", {"symbol": "AAPL", "run_dir": "/other"}) == '{"ok": true}'


def test_release_policy_soft_skips_unload(monkeypatch: pytest.MonkeyPatch) -> None:
    memory_mgmt.set_vram_policy("balanced")
    calls: list[str] = []

    def _fake_unload(model: str | None = None) -> bool:
        calls.append(model or "")
        return True

    monkeypatch.setattr(memory_mgmt, "unload_ollama_model", _fake_unload)
    memory_mgmt.release_gpu_memory(model_name="m1", tag="post_chat")
    memory_mgmt.release_gpu_memory(model_name="m1", tag="post_worker_x")
    assert calls == []  # soft path — no unload
    memory_mgmt.release_gpu_memory(model_name="m1", tag="run_end", force=True)
    assert calls == ["m1"]


def test_estimate_speedup_factors_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARM_VRAM_MODE", "balanced")
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    reset_env_config()
    est = speed.estimate_speedup_factors(layer_widths=[2, 1, 1], llm_calls_per_worker=8)
    assert est["estimated_speedup_x"] >= 2.0
    assert est["effective_max_workers"] >= 1


def test_runtime_honors_resolved_workers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SWARM_VRAM_MODE", "balanced")
    monkeypatch.setenv("LANGCHAIN_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    reset_env_config()

    from src.swarm.runtime import SwarmRuntime
    from src.swarm.store import SwarmStore

    store = SwarmStore(base_dir=tmp_path)
    runtime = SwarmRuntime(store=store, max_workers=8)
    assert runtime._max_workers == 4
