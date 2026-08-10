#!/usr/bin/env python3
"""Synaptika Trade Ops panel — SSR dashboard + read/write tools API."""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

import chat_history as chathist
import control as opsctl
import control_api
import data as botdata

app = Flask(__name__, template_folder="templates", static_folder=None)
app.secret_key = os.environ.get("OPS_SECRET", secrets.token_hex(16))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("OPS_COOKIE_SECURE", "1") != "0",
)

VIBE = Path(os.environ.get("VIBE_HOME", "/data/vibe"))
ALPACA = Path(os.environ.get("ALPACA_HOME", "/data/alpaca"))
AUDIT = Path(os.environ.get("OPS_AUDIT", "/data/ops_audit.jsonl"))
PASSWORD = os.environ.get("OPS_PASSWORD", "changeme")
API_KEY = os.environ.get("OPS_API_KEY", "").strip()
BRAND = os.environ.get("BRAND_NAME", "Synaptika Trade")
BRAND_DIR = Path(os.environ.get("BRAND_DIR", "/brand"))
HERE = Path(__file__).resolve().parent
CHAT_URL = os.environ.get(
    "CHAT_URL", "https://synaptika-trade.duckdns.org:8443/__boot"
)
CDMX = ZoneInfo("America/Mexico_City")


def _audit(action: str, detail: str = "") -> None:
    row = {
        "ts": time.time(),
        "action": action,
        "detail": detail[:400],
        "ip": request.remote_addr,
    }
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _authed() -> bool:
    if session.get("ok"):
        return True
    if API_KEY:
        hdr = request.headers.get("X-Ops-Key") or ""
        if secrets.compare_digest(hdr, API_KEY):
            return True
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer ") and secrets.compare_digest(
            auth[7:].strip(), API_KEY
        ):
            return True
    return False


def login_required(fn):
    @wraps(fn)
    def wrap(*a, **k):
        if not session.get("ok"):
            return redirect(url_for("login"))
        return fn(*a, **k)

    return wrap


def api_auth_required(fn):
    @wraps(fn)
    def wrap(*a, **k):
        if not _authed():
            return jsonify({"error": "unauthorized"}), 401
        return fn(*a, **k)

    return wrap


@app.template_filter("ts_cdmx")
def ts_cdmx(ts):
    if ts is None or ts == "":
        return "—"
    try:
        if isinstance(ts, str):
            s = ts.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(CDMX).strftime("%Y-%m-%d %H:%M")
        t = float(ts)
    except (TypeError, ValueError):
        return "—"
    if t > 1e12:
        t /= 1000.0
    try:
        dt = datetime.fromtimestamp(t, tz=timezone.utc).astimezone(CDMX)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "—"


@app.template_filter("feed_detail")
def feed_detail(row: dict) -> str:
    if not isinstance(row, dict):
        return "—"
    k = row.get("kind")
    if k == "trade":
        return (
            f"{row.get('side') or '?'} {row.get('symbol') or '?'} · "
            f"${row.get('usd') if row.get('usd') is not None else '?'} · "
            f"{row.get('result') or row.get('reason') or ''}"
        )[:180]
    if k == "skip":
        return f"SKIP {row.get('symbol') or ''} · {str(row.get('reason') or row.get('result') or '')[:120]}"
    if k in ("cycle", "CYCLE_END", "CYCLE_ERROR", "DECISION") or row.get("decision"):
        dec = row.get("decision") or {}
        action = dec.get("action") or row.get("action") or row.get("kind")
        reason = dec.get("reason") or row.get("reason") or ""
        return f"{action} · {str(reason)[:120]}"
    return str(row.get("reason") or row.get("kind") or "")[:160]


@app.template_filter("sparkline")
def sparkline(points) -> str:
    pts = list(points or [])[-80:]
    if len(pts) < 2:
        return '<p class="meta">Sin historial de equity aún.</p>'
    ys = []
    for p in pts:
        try:
            ys.append(float(p["equity"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(ys) < 2:
        return '<p class="meta">Sin historial de equity aún.</p>'
    w, h, pad = 520, 140, 8
    mn, mx = min(ys), max(ys)
    span = (mx - mn) or 1.0
    coords = []
    for i, y in enumerate(ys):
        x = pad + (w - 2 * pad) * (i / (len(ys) - 1))
        yy = pad + (h - 2 * pad) * (1 - (y - mn) / span)
        coords.append(f"{x:.1f},{yy:.1f}")
    poly = " ".join(coords)
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" height="140" '
        f'role="img" aria-label="equity">'
        f'<polyline fill="none" stroke="#2563eb" stroke-width="2" points="{poly}"/>'
        f"</svg>"
    )


# ---------- pages ----------


@app.get("/login")
def login():
    if session.get("ok"):
        return redirect("/")
    return render_template("login.html", brand=BRAND, error=None)


@app.post("/login")
def login_post():
    if request.form.get("password") == PASSWORD:
        session["ok"] = True
        _audit("login_ok")
        return redirect("/")
    _audit("login_fail")
    return render_template("login.html", brand=BRAND, error="Contraseña incorrecta"), 401


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/")
@app.get("/ops")
@app.get("/ops/refresh")
@login_required
def home():
    return render_template(
        "dashboard.html",
        brand=BRAND,
        status=botdata.full_status(VIBE, ALPACA),
        chat_url=CHAT_URL,
    )


@app.get("/ops/chat")
@login_required
def chat_embed():
    return render_template(
        "chat.html",
        brand=BRAND,
        chat_url=CHAT_URL,
    )


@app.get("/ops/historial")
@login_required
def historial():
    chats = chathist.list_chats(limit=100, sync=True)
    turns = chathist.recent_turns(limit=30)
    return render_template(
        "historial.html",
        brand=BRAND,
        chats=chats,
        turns=turns,
        chat_url=CHAT_URL,
    )


@app.get("/ops/historial/<chat_id>")
@login_required
def historial_chat(chat_id: str):
    chat = chathist.get_chat(chat_id, sync=True)
    if not chat:
        return redirect(url_for("historial"))
    return render_template(
        "historial_chat.html",
        brand=BRAND,
        chat=chat,
        chat_url=CHAT_URL,
    )


@app.get("/ops/api/authcheck")
def authcheck():
    """Caddy forward_auth for :8443. Also supplies Open WebUI trusted-auth headers."""
    if not session.get("ok"):
        return "", 401
    # Always plain 200 — never echo Upgrade/websocket (Caddy must strip those
    # headers on the auth subrequest; this stays defensive).
    resp = app.make_response(("", 200))
    resp.headers["X-WebUI-Email"] = "admin@synaptika.local"
    resp.headers["X-WebUI-Name"] = "Synaptika Admin"
    resp.headers["X-WebUI-Role"] = "admin"
    resp.headers["Connection"] = "close"
    return resp


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "brand": BRAND, "chat_url": CHAT_URL})


@app.get("/static/brand/<path:filename>")
def brand_static(filename: str):
    return send_from_directory(BRAND_DIR, filename)


@app.get("/static/ops/<path:filename>")
def ops_static(filename: str):
    return send_from_directory(HERE / "static", filename)


# ---------- JSON API ----------


@app.get("/ops/api/status")
@api_auth_required
def api_status():
    return jsonify(botdata.full_status(VIBE, ALPACA))


@app.get("/ops/api/equity")
@api_auth_required
def api_equity():
    venue = (request.args.get("venue") or "all").lower()
    out = {}
    if venue in ("binance", "all"):
        out["binance"] = botdata.equity_series(VIBE)
    if venue in ("alpaca", "all"):
        out["alpaca"] = botdata.equity_series(ALPACA)
    if venue in ("alpaca_scalp15", "scalp15", "all"):
        out["alpaca_scalp15"] = botdata.equity_series(
            Path(os.environ.get("ALPACA_SCALP15_HOME", "/data/alpaca_scalp15"))
        )
    out["ts"] = time.time()
    return jsonify(out)


@app.get("/ops/api/activity")
@api_auth_required
def api_activity():
    try:
        limit = int(request.args.get("limit") or 40)
    except ValueError:
        limit = 40
    limit = max(5, min(limit, 100))
    payload = botdata.activity(VIBE, ALPACA, limit=limit)
    payload["ts"] = time.time()
    return jsonify(payload)


@app.get("/ops/api/digest")
@api_auth_required
def api_digest():
    return jsonify({"ts": time.time(), "text": botdata.digest_text(VIBE, ALPACA)})


@app.get("/ops/api/winloss")
@api_auth_required
def api_winloss():
    payload = botdata.win_loss_table(VIBE, ALPACA)
    payload["ts"] = time.time()
    return jsonify(payload)


@app.get("/ops/api/copilot")
@api_auth_required
def api_copilot():
    """Rich brief for Open WebUI filter (trades + W/L + constraints)."""
    return jsonify(
        {
            "ts": time.time(),
            "text": botdata.copilot_context_text(VIBE, ALPACA),
        }
    )


@app.get("/ops/api/hermes")
@api_auth_required
def api_hermes():
    """Full VPS/bots digest for Hermes Agent (plain Spanish + systemd)."""
    return jsonify(
        {
            "ts": time.time(),
            "text": botdata.hermes_full_digest(VIBE, ALPACA),
            "overview": botdata.novice_overview(VIBE, ALPACA),
            "services": botdata.vps_services(),
        }
    )


@app.get("/ops/api/overview")
@api_auth_required
def api_overview():
    """Novice-friendly cards for Ops UI."""
    return jsonify(
        {
            "ts": time.time(),
            **botdata.novice_overview(VIBE, ALPACA),
            "services": botdata.vps_services(),
        }
    )


@app.get("/ops/api/situations")
@api_auth_required
def api_situations():
    return jsonify({"ts": time.time(), **botdata.live_situations(VIBE)})


@app.get("/ops/api/feedback")
@api_auth_required
def api_feedback():
    try:
        limit = int(request.args.get("limit") or 40)
    except ValueError:
        limit = 40
    rows = botdata.feedback_history(VIBE, limit=max(1, min(limit, 200)))
    return jsonify({"ts": time.time(), "count": len(rows), "feedback": rows})


@app.get("/ops/api/learning")
@api_auth_required
def api_learning():
    try:
        limit = int(request.args.get("limit") or 40)
    except ValueError:
        limit = 40
    rows = botdata.learning_history(VIBE, limit=max(1, min(limit, 200)))
    return jsonify(
        {
            "ts": time.time(),
            "count": len(rows),
            "learning": rows,
            "overlay": botdata.knobs_overlay_snapshot(VIBE),
        }
    )


@app.get("/ops/api/positions")
@api_auth_required
def api_positions():
    rows = botdata.open_positions_table(VIBE, ALPACA)
    return jsonify({"ts": time.time(), "count": len(rows), "positions": rows})


@app.get("/ops/api/trades")
@api_auth_required
def api_trades():
    try:
        limit = int(request.args.get("limit") or 40)
    except ValueError:
        limit = 40
    rows = botdata.trade_ledger(VIBE, ALPACA, limit=max(1, min(limit, 200)))
    return jsonify({"ts": time.time(), "count": len(rows), "trades": rows})


@app.get("/ops/api/historial")
@api_auth_required
def api_historial():
    try:
        limit = int(request.args.get("limit") or 50)
    except ValueError:
        limit = 50
    sync = (request.args.get("sync") or "1") != "0"
    chats = chathist.list_chats(limit=limit, sync=sync)
    return jsonify({"ts": time.time(), "count": len(chats), "chats": chats})


@app.post("/ops/api/historial/sync")
@api_auth_required
def api_historial_sync():
    result = chathist.sync_from_webui()
    result["ts"] = time.time()
    return jsonify(result)


@app.get("/ops/api/strategy")
@api_auth_required
def api_strategy():
    bn = botdata.binance_snapshot(VIBE)
    ap = botdata.alpaca_snapshot(ALPACA)
    return jsonify(
        {
            "ts": time.time(),
            "briefs": botdata.strategy_briefs(),
            "live": {
                "binance": {
                    "mode": bn.get("mode"),
                    "reason": bn.get("reason"),
                    "regime": bn.get("regime"),
                    "strategy": bn.get("strategy"),
                    "halt": bn.get("halt"),
                    "features": bn.get("features"),
                },
                "alpaca": {
                    "mode": ap.get("mode"),
                    "title": ap.get("title"),
                    "regime": ap.get("regime"),
                    "strategy": ap.get("strategy"),
                    "halt": ap.get("halt"),
                },
            },
            "control": opsctl.control_status(VIBE, ALPACA),
        }
    )


@app.get("/ops/api/openapi.json")
def api_openapi():
    server = request.url_root.rstrip("/")
    paths = {
        "/ops/api/status": {
            "get": {
                "operationId": "get_bot_status",
                "summary": "Snapshot completo Binance+Alpaca",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/digest": {
            "get": {
                "operationId": "get_bot_digest",
                "summary": "Digest ES de los bots",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/winloss": {
            "get": {
                "operationId": "get_win_loss",
                "summary": "Wins/Losses por bot",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/copilot": {
            "get": {
                "operationId": "get_copilot_brief",
                "summary": "Brief rico para el chat IA",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/hermes": {
            "get": {
                "operationId": "get_hermes_vps_digest",
                "summary": "Vista completa VPS+bots para Hermes",
                "description": (
                    "Resumen en español de Binance, Alpaca, scalp15, servicios systemd "
                    "y rutas del VPS. Preferido cuando Hermes reporte estado global."
                ),
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/overview": {
            "get": {
                "operationId": "get_novice_overview",
                "summary": "Tarjetas simples de cada bot",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/strategy": {
            "get": {
                "operationId": "get_strategy_briefs",
                "summary": "Briefs + modo live + control flags",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/activity": {
            "get": {
                "operationId": "get_bot_activity",
                "summary": "Ciclos / fills / skips",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/equity": {
            "get": {
                "operationId": "get_equity_history",
                "summary": "Serie de equity",
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
    paths.update(control_api.openapi_write_paths())
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "Synaptika Trade Ops Tools",
            "version": "2.0.0",
            "description": (
                "Live state + control for Binance smart-fast-v6 and Alpaca paper. "
                "Auth: Bearer OPS_API_KEY or X-Ops-Key. "
                "Write tools: Chat IA must ask the user to confirm before calling; then execute."
            ),
        },
        "servers": [{"url": server}],
        "paths": paths,
        "components": {
            "securitySchemes": {
                "OpsKey": {"type": "apiKey", "in": "header", "name": "X-Ops-Key"},
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
        "security": [{"OpsKey": []}, {"BearerAuth": []}],
    }
    return jsonify(spec)


@app.get("/api/status")
@login_required
def api_status_legacy():
    return redirect("/ops/api/status")


# Register write control routes (halt/mode/knobs/intent for Binance + Alpaca)
control_api.register(
    app,
    vibe=VIBE,
    alpaca=ALPACA,
    audit=_audit,
    api_auth_required=api_auth_required,
)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787)
