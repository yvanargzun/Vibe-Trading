"""Inference memory helpers: GPU cleanup + KV-cache offload for local LLMs.

Designed for low-VRAM hosts running sequential swarm workers. Remote API
providers get a no-op beyond ``gc.collect``; local Ollama/torch stacks unload
resident weights so the next agent does not share VRAM with idle peers.
"""

from __future__ import annotations

import gc
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def _ollama_base_url() -> str:
    raw = (
        os.environ.get("OLLAMA_HOST", "").strip()
        or os.environ.get("OLLAMA_BASE_URL", "").strip()
        or "http://127.0.0.1:11434"
    )
    return raw.rstrip("/")


def offload_kv_cache_to_disk(tag: str = "idle") -> None:
    """Mark a disk slot for KV/offload state under the portable workspace.

    Local OpenAI-compatible stacks do not expose raw KV tensors to the client;
    this writes a tiny marker so operators can confirm offload cycles and keeps
    the ``kv_cache`` directory ready for native backends that dump tensors.
    """
    try:
        from src.config.paths import get_kv_cache_dir

        slot = get_kv_cache_dir() / f"{tag}.offload.json"
        payload = {"tag": tag, "status": "released"}
        # Late import avoids cycles; atomic write keeps sync tools safe.
        from src.config.paths import atomic_write_text

        atomic_write_text(slot, json.dumps(payload, indent=2) + "\n")
    except Exception:
        logger.debug("KV offload marker skipped", exc_info=True)


def unload_ollama_model(model: str | None = None) -> bool:
    """Ask Ollama to drop a model from VRAM (``keep_alive=0``).

    Returns:
        True if the unload request was accepted, False otherwise.
    """
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


def release_gpu_memory(*, model_name: str | None = None, tag: str = "post_generate") -> None:
    """Aggressively free client-side + local-server inference memory.

    Steps:
      1. Python GC
      2. ``torch.cuda.empty_cache`` / IPC collect when torch+CUDA present
      3. Ollama ``keep_alive=0`` unload when a local host is reachable
      4. Disk KV offload marker under ``vibe_workspace/kv_cache``
    """
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

    unload_ollama_model(model_name)
    offload_kv_cache_to_disk(tag=tag)
