"""Swarm speed / VRAM policy: hybrid concurrency, model overrides, context trim.

``SWARM_VRAM_MODE``:
  * ``safe``     — always ``max_workers=1``; aggressive unload between workers
  * ``balanced`` — parallel when remote; sequential when local (default)
  * ``fast``     — remote parallel; local adaptive 1–3 from free VRAM
"""

from __future__ import annotations

import logging
import os
import re
from typing import Literal

from src.swarm.models import SwarmRun

logger = logging.getLogger(__name__)

VramMode = Literal["safe", "balanced", "fast"]
_VALID_MODES = frozenset({"safe", "balanced", "fast"})

# Heuristic: last-layer / decision roles keep the lead model.
_LEAD_ID_HINTS = re.compile(
    r"(portfolio_manager|pm\b|chair|decision|aggregator|synthesizer|cio|lead)",
    re.IGNORECASE,
)

# Upstream summary char caps by mode (None = no trim).
_SUMMARY_CAPS: dict[str, int | None] = {
    "safe": None,
    "balanced": 12_000,
    "fast": 6_000,
}


def get_vram_mode() -> VramMode:
    """Return configured VRAM/speed mode (default ``balanced``)."""
    try:
        from src.config.accessor import get_env_config

        raw = (get_env_config().swarm.swarm_vram_mode or "balanced").strip().lower()
    except Exception:
        raw = (os.environ.get("SWARM_VRAM_MODE") or "balanced").strip().lower()
    if raw not in _VALID_MODES:
        logger.warning("Unknown SWARM_VRAM_MODE=%r; using balanced", raw)
        return "balanced"
    return raw  # type: ignore[return-value]


def is_local_llm_provider() -> bool:
    """True when the configured provider runs on this host (Ollama / local OpenAI)."""
    try:
        from src.config.accessor import get_env_config

        cfg = get_env_config()
        provider = (cfg.llm.langchain_provider or "").strip().lower()
    except Exception:
        provider = (os.environ.get("LANGCHAIN_PROVIDER") or "").strip().lower()

    if provider in {"ollama", "local", "llama.cpp", "llamacpp", "lmstudio"}:
        return True

    base = (
        os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or os.environ.get("OLLAMA_HOST")
        or os.environ.get("OLLAMA_BASE_URL")
        or ""
    ).strip().lower()
    if not base:
        return provider == "ollama"
    # Local loopback / LAN OpenAI-compatible endpoints count as local VRAM.
    local_markers = (
        "127.0.0.1",
        "localhost",
        "0.0.0.0",
        "::1",
        "192.168.",
        "10.",
        ":11434",
        ":1234",
        ":8080/v1",
    )
    return any(m in base for m in local_markers)


def estimate_free_vram_mb() -> int | None:
    """Best-effort free VRAM in MiB; ``None`` if CUDA unavailable."""
    try:
        import torch  # type: ignore

        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return int(free // (1024 * 1024))
    except Exception:
        return None


def resolve_max_workers(requested: int | None = None) -> int:
    """Resolve effective intra-layer concurrency from mode + provider + VRAM.

    Args:
        requested: Caller hint (usually ``SWARM_MAX_WORKERS``). ``None`` reads env.

    Returns:
        Clamped worker count (>= 1).
    """
    if requested is None:
        try:
            from src.config.accessor import get_env_config

            requested = int(get_env_config().swarm.swarm_max_workers)
        except Exception:
            requested = int(os.environ.get("SWARM_MAX_WORKERS") or "4")
    requested = max(1, int(requested))
    mode = get_vram_mode()
    local = is_local_llm_provider()

    if mode == "safe":
        return 1

    if mode == "balanced":
        if local:
            return 1
        return min(requested, 4)

    # fast
    if not local:
        return min(requested, 8)

    free = estimate_free_vram_mb()
    if free is None:
        return 1
    if free >= 12_000:
        return min(requested, 3)
    if free >= 8_000:
        return min(requested, 2)
    return 1


def summary_char_cap() -> int | None:
    """Max characters per upstream summary for the active mode."""
    return _SUMMARY_CAPS.get(get_vram_mode())


def trim_upstream_summaries(upstream: dict[str, str]) -> dict[str, str]:
    """Trim upstream text when speed mode asks for shorter context."""
    cap = summary_char_cap()
    if not cap or not upstream:
        return upstream
    out: dict[str, str] = {}
    for key, text in upstream.items():
        if len(text) <= cap:
            out[key] = text
            continue
        keep = max(0, cap - 80)
        out[key] = text[:keep] + "\n\n[…trimmed for swarm speed mode…]"
    return out


def _lead_agent_ids(run: SwarmRun) -> set[str]:
    """Agents that should keep the heavyweight / lead model."""
    try:
        from src.config.accessor import get_env_config

        raw = (get_env_config().swarm.swarm_lead_agent_ids or "").strip()
    except Exception:
        raw = (os.environ.get("SWARM_LEAD_AGENT_IDS") or "").strip()
    if raw:
        return {x.strip() for x in raw.split(",") if x.strip()}

    # Auto: last topological layer + id/role heuristics.
    from src.swarm.task_store import topological_layers

    leads: set[str] = set()
    try:
        layers = topological_layers(run.tasks)
        if layers:
            task_by_id = {t.id: t for t in run.tasks}
            for tid in layers[-1]:
                task = task_by_id.get(tid)
                if task:
                    leads.add(task.agent_id)
    except Exception:
        logger.debug("lead-layer detect failed", exc_info=True)

    for agent in run.agents:
        blob = f"{agent.id} {agent.role}"
        if _LEAD_ID_HINTS.search(blob):
            leads.add(agent.id)
    return leads


def is_free_tier_config() -> bool:
    """Detect zero-cost model config (``SWARM_FREE_TIER`` or ``:free`` model ids)."""
    raw = ""
    try:
        from src.config.accessor import get_env_config

        cfg = get_env_config()
        raw = (cfg.swarm.swarm_free_tier or "auto").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        model = (cfg.llm.langchain_model_name or "").strip()
        worker = (cfg.swarm.swarm_worker_model or "").strip()
        lead = (cfg.swarm.swarm_lead_model or "").strip()
    except Exception:
        raw = (os.environ.get("SWARM_FREE_TIER") or "auto").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        model = (os.environ.get("LANGCHAIN_MODEL_NAME") or "").strip()
        worker = (os.environ.get("SWARM_WORKER_MODEL") or "").strip()
        lead = (os.environ.get("SWARM_LEAD_MODEL") or "").strip()
    return any(x.endswith(":free") for x in (model, worker, lead) if x)


def apply_free_tier_agent_limits(run: SwarmRun) -> None:
    """Clamp iterations/timeouts when running on free-tier models."""
    if not is_free_tier_config():
        return
    try:
        from src.config.accessor import get_env_config

        swarm = get_env_config().swarm
        max_iter = int(swarm.swarm_free_max_iter)
        timeout = int(swarm.swarm_free_timeout_s)
    except Exception:
        max_iter = int(os.environ.get("SWARM_FREE_MAX_ITER") or "20")
        timeout = int(os.environ.get("SWARM_FREE_TIMEOUT_S") or "600")
    for agent in run.agents:
        if agent.max_iterations and agent.max_iterations > max_iter:
            agent.max_iterations = max_iter
        if agent.timeout_seconds and agent.timeout_seconds > timeout:
            agent.timeout_seconds = timeout
    logger.info(
        "SWARM_FREE_TIER clamps max_iterations<=%s timeout_seconds<=%s",
        max_iter,
        timeout,
    )


def apply_speed_model_overrides(run: SwarmRun) -> None:
    """Assign ``SWARM_WORKER_MODEL`` / ``SWARM_LEAD_MODEL`` when YAML omits model_name.

    Mutates ``run.agents`` in place. Explicit preset ``model_name`` always wins.
    """
    try:
        from src.config.accessor import get_env_config

        swarm = get_env_config().swarm
        worker_model = (swarm.swarm_worker_model or "").strip()
        lead_model = (swarm.swarm_lead_model or "").strip()
    except Exception:
        worker_model = (os.environ.get("SWARM_WORKER_MODEL") or "").strip()
        lead_model = (os.environ.get("SWARM_LEAD_MODEL") or "").strip()

    if worker_model or lead_model:
        leads = _lead_agent_ids(run)
        for agent in run.agents:
            if agent.model_name:
                continue
            if agent.id in leads:
                if lead_model:
                    agent.model_name = lead_model
            elif worker_model:
                agent.model_name = worker_model

    apply_free_tier_agent_limits(run)


def estimate_speedup_factors(
    *,
    layer_widths: list[int] | None = None,
    llm_calls_per_worker: int = 8,
) -> dict[str, float | str | int | bool]:
    """Rough analytical estimate of wall-clock benefit vs the prior hard-sequential unload-every-call design.

    Assumptions (documented for operators, not a benchmark):
      * Prior: max_workers=1 + Ollama unload after every LLM call (~3–8s reload)
      * New balanced/local: still 1 worker, but keep model resident across calls
      * New balanced/remote: parallel up to SWARM_MAX_WORKERS on independent DAG layer
    """
    mode = get_vram_mode()
    local = is_local_llm_provider()
    workers = resolve_max_workers()
    widths = layer_widths or [2, 1, 1]
    max_width = max(widths) if widths else 1
    reload_s = 5.0
    call_s = 2.0

    prior_reload_tax = llm_calls_per_worker * reload_s * sum(widths)
    prior_compute = llm_calls_per_worker * call_s * sum(widths)
    prior_total = prior_reload_tax + prior_compute

    # New: at most one hard unload per worker (model switch / run end), not per call.
    new_reload_tax = (1.0 if local else 0.0) * reload_s * sum(widths)
    # Parallelism only helps the widest independent layer(s).
    parallel_factor = float(max_width) / float(workers) if workers else float(max_width)
    # Wall time for compute ≈ sum over layers of (width/workers)*per_worker_time
    new_compute = 0.0
    for w in widths:
        batches = (w + workers - 1) // workers
        new_compute += batches * llm_calls_per_worker * call_s
    new_total = new_reload_tax + new_compute
    speedup = prior_total / new_total if new_total > 0 else 1.0

    return {
        "mode": mode,
        "local_provider": local,
        "effective_max_workers": workers,
        "assumed_layer_widths": widths,
        "assumed_llm_calls_per_worker": llm_calls_per_worker,
        "estimated_prior_seconds": round(prior_total, 1),
        "estimated_new_seconds": round(new_total, 1),
        "estimated_speedup_x": round(speedup, 2),
        "notes": (
            "Analytical estimate vs unload-every-call + forced max_workers=1; "
            "real gains depend on provider latency and DAG shape."
        ),
    }


def cacheable_tool_names() -> frozenset[str]:
    """Tools safe to share across swarm tasks within a single run."""
    return frozenset(
        {
            "get_market_data",
            "factor_analysis",
            "web_search",
            "fred_macro",
            "load_skill",
        }
    )
