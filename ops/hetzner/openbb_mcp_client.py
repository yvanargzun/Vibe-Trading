#!/usr/bin/env python3
"""OpenBB MCP client for Vibe autotrade — direct market intel (no Hermes).

Uses free providers (yfinance) for crypto + equity discovery. News providers
need API keys; if present in env they are tried automatically.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HOME = Path(os.environ.get("VIBE_TRADING_HOME", "/root/.vibe-trading"))
CACHE_PATH = HOME / "openbb_market_intel.json"
HISTORY_PATH = HOME / "openbb_intel_history.jsonl"
MCP_URL = os.environ.get("OPENBB_MCP_URL", "http://127.0.0.1:8100/mcp")
CACHE_TTL_SEC = int(os.environ.get("OPENBB_CACHE_TTL_SEC", str(10 * 60)))
HISTORY_KEEP = 200


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


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("results", "data", "result"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
            if isinstance(val, dict) and isinstance(val.get("results"), list):
                return [r for r in val["results"] if isinstance(r, dict)]
    return []


class OpenBBMcpClient:
    def __init__(self, url: str = MCP_URL, timeout: int = 45) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None
        self._rid = 0

    def _next_id(self) -> int:
        self._rid += 1
        return self._rid

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
        if str(payload.get("method") or "").startswith("notifications/"):
            return {}
        doc = _parse_body(raw)
        if "error" in doc:
            raise OpenBBMcpError(str(doc["error"]))
        return doc

    def initialize(self) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "vibe-autotrade", "version": "2"},
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
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        result = doc.get("result") or {}
        content = result.get("content") or []
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
        joined = "\n".join(texts).strip()
        if result.get("isError"):
            raise OpenBBMcpError(joined or f"tool {name} isError")
        if not joined:
            return result
        try:
            parsed = json.loads(joined)
        except json.JSONDecodeError:
            return {"text": joined}
        # OpenBB sometimes wraps tool failures as HTTP-looking text without isError
        if isinstance(parsed, dict) and parsed.get("detail") and not parsed.get("results"):
            raise OpenBBMcpError(str(parsed.get("detail"))[:300])
        return parsed


def _closes(payload: Any) -> list[float]:
    closes: list[float] = []
    for row in _rows(payload):
        c = row.get("close") or row.get("Close") or row.get("adj_close")
        if c is None:
            continue
        try:
            closes.append(float(c))
        except (TypeError, ValueError):
            continue
    return closes


def _pct_chg(closes: list[float], lookback: int | None = None) -> float | None:
    if len(closes) < 2:
        return None
    series = closes[-(lookback + 1) :] if lookback else closes
    if len(series) < 2 or series[0] <= 0:
        return None
    return (series[-1] - series[0]) / series[0] * 100.0


def _news_headlines(payload: Any, limit: int = 8) -> list[str]:
    out: list[str] = []
    for row in _rows(payload):
        title = str(row.get("title") or row.get("headline") or "").strip()
        if title:
            out.append(title[:180])
        if len(out) >= limit:
            break
    return out


def _discovery_summary(payload: Any, limit: int = 5) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _rows(payload)[:limit]:
        try:
            pct = row.get("percent_change")
            if pct is None:
                pct = row.get("change_percent")
            pct_f = float(pct) if pct is not None else 0.0
            # yfinance discovery sometimes returns fraction (0.13 = 13%)
            if abs(pct_f) <= 1.5 and pct_f != 0:
                pct_f *= 100.0
            out.append(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "name": str(row.get("name") or "")[:40],
                    "pct": round(pct_f, 3),
                }
            )
        except Exception:
            continue
    return out


def _avg_pct(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    return sum(float(i.get("pct") or 0) for i in items) / len(items)


def _news_providers() -> list[str]:
    """Paid/world news providers only if credentials look configured."""
    env = {k.upper(): v for k, v in os.environ.items()}
    mapping = [
        ("benzinga", ("BENZINGA_API_KEY", "BENZINGA_TOKEN")),
        ("fmp", ("FMP_API_KEY", "FMP_KEY")),
        ("tiingo", ("TIINGO_TOKEN", "TIINGO_API_KEY")),
        ("intrinio", ("INTRINIO_API_KEY",)),
    ]
    out: list[str] = []
    for provider, keys in mapping:
        if any(env.get(k) for k in keys):
            out.append(provider)
    forced = (os.environ.get("OPENBB_NEWS_PROVIDER") or "").strip().lower()
    if forced and forced not in out and forced != "yfinance":
        out.insert(0, forced)
    return out


def _fetch_yfinance_news(
    client: OpenBBMcpClient,
    *,
    symbols: list[str] | None = None,
    limit_per: int = 5,
    errors: list[str] | None = None,
) -> tuple[list[str], str]:
    """Free Yahoo Finance news via OpenBB news_company (no API key)."""
    symbols = symbols or ["BTC-USD", "ETH-USD"]
    err_out = errors if errors is not None else []
    seen: set[str] = set()
    headlines: list[str] = []
    for sym in symbols:
        try:
            news = client.call_tool(
                "news_company",
                {"provider": "yfinance", "symbol": sym, "limit": limit_per},
            )
            for title in _news_headlines(news, limit=limit_per):
                key = title.lower()
                if key in seen:
                    continue
                seen.add(key)
                headlines.append(title)
        except Exception as exc:  # noqa: BLE001
            err_out.append(f"news_yf:{sym}:{exc}")
    return headlines[:10], ("yfinance" if headlines else "")


def _classify_bias(
    *,
    btc_1d: float | None,
    btc_1h_window: float | None,
    eth_1d: float | None,
    equity_risk: float,
    news_score: float,
) -> tuple[str, float, list[str]]:
    """Return bias, confidence 0..1, reasons."""
    reasons: list[str] = []
    score = 0.0

    if btc_1d is not None:
        score += max(-2.0, min(2.0, btc_1d / 1.5))
        reasons.append(f"btc_1d={btc_1d:+.2f}%")
    if btc_1h_window is not None:
        score += max(-1.2, min(1.2, btc_1h_window / 0.8))
        reasons.append(f"btc_12h={btc_1h_window:+.2f}%")
    if eth_1d is not None and btc_1d is not None:
        rel = eth_1d - btc_1d
        score += max(-0.6, min(0.6, rel / 2.0))
        reasons.append(f"eth_rel={rel:+.2f}%")
    # Broad equity risk appetite as secondary risk-on/off
    score += max(-0.8, min(0.8, equity_risk / 2.5))
    if abs(equity_risk) >= 0.4:
        reasons.append(f"eq_risk={equity_risk:+.2f}")
    score += max(-0.8, min(0.8, news_score))
    if abs(news_score) >= 0.15:
        reasons.append(f"news={news_score:+.2f}")

    if score >= 1.35:
        bias = "risk_on"
    elif score >= 0.45:
        bias = "mild_on"
    elif score <= -1.35:
        bias = "risk_off"
    elif score <= -0.45:
        bias = "mild_off"
    else:
        bias = "neutral"
    conf = min(1.0, abs(score) / 2.2)
    return bias, round(conf, 3), reasons


def _news_sentiment(headlines: list[str]) -> float:
    blob = " ".join(headlines).lower()
    bullish = (
        "etf",
        "approval",
        "surge",
        "rally",
        "record high",
        "inflow",
        "bull",
        "breakthrough",
        "adoption",
        "all-time high",
        "beats estimates",
        "staking",
        "institutional",
        "accumulate",
        "buy the dip",
        "spot bitcoin",
        "spot ethereum",
    )
    bearish = (
        "hack",
        "ban",
        "lawsuit",
        "crash",
        "outflow",
        "bear",
        "liquidation",
        "sec charges",
        "fraud",
        "exploit",
        "bankrupt",
        "default",
        "sanctions",
        "selloff",
        "sell-off",
        "risk-off",
        "collapse",
        "investigation",
    )
    hits_b = sum(1 for w in bullish if w in blob)
    hits_r = sum(1 for w in bearish if w in blob)
    return hits_b * 0.22 - hits_r * 0.28


def _signals_from_bias(bias: str, confidence: float, btc_1d: float | None) -> dict[str, Any]:
    """Actionable knobs for vibe_autotrade."""
    score_delta = {
        "risk_on": 0.7,
        "mild_on": 0.35,
        "neutral": 0.0,
        "mild_off": -0.35,
        "risk_off": -0.8,
    }.get(bias, 0.0)
    # Scale by confidence so weak signals don't dominate
    score_delta = round(score_delta * (0.55 + 0.45 * confidence), 3)

    allow_buys = bias not in ("risk_off",)
    # Hard block only on strong risk_off
    hard_block = bias == "risk_off" and confidence >= 0.55
    prefer_majors = bias in ("mild_off", "risk_off", "neutral")
    block_alts = bias == "risk_off" or (bias == "mild_off" and confidence >= 0.5)
    # Extra caution if BTC daily dump
    if btc_1d is not None and btc_1d <= -3.0:
        allow_buys = False
        hard_block = True
        block_alts = True
        prefer_majors = True
        score_delta = min(score_delta, -0.6)

    return {
        "allow_buys": allow_buys,
        "hard_block": hard_block,
        "prefer_majors": prefer_majors,
        "block_alts": block_alts,
        "score_delta": score_delta,
        "majors_bonus": 0.25 if prefer_majors else 0.0,
        "alts_penalty": 0.45 if block_alts else (0.15 if prefer_majors else 0.0),
    }


def _append_history(doc: dict[str, Any]) -> None:
    try:
        HOME.mkdir(parents=True, exist_ok=True)
        slim = {
            "ts": doc.get("ts"),
            "ok": doc.get("ok"),
            "bias": doc.get("bias"),
            "confidence": doc.get("confidence"),
            "score_delta": doc.get("score_delta"),
            "btc_chg_pct": doc.get("btc_chg_pct"),
            "btc_12h_pct": doc.get("btc_12h_pct"),
            "eth_chg_pct": doc.get("eth_chg_pct"),
            "equity_risk": doc.get("equity_risk"),
            "hard_block": (doc.get("signals") or {}).get("hard_block"),
        }
        with HISTORY_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(slim, ensure_ascii=False) + "\n")
        # trim
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > HISTORY_KEEP:
            HISTORY_PATH.write_text("\n".join(lines[-HISTORY_KEEP:]) + "\n", encoding="utf-8")
    except Exception:
        pass


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
    btc_d: list[float] = []
    btc_h: list[float] = []
    eth_d: list[float] = []
    headlines: list[str] = []
    gainers: list[dict[str, Any]] = []
    losers: list[dict[str, Any]] = []

    try:
        client.initialize()
    except Exception as exc:  # noqa: BLE001
        doc = {
            "ok": False,
            "ts": now,
            "error": f"init:{exc}",
            "bias": "neutral",
            "confidence": 0.0,
            "btc_chg_pct": 0.0,
            "score_delta": 0.0,
            "headlines": [],
            "signals": _signals_from_bias("neutral", 0.0, None),
            "source": "openbb-mcp",
            "version": 2,
        }
        try:
            CACHE_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
        return doc

    start_d = time.strftime("%Y-%m-%d", time.gmtime(now - 14 * 86400))
    start_h = time.strftime("%Y-%m-%d", time.gmtime(now - 3 * 86400))

    # --- Crypto multi-timeframe (yfinance works without keys) ---
    try:
        btc_d = _closes(
            client.call_tool(
                "crypto_price_historical",
                {
                    "symbol": "BTCUSD",
                    "provider": "yfinance",
                    "interval": "1d",
                    "start_date": start_d,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"btc_1d:{exc}")

    try:
        btc_h = _closes(
            client.call_tool(
                "crypto_price_historical",
                {
                    "symbol": "BTCUSD",
                    "provider": "yfinance",
                    "interval": "1h",
                    "start_date": start_h,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"btc_1h:{exc}")

    try:
        eth_d = _closes(
            client.call_tool(
                "crypto_price_historical",
                {
                    "symbol": "ETHUSD",
                    "provider": "yfinance",
                    "interval": "1d",
                    "start_date": start_d,
                },
            )
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"eth_1d:{exc}")

    # --- Equity risk appetite (proxy when crypto news keys missing) ---
    try:
        gainers = _discovery_summary(
            client.call_tool("equity_discovery_gainers", {"provider": "yfinance", "limit": 8}),
            limit=8,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"gainers:{exc}")
    try:
        losers = _discovery_summary(
            client.call_tool("equity_discovery_losers", {"provider": "yfinance", "limit": 8}),
            limit=8,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"losers:{exc}")

    # --- News: free yfinance first (BTC/ETH), then paid providers if keys exist ---
    headlines, news_source = _fetch_yfinance_news(
        client,
        symbols=["BTC-USD", "ETH-USD"],
        limit_per=5,
        errors=errors,
    )
    if not headlines:
        for provider in _news_providers():
            try:
                news = client.call_tool("news_world", {"provider": provider, "limit": 10})
                headlines = _news_headlines(news, limit=8)
                if headlines:
                    news_source = provider
                    break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"news:{provider}:{exc}")
            try:
                news = client.call_tool(
                    "news_company",
                    {"provider": provider, "symbol": "BTC-USD", "limit": 8},
                )
                headlines = _news_headlines(news, limit=8)
                if headlines:
                    news_source = provider
                    break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"news_co:{provider}:{exc}")

    btc_1d = _pct_chg(btc_d, lookback=7)  # ~1w window on daily
    if btc_1d is None:
        btc_1d = _pct_chg(btc_d)
    btc_12h = _pct_chg(btc_h, lookback=12)
    eth_1d = _pct_chg(eth_d, lookback=7)
    if eth_1d is None:
        eth_1d = _pct_chg(eth_d)

    g_avg = _avg_pct(gainers)
    # Discovery "losers" sometimes returns odd positive prints — only count downside
    l_down = [float(x.get("pct") or 0.0) for x in losers]
    l_avg = sum(min(v, 0.0) for v in l_down) / max(len(l_down), 1)
    # Gainers list is biased by construction; dampen so it doesn't dominate crypto
    equity_risk = round(min(1.8, g_avg * 0.2) + max(-1.8, l_avg * 0.35), 3)
    news_score = _news_sentiment(headlines)

    bias, confidence, reasons = _classify_bias(
        btc_1d=btc_1d,
        btc_1h_window=btc_12h,
        eth_1d=eth_1d,
        equity_risk=equity_risk,
        news_score=news_score,
    )
    signals = _signals_from_bias(bias, confidence, btc_1d)
    # Merge news into score_delta lightly (already in bias, but keep explicit)
    signals["score_delta"] = round(float(signals["score_delta"]) + max(-0.2, min(0.2, news_score)), 3)

    ok = bool(btc_d or btc_h or eth_d or gainers)
    doc: dict[str, Any] = {
        "ok": ok,
        "ts": now,
        "bias": bias,
        "confidence": confidence,
        "reasons": reasons,
        "btc_chg_pct": round(btc_1d or 0.0, 3),
        "btc_12h_pct": round(btc_12h or 0.0, 3) if btc_12h is not None else None,
        "eth_chg_pct": round(eth_1d or 0.0, 3),
        "equity_risk": equity_risk,
        "news_score": round(news_score, 3),
        "score_delta": signals["score_delta"],
        "signals": signals,
        "headlines": headlines[:6],
        "news_source": news_source or None,
        "gainers": gainers[:5],
        "losers": losers[:5],
        "btc_last": btc_d[-1] if btc_d else (btc_h[-1] if btc_h else None),
        "eth_last": eth_d[-1] if eth_d else None,
        "errors": errors[:12],
        "source": "openbb-mcp",
        "version": 2,
    }
    if not ok and errors:
        doc["error"] = errors[0]

    try:
        HOME.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass
    _append_history(doc)
    return doc


def load_cached_intel() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    print(json.dumps(build_market_intel(force=True), indent=2, ensure_ascii=False)[:3500])
