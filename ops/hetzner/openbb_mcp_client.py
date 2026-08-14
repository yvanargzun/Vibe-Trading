#!/usr/bin/env python3
"""OpenBB MCP client for Vibe autotrade market intel (direct, no Hermes)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HOME = Path("/root/.vibe-trading")
CACHE_PATH = HOME / "openbb_market_intel.json"
MCP_URL = "http://127.0.0.1:8100/mcp"
CACHE_TTL_SEC = 15 * 60  # refresh at most every 15m inside a tick loop


class OpenBBMcpError(RuntimeError):
    pass


def _parse_body(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        return {"raw": text[:800]}
    return json.loads(data_lines[-1])


class OpenBBMcpClient:
    def __init__(self, url: str = MCP_URL, timeout: int = 45) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            raise OpenBBMcpError(f"HTTP {exc.code}: {detail or exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise OpenBBMcpError(str(exc)) from exc
        if payload.get("method", "").startswith("notifications/"):
            return {}
        doc = _parse_body(raw)
        if "error" in doc:
            raise OpenBBMcpError(str(doc["error"]))
        return doc

    def initialize(self) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "vibe-autotrade", "version": "1"},
                },
            }
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if not self.session_id:
            self.initialize()
        doc = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        result = doc.get("result") or {}
        # Prefer structured content text JSON
        content = result.get("content") or []
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
        joined = "\n".join(texts).strip()
        if not joined:
            return result
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return {"text": joined}


def _closes_from_crypto(payload: Any) -> list[float]:
    rows = []
    if isinstance(payload, dict):
        rows = payload.get("results") or payload.get("data") or []
    elif isinstance(payload, list):
        rows = payload
    closes: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        c = row.get("close") or row.get("Close") or row.get("adj_close")
        if c is None:
            continue
        try:
            closes.append(float(c))
        except (TypeError, ValueError):
            continue
    return closes


def _news_headlines(payload: Any, limit: int = 6) -> list[str]:
    rows = []
    if isinstance(payload, dict):
        rows = payload.get("results") or payload.get("data") or []
    elif isinstance(payload, list):
        rows = payload
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("headline") or "").strip()
        if title:
            out.append(title[:160])
        if len(out) >= limit:
            break
    return out


def _bias_from_closes(closes: list[float]) -> tuple[str, float]:
    if len(closes) < 3:
        return "neutral", 0.0
    first = closes[0]
    last = closes[-1]
    if first <= 0:
        return "neutral", 0.0
    chg = (last - first) / first * 100.0
    # short window momentum
    if chg >= 2.0:
        return "risk_on", chg
    if chg <= -2.0:
        return "risk_off", chg
    if chg >= 0.6:
        return "mild_on", chg
    if chg <= -0.6:
        return "mild_off", chg
    return "neutral", chg


def _score_delta(bias: str, headlines: list[str]) -> float:
    delta = {
        "risk_on": 0.55,
        "mild_on": 0.25,
        "neutral": 0.0,
        "mild_off": -0.25,
        "risk_off": -0.55,
    }.get(bias, 0.0)
    blob = " ".join(headlines).lower()
    bullish = ("etf", "approval", "surge", "rally", "record", "inflow", "bull")
    bearish = ("hack", "ban", "sec charge", "lawsuit", "crash", "outflow", "bear", "liquidation")
    hits_b = sum(1 for w in bullish if w in blob)
    hits_r = sum(1 for w in bearish if w in blob)
    delta += min(hits_b, 2) * 0.12
    delta -= min(hits_r, 2) * 0.15
    return round(delta, 3)


def build_market_intel(*, force: bool = False) -> dict[str, Any]:
    """Fetch OpenBB MCP intel for Vibe decisions. Cached on disk."""
    now = time.time()
    if CACHE_PATH.exists() and not force:
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if now - float(cached.get("ts") or 0) < CACHE_TTL_SEC and cached.get("ok"):
                return cached
        except Exception:
            pass

    client = OpenBBMcpClient()
    errors: list[str] = []
    btc_closes: list[float] = []
    eth_closes: list[float] = []
    headlines: list[str] = []

    try:
        client.initialize()
    except Exception as exc:  # noqa: BLE001
        doc = {
            "ok": False,
            "ts": now,
            "error": f"init:{exc}",
            "bias": "neutral",
            "btc_chg_pct": 0.0,
            "score_delta": 0.0,
            "headlines": [],
            "source": "openbb-mcp",
        }
        try:
            CACHE_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
        return doc

    try:
        btc = client.call_tool(
            "crypto_price_historical",
            {
                "symbol": "BTCUSD",
                "provider": "yfinance",
                "interval": "1d",
                "start_date": time.strftime("%Y-%m-%d", time.gmtime(now - 12 * 86400)),
            },
        )
        btc_closes = _closes_from_crypto(btc)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"btc:{exc}")

    try:
        eth = client.call_tool(
            "crypto_price_historical",
            {
                "symbol": "ETHUSD",
                "provider": "yfinance",
                "interval": "1d",
                "start_date": time.strftime("%Y-%m-%d", time.gmtime(now - 12 * 86400)),
            },
        )
        eth_closes = _closes_from_crypto(eth)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eth:{exc}")

    for tool, args in (
        ("news_world", {"limit": 8}),
        ("news_company", {"symbol": "BTC", "limit": 5}),
    ):
        try:
            news = client.call_tool(tool, args)
            headlines.extend(_news_headlines(news, limit=6))
            if headlines:
                break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{tool}:{exc}")

    bias, btc_chg = _bias_from_closes(btc_closes)
    # blend mild ETH confirmation
    eth_bias, eth_chg = _bias_from_closes(eth_closes)
    if bias == "neutral" and eth_bias != "neutral":
        bias = eth_bias
    score_delta = _score_delta(bias, headlines)

    # Prefer BTC window; keep ETH chg for context
    doc = {
        "ok": True,
        "ts": now,
        "bias": bias,
        "btc_chg_pct": round(btc_chg, 3),
        "eth_chg_pct": round(eth_chg, 3),
        "score_delta": score_delta,
        "headlines": headlines[:6],
        "btc_last": btc_closes[-1] if btc_closes else None,
        "eth_last": eth_closes[-1] if eth_closes else None,
        "errors": errors,
        "source": "openbb-mcp",
    }
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    return doc


def load_cached_intel() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    print(json.dumps(build_market_intel(force=True), indent=2)[:2000])
