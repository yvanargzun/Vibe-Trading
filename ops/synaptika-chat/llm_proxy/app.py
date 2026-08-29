#!/usr/bin/env python3
"""OpenAI-compatible LLM failover for Synaptika Chat.

Chain (skip missing keys): OpenRouter free → Gemini free → Groq free.
Supports SSE streaming so Open WebUI can show typing / thinking.
"""

from __future__ import annotations

import json
import os
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, BinaryIO

HOST = os.environ.get("LLM_PROXY_HOST", "0.0.0.0")
PORT = int(os.environ.get("LLM_PROXY_PORT", "4000"))
PROXY_MODEL = os.environ.get("LLM_PROXY_MODEL", "synaptika-chat-auto")
TIMEOUT = float(os.environ.get("LLM_PROXY_TIMEOUT", "60"))
CONNECT_TIMEOUT = float(os.environ.get("LLM_PROXY_CONNECT_TIMEOUT", "12"))

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

GEMINI_KEY = (
    os.environ.get("GEMINI_API_KEY", "").strip()
    or os.environ.get("GOOGLE_API_KEY", "").strip()
)
GEMINI_BASE = os.environ.get(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai",
).rstrip("/")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# Keep auto-chain short: one OpenRouter attempt, then Gemini/Groq (faster failover).
_DEFAULT_FREE_OPENROUTER = "openrouter/free"


def _parse_openrouter_models() -> list[str]:
    raw = os.environ.get("OPENROUTER_FAILOVER_MODELS", _DEFAULT_FREE_OPENROUTER)
    models = [m.strip() for m in raw.split(",") if m.strip()]
    allow_paid = os.environ.get("ALLOW_PAID_OPENROUTER", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if allow_paid:
        return models
    free_only = [
        m
        for m in models
        if m.endswith(":free") or m in {"openrouter/free", "openrouter/auto"}
    ]
    return free_only or ["openrouter/free"]


OPENROUTER_MODELS = _parse_openrouter_models()

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
    "free-models-per-day",
    "provider returned error",
    "no healthy upstream",
    "model not found",
    "unauthorized",
    "forbidden",
    "invalid api key",
)


def log(msg: str) -> None:
    print(msg, flush=True)


def providers() -> list[dict[str, Any]]:
    """Priority: OpenRouter free → Gemini → Groq."""
    out: list[dict[str, Any]] = []
    if OPENROUTER_KEY:
        out.append(
            {
                "name": "openrouter",
                "base": OPENROUTER_BASE,
                "key": OPENROUTER_KEY,
                "models": OPENROUTER_MODELS,
            }
        )
    if GEMINI_KEY:
        out.append(
            {
                "name": "gemini",
                "base": GEMINI_BASE,
                "key": GEMINI_KEY,
                "models": [GEMINI_MODEL],
            }
        )
    if GROQ_KEY:
        out.append(
            {
                "name": "groq",
                "base": GROQ_BASE,
                "key": GROQ_KEY,
                "models": [GROQ_MODEL],
            }
        )
    return out


def is_failover_status(code: int) -> bool:
    return code in (400, 401, 402, 403, 404, 408, 429, 500, 502, 503, 504) or code >= 520


def body_needs_failover(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in FAIL_MARKERS)


def _headers(key: str, stream: bool) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Accept": "text/event-stream" if stream else "application/json",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://synaptika-chat.duckdns.org",
        "X-Title": "Synaptika Chat",
    }


class _TimeoutHTTPErrorProcessor(urllib.request.HTTPErrorProcessor):
    """Keep HTTPError behavior for non-2xx."""


def upstream_request(
    *,
    base: str,
    key: str,
    path: str,
    method: str = "GET",
    body: dict | None = None,
) -> tuple[int, bytes, str]:
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers=_headers(key, stream=False), method=method
    )
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


def open_upstream_stream(
    *, base: str, key: str, body: dict[str, Any]
) -> tuple[int, Any, str]:
    """Open streaming response; caller must close resp on success."""
    url = f"{base.rstrip('/')}/chat/completions"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers=_headers(key, stream=True), method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        ctype = resp.headers.get("Content-Type") or "text/event-stream"
        return int(resp.status), resp, ctype
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        return int(exc.code), raw, "application/json"
    except Exception as exc:  # noqa: BLE001
        return 599, json.dumps({"error": str(exc)}).encode("utf-8"), "application/json"


def inject_system_prompt(body: dict[str, Any]) -> dict[str, Any]:
    """Ensure a system prompt that prefers web-search for current events."""
    prompt = os.environ.get("CHAT_SYSTEM_PROMPT", "").strip()
    if not prompt:
        prompt = (
            "Eres Synaptika Chat. Responde en español. "
            "Para noticias o datos actuales USA búsqueda web / search_web; "
            "no digas que no tienes internet si puedes buscar. "
            "No inventes titulares."
        )
    out = dict(body)
    msgs = list(out.get("messages") or [])
    if not msgs:
        out["messages"] = [{"role": "system", "content": prompt}]
        return out
    if msgs[0].get("role") == "system":
        # Prepend our rules if missing
        content = str(msgs[0].get("content") or "")
        if "búsqueda web" not in content.lower() and "search_web" not in content.lower():
            msgs[0] = {
                **msgs[0],
                "content": prompt + "\n\n" + content,
            }
    else:
        msgs.insert(0, {"role": "system", "content": prompt})
    out["messages"] = msgs
    return out


def rewrite_model(body: dict[str, Any], model: str) -> dict[str, Any]:
    out = inject_system_prompt(body)
    out["model"] = model
    return out


def try_chat(body: dict[str, Any]) -> tuple[int, bytes, str, str]:
    req_body = dict(body)
    req_body["stream"] = False
    chain = providers()
    if not chain:
        err = {
            "error": {
                "message": "No LLM keys configured (OPENROUTER/GEMINI/GROQ)",
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
            )
            label = f"{prov['name']}:{model}"
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else ""
            if code == 200 and raw:
                try:
                    doc = json.loads(text)
                    choices = doc.get("choices") or []
                    if choices:
                        doc.setdefault("synaptika_proxy", {})["via"] = label
                        log(f"OK {label}")
                        return 200, json.dumps(doc).encode("utf-8"), "application/json", label
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
            "message": "All LLM upstreams failed (openrouter/gemini/groq)",
            "type": "proxy_exhausted",
            "detail": errors[-8:],
        }
    }
    return 503, json.dumps(err).encode("utf-8"), "application/json", "exhausted"


def try_chat_stream(handler: BaseHTTPRequestHandler, body: dict[str, Any]) -> str:
    """Stream first healthy upstream as SSE. Returns via-label or 'exhausted'."""
    req_body = dict(body)
    req_body["stream"] = True
    # Nudge providers to start tokens sooner
    req_body.setdefault("temperature", 0.5)
    chain = providers()
    if not chain:
        err = json.dumps(
            {
                "error": {
                    "message": "No LLM keys configured (OPENROUTER/GEMINI/GROQ)",
                    "type": "proxy_config",
                }
            }
        ).encode("utf-8")
        handler.send_response(503)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(err)))
        handler.end_headers()
        handler.wfile.write(err)
        return "none"

    errors: list[str] = []
    for prov in chain:
        for model in prov["models"]:
            payload = rewrite_model(req_body, model)
            label = f"{prov['name']}:{model}"
            code, resp_or_raw, ctype = open_upstream_stream(
                base=prov["base"], key=prov["key"], body=payload
            )
            if code != 200:
                text = (
                    resp_or_raw.decode("utf-8", errors="replace")
                    if isinstance(resp_or_raw, bytes)
                    else str(resp_or_raw)[:200]
                )
                errors.append(f"{label} http={code} {text[:160]}")
                log(f"FAIL stream {label} http={code}")
                continue

            # Success path: pipe SSE to client
            assert not isinstance(resp_or_raw, bytes)
            log(f"STREAM {label}")
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "keep-alive")
            handler.send_header("X-Synaptika-Chat-Proxy", "1")
            handler.send_header("X-Synaptika-Via", label)
            handler.end_headers()
            try:
                while True:
                    chunk = resp_or_raw.read(1024)
                    if not chunk:
                        break
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
            finally:
                try:
                    resp_or_raw.close()
                except Exception:  # noqa: BLE001
                    pass
            return label

    err = json.dumps(
        {
            "error": {
                "message": "All LLM upstreams failed (openrouter/gemini/groq)",
                "type": "proxy_exhausted",
                "detail": errors[-8:],
            }
        }
    ).encode("utf-8")
    handler.send_response(503)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(err)))
    handler.end_headers()
    handler.wfile.write(err)
    return "exhausted"


def models_payload() -> dict[str, Any]:
    data = [
        {
            "id": PROXY_MODEL,
            "object": "model",
            "owned_by": "synaptika",
            "description": "Auto failover (stream): OpenRouter free → Gemini → Groq",
        }
    ]
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
        self.send_header("X-Synaptika-Chat-Proxy", "1")
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
                "gemini": bool(GEMINI_KEY),
                "groq": bool(GROQ_KEY),
                "stream": True,
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
            mid = str(body.get("model") or PROXY_MODEL)
            if mid in (PROXY_MODEL, "synaptika-chat") or mid.startswith("synaptika"):
                body["model"] = PROXY_MODEL
            # Default to streaming for Open WebUI typing/thinking UX
            if "stream" not in body:
                body["stream"] = True
            if body.get("stream"):
                via = try_chat_stream(self, body)
                log(f"stream done via={via}")
                return
            code, raw, ctype, via = try_chat(body)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Synaptika-Chat-Proxy", "1")
            self.send_header("X-Synaptika-Via", via)
            self.end_headers()
            self.wfile.write(raw)
        except Exception:
            tb = traceback.format_exc()
            log(tb)
            err = json.dumps(
                {"error": {"message": "proxy_internal", "detail": tb[-500:]}}
            )
            self._send(500, err.encode("utf-8"), "application/json")


def main() -> None:
    chain = [p["name"] for p in providers()]
    log(f"LLM_PROXY listen={HOST}:{PORT} model={PROXY_MODEL} chain={chain} stream=on")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
