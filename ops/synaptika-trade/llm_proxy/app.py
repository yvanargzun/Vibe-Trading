#!/usr/bin/env python3
"""OpenAI-compatible LLM failover proxy for Synaptika Open WebUI.

Chain (skip missing keys): Gemini → Ollama Cloud → OpenRouter (free then paid).
Never returns empty if any upstream still has quota.
"""

from __future__ import annotations

import json
import os
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HOST = os.environ.get("LLM_PROXY_HOST", "0.0.0.0")
PORT = int(os.environ.get("LLM_PROXY_PORT", "4000"))
PROXY_MODEL = os.environ.get("LLM_PROXY_MODEL", "synaptika-auto")
TIMEOUT = float(os.environ.get("LLM_PROXY_TIMEOUT", "90"))

GEMINI_KEY = (
    os.environ.get("GEMINI_API_KEY", "").strip()
    or os.environ.get("GOOGLE_API_KEY", "").strip()
)
GEMINI_BASE = os.environ.get(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai",
).rstrip("/")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

OLLAMA_KEY = (
    os.environ.get("OLLAMA_API_KEY", "").strip()
    or os.environ.get("OLLAMA_CLOUD_API_KEY", "").strip()
)
OLLAMA_BASE = os.environ.get(
    "OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1"
).rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_DEFAULT_MODEL", "deepseek-v4-flash")

OPENROUTER_KEY = (
    os.environ.get("OPENROUTER_API_KEY", "").strip()
    or os.environ.get("OPENAI_API_KEY", "").strip()
)
OPENROUTER_BASE = os.environ.get(
    "OPENROUTER_BASE_URL",
    os.environ.get("OPENAI_API_BASE_URL", "https://openrouter.ai/api/v1"),
).rstrip("/")
if "api.openai.com" in OPENROUTER_BASE:
    OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Free first, then cheap paid — always last net for "never without model"
OPENROUTER_MODELS = [
    m.strip()
    for m in os.environ.get(
        "OPENROUTER_FAILOVER_MODELS",
        "inclusionai/ling-3.0-flash:free,"
        "google/gemma-3-27b-it:free,"
        "meta-llama/llama-3.3-70b-instruct:free,"
        "qwen/qwen-2.5-72b-instruct:free,"
        "openai/gpt-4o-mini,"
        "google/gemini-2.0-flash-001,"
        "anthropic/claude-3.5-haiku",
    ).split(",")
    if m.strip()
]

FAIL_MARKERS = (
    "rate limit",
    "rate_limit",
    "quota",
    "insufficient",
    "credit",
    "billing",
    "payment",
    "balance",
    "exceeded",
    "capacity",
    "overloaded",
    "temporarily unavailable",
    "tokens",
    "token",
    "// free-models-per-day",
    "provider returned error",
    "no healthy upstream",
    "model not found",
    "not found",
    "unauthorized",
    "forbidden",
    "invalid api key",
)


def log(msg: str) -> None:
    print(msg, flush=True)


def providers() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if GEMINI_KEY:
        out.append(
            {
                "name": "gemini",
                "base": GEMINI_BASE,
                "key": GEMINI_KEY,
                "models": [GEMINI_MODEL],
            }
        )
    if OLLAMA_KEY:
        out.append(
            {
                "name": "ollama",
                "base": OLLAMA_BASE,
                "key": OLLAMA_KEY,
                "models": [OLLAMA_MODEL],
            }
        )
    if OPENROUTER_KEY:
        out.append(
            {
                "name": "openrouter",
                "base": OPENROUTER_BASE,
                "key": OPENROUTER_KEY,
                "models": OPENROUTER_MODELS,
            }
        )
    return out


def is_failover_status(code: int) -> bool:
    return code in (401, 402, 403, 408, 429, 500, 502, 503, 504) or code >= 520


def body_needs_failover(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in FAIL_MARKERS)


def upstream_request(
    *,
    base: str,
    key: str,
    path: str,
    method: str = "GET",
    body: dict | None = None,
    stream: bool = False,
) -> tuple[int, bytes, str]:
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "text/event-stream" if stream else "application/json",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://synaptika-trade.duckdns.org",
        "X-Title": "Synaptika Trade Copiloto",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type") or "application/json"
            return int(resp.status), raw, ctype
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        return int(exc.code), raw, "application/json"
    except Exception as exc:  # noqa: BLE001
        return 599, json.dumps({"error": str(exc)}).encode("utf-8"), "application/json"


def rewrite_model(body: dict[str, Any], model: str) -> dict[str, Any]:
    out = dict(body)
    out["model"] = model
    return out


def try_chat(body: dict[str, Any]) -> tuple[int, bytes, str, str]:
    """Return status, raw, content_type, provider_label.

    Always uses non-stream upstream calls so failover is reliable;
    Open WebUI still accepts a full JSON completion.
    """
    req_body = dict(body)
    req_body["stream"] = False
    chain = providers()
    if not chain:
        err = {
            "error": {
                "message": "No LLM keys configured (GEMINI/OLLAMA/OPENROUTER)",
                "type": "proxy_config",
            }
        }
        return 503, json.dumps(err).encode("utf-8"), "application/json", "none"

    errors: list[str] = []
    for prov in chain:
        for model in prov["models"]:
            payload = rewrite_model(req_body, model)
            code, raw, ctype = upstream_request(
                base=prov["base"],
                key=prov["key"],
                path="chat/completions",
                method="POST",
                body=payload,
                stream=False,
            )
            label = f"{prov['name']}:{model}"
            text = raw.decode("utf-8", errors="replace")
            if code == 200 and raw:
                try:
                    doc = json.loads(text)
                    choices = doc.get("choices") or []
                    if choices:
                        doc.setdefault("synaptika_proxy", {})["via"] = label
                        raw_out = json.dumps(doc).encode("utf-8")
                        log(f"OK {label}")
                        return 200, raw_out, "application/json", label
                except json.JSONDecodeError:
                    pass
                errors.append(f"{label} empty_response")
                log(f"FAIL {label} empty")
                continue
            if is_failover_status(code) or body_needs_failover(text):
                errors.append(f"{label} http={code} {text[:160]}")
                log(f"FAIL {label} http={code}")
                continue
            errors.append(f"{label} http={code} {text[:160]}")
            log(f"FAIL {label} http={code} (continue)")

    err = {
        "error": {
            "message": "All LLM upstreams failed (gemini/ollama/openrouter)",
            "type": "proxy_exhausted",
            "detail": errors[-8:],
        }
    }
    return 503, json.dumps(err).encode("utf-8"), "application/json", "exhausted"


def models_payload() -> dict[str, Any]:
    data = [
        {
            "id": PROXY_MODEL,
            "object": "model",
            "owned_by": "synaptika",
            "description": "Auto failover: Gemini → Ollama → OpenRouter",
        }
    ]
    # Also expose upstreams for debugging / manual picks via proxy
    for prov in providers():
        for mid in prov["models"][:3]:
            data.append(
                {
                    "id": f"{prov['name']}/{mid}",
                    "object": "model",
                    "owned_by": prov["name"],
                }
            )
    return {"object": "list", "data": data}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        log("%s - %s" % (self.address_string(), fmt % args))

    def _send(self, code: int, raw: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Synaptika-Proxy", "1")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            doc = json.loads(raw.decode("utf-8"))
            return doc if isinstance(doc, dict) else {}
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/healthz", "/health", "/"):
            chain = [p["name"] for p in providers()]
            body = {
                "ok": bool(chain),
                "model": PROXY_MODEL,
                "chain": chain,
                "openrouter": bool(OPENROUTER_KEY),
                "ollama": bool(OLLAMA_KEY),
                "gemini": bool(GEMINI_KEY),
            }
            self._send(200, json.dumps(body).encode("utf-8"), "application/json")
            return
        if path in ("/v1/models", "/models"):
            self._send(
                200,
                json.dumps(models_payload()).encode("utf-8"),
                "application/json",
            )
            return
        self._send(
            404,
            json.dumps({"error": "not_found"}).encode("utf-8"),
            "application/json",
        )

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._send(
                404,
                json.dumps({"error": "not_found"}).encode("utf-8"),
                "application/json",
            )
            return
        try:
            body = self._read_json()
            # Force auto routing when copiloto / unknown ids hit the proxy
            mid = str(body.get("model") or PROXY_MODEL)
            if mid in (PROXY_MODEL, "synaptika-copiloto") or mid.startswith("synaptika"):
                body["model"] = PROXY_MODEL
            # Prefer non-stream for cleaner failover (OWUI still works)
            # Keep stream if client insists — try_chat handles both.
            code, raw, ctype, via = try_chat(body)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Synaptika-Proxy", "1")
            self.send_header("X-Synaptika-Via", via)
            self.end_headers()
            self.wfile.write(raw)
        except Exception:
            tb = traceback.format_exc()
            log(tb)
            err = json.dumps({"error": {"message": "proxy_internal", "detail": tb[-500:]}})
            self._send(500, err.encode("utf-8"), "application/json")


def main() -> None:
    chain = [p["name"] for p in providers()]
    log(f"LLM_PROXY listen={HOST}:{PORT} model={PROXY_MODEL} chain={chain}")
    if "openrouter" not in chain:
        log("WARN: OpenRouter key missing — last-resort failover unavailable")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
