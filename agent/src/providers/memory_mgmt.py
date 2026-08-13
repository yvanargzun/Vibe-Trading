"""Inference memory helpers: policy-aware GPU cleanup for local LLMs.

Release levels:
  * soft — GC + CUDA empty_cache; keep Ollama weights resident
  * hard — also ``keep_alive=0`` unload + KV marker

``SWARM_VRAM_MODE`` drives defaults:
  * safe     — hard unload between workers
  * balanced — soft between same-model turns; hard on model switch / run end
  * fast     — soft during run; hard only at run end (and on model switch)
"""

from __future__ import annotations

import gc
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Literal

logger = logging.getLogger(__name__)

ReleaseLevel = Literal["soft", "hard", "none"]

_lock = threading.Lock()
_resident_model: str | None = None
_policy: str = "balanced"


def set_vram_policy(mode: str | None) -> None:
    """Install active VRAM policy from ``SWARM_VRAM_MODE`` (or override)."""
    global _policy
    raw = (mode or "balanced").strip().lower()
    if raw not in {"safe", "balanced", "fast"}:
        raw = "balanced"
    with _lock:
        _policy = raw


def get_vram_policy() -> str:
    with _lock:
        return _policy


def note_model_in_use(model_name: str | None) -> str | None:
    """Record the model currently loaded; return previous resident model if any."""
    global _resident_model
    name = (model_name or "").strip() or None
    with _lock:
        prev = _resident_model
        _resident_model = name
        return prev


def _ollama_base_url() -> str:
    raw = (
        os.environ.get("OLLAMA_HOST", "").strip()
        or os.environ.get("OLLAMA_BASE_URL", "").strip()
        or "http://127.0.0.1:11434"
    )
    return raw.rstrip("/")


def offload_kv_cache_to_disk(tag: str = "idle") -> None:
    """Mark a disk slot for KV/offload state under the portable workspace."""
    try:
        from src.config.paths import atomic_write_text, get_kv_cache_dir

        slot = get_kv_cache_dir() / f"{tag}.offload.json"
        payload = {"tag": tag, "status": "released"}
        atomic_write_text(slot, json.dumps(payload, indent=2) + "\n")
    except Exception:
        logger.debug("KV offload marker skipped", exc_info=True)


def unload_ollama_model(model: str | None = None) -> bool:
    """Ask Ollama to drop a model from VRAM (``keep_alive=0``)."""
    base = _ollama_base_url()
    body: dict[str, Any] = {"keep_alive": 0}
    if model:
        body["model"] = model
        body["prompt"] = ""
    try:
        req = urllib.request.Request(
            f"{base}/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Ollama unload skipped: %s", exc)
        return False


def touch_ollama_keep_alive(model: str | None = None, seconds: int = 60) -> bool:
    """Extend Ollama residency without generating tokens."""
    if seconds <= 0:
        return False
    base = _ollama_base_url()
    body: dict[str, Any] = {"keep_alive": seconds}
    if model:
        body["model"] = model
        body["prompt"] = ""
    try:
        req = urllib.request.Request(
            f"{base}/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        return True
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Ollama keep_alive touch skipped: %s", exc)
        return False


def _cuda_empty() -> None:
    gc.collect()
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
            gc.collect()
            torch.cuda.empty_cache()
    except Exception:
        logger.debug("torch CUDA cleanup skipped", exc_info=True)


def _infer_level(tag: str, *, force: bool) -> ReleaseLevel:
    if force:
        return "hard"
    policy = get_vram_policy()
    # Mid-ReAct / factory: never hard-unload — that reloads every LLM call.
    if tag.startswith("post_chat") or tag.startswith("post_stream") or tag.startswith("post_build"):
        return "soft" if policy != "fast" else "none"
    if tag.startswith("run_end"):
        return "hard"
    if tag.startswith("model_switch"):
        return "hard"
    # post_worker / worker_done
    if policy == "safe":
        return "hard"
    if policy == "fast":
        return "soft"
    # balanced
    return "soft"


def release_gpu_memory(
    *,
    model_name: str | None = None,
    tag: str = "post_generate",
    level: ReleaseLevel | None = None,
    force: bool = False,
) -> None:
    """Free client-side (+ optional Ollama) inference memory per policy.

    Args:
        model_name: Model id for Ollama unload / keep_alive.
        tag: Call-site label for KV markers and policy inference.
        level: Explicit ``soft`` / ``hard`` / ``none``; overrides inference.
        force: Force hard unload (run end / explicit operator).
    """
    resolved = level if level is not None else _infer_level(tag, force=force)
    if resolved == "none":
        return

    _cuda_empty()
    if resolved == "soft":
        # Keep weights warm; briefly extend keep_alive on local Ollama.
        if get_vram_policy() in {"balanced", "fast"}:
            try:
                from src.config.accessor import get_env_config

                secs = int(get_env_config().swarm.swarm_ollama_keep_alive_s)
            except Exception:
                secs = int(os.environ.get("SWARM_OLLAMA_KEEP_ALIVE_S") or "60")
            touch_ollama_keep_alive(model_name, seconds=max(0, secs))
        return

    unload_ollama_model(model_name)
    offload_kv_cache_to_disk(tag=tag)
    global _resident_model
    with _lock:
        if model_name is None or _resident_model == model_name:
            _resident_model = None


def release_on_model_switch(new_model: str | None) -> None:
    """Hard-unload previous resident model when the next agent uses a different one."""
    prev = note_model_in_use(new_model)
    new_name = (new_model or "").strip() or None
    if prev and new_name and prev != new_name:
        release_gpu_memory(model_name=prev, tag="model_switch", level="hard")


def release_at_run_end(*, model_name: str | None = None) -> None:
    """Hard release after a swarm run finishes."""
    global _resident_model
    release_gpu_memory(model_name=model_name, tag="run_end", force=True)
    with _lock:
        _resident_model = None
