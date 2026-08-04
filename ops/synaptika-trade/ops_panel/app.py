#!/usr/bin/env python3
"""Synaptika Trade Ops panel — posiciones + acciones simples."""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from functools import wraps

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    session,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.environ.get("OPS_SECRET", secrets.token_hex(16))

VIBE = Path(os.environ.get("VIBE_HOME", "/data/vibe"))
ALPACA = Path(os.environ.get("ALPACA_HOME", "/data/alpaca"))
AUDIT = Path(os.environ.get("OPS_AUDIT", "/data/ops_audit.jsonl"))
PASSWORD = os.environ.get("OPS_PASSWORD", "changeme")
BRAND = os.environ.get("BRAND_NAME", "Synaptika Trade")
BRAND_DIR = Path(os.environ.get("BRAND_DIR", "/brand"))


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _audit(action: str, detail: str = "") -> None:
    row = {"ts": time.time(), "action": action, "detail": detail[:400], "ip": request.remote_addr}
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def login_required(fn):
    @wraps(fn)
    def wrap(*a, **k):
        if not session.get("ok"):
            return redirect(url_for("login"))
        return fn(*a, **k)

    return wrap


def binance_snapshot() -> dict:
    st = _read_json(VIBE / "autotrade_state.json")
    mode = _read_json(VIBE / "strategy_mode.json")
    g = st.get("goals") or {}
    eq = float(st.get("equity") or 0)
    day_open = float(g.get("day_open_equity") or eq or 1)
    day_pnl_pct = ((eq - day_open) / day_open * 100.0) if day_open else 0.0
    pos = st.get("positions") or {}
    legs = [
        {"asset": a, "usd": round(float((m or {}).get("usd") or 0), 2)}
        for a, m in pos.items()
        if float((m or {}).get("usd") or 0) >= 0.5
    ]
    feats = mode.get("features") or {}
    return {
        "venue": "Binance",
        "equity": round(eq, 2),
        "usable": round(float(feats.get("usable_usdt") or 0), 2),
        "mode": mode.get("mode") or "?",
        "reason": str(mode.get("reason") or "")[:120],
        "regime": (st.get("regime") or feats.get("btc_regime") or "?"),
        "day_pnl_pct": round(day_pnl_pct, 2),
        "buys_today": st.get("buys_today"),
        "legs": legs,
        "halt": (VIBE / "HALT").exists(),
        "strategy": st.get("strategy") or "smart-fast-v6",
    }


def alpaca_snapshot() -> dict:
    st = _read_json(ALPACA / "state.json")
    g = st.get("goals") or {}
    eq = float(st.get("equity") or st.get("last_equity") or 0)
    day_open = float(g.get("day_open_equity") or eq or 1)
    day_pnl_pct = ((eq - day_open) / day_open * 100.0) if day_open else 0.0
    sleeves = st.get("sleeves") or {}
    legs = []
    for sk, book in sleeves.items():
        for a, m in ((book or {}).get("positions") or {}).items():
            usd = float((m or {}).get("usd") or 0)
            if usd >= 0.5:
                legs.append({"asset": a, "usd": round(usd, 2), "sleeve": sk})
    return {
        "venue": "Alpaca",
        "equity": round(eq, 2),
        "mode": st.get("active_mode") or "?",
        "title": st.get("mode_title") or "",
        "regime": st.get("regime") or "?",
        "day_pnl_pct": round(day_pnl_pct, 2),
        "legs": legs,
        "halt": (ALPACA / "HALT").exists(),
        "strategy": st.get("strategy") or "?",
    }


def recent_trace(n: int = 5) -> list[dict]:
    p = VIBE / "v6_cycles.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines()[-n * 15 :]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    ends = [r for r in rows if r.get("kind") in ("CYCLE_END", "CYCLE_ERROR", "DECISION")][-n:]
    return ends


PAGE = r"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>{{ brand }}</title>
  <link rel="icon" href="/static/brand/logo-icon.png"/>
  <style>
    :root {
      --bg: #f4f6f8;
      --card: #ffffff;
      --ink: #0f172a;
      --muted: #64748b;
      --accent: #2563eb;
      --line: #e2e8f0;
      --ok: #059669;
      --bad: #dc2626;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
      background: radial-gradient(1200px 600px at 10% -10%, #dbeafe 0%, transparent 50%),
                  radial-gradient(900px 500px at 100% 0%, #e0e7ff 0%, transparent 45%),
                  var(--bg);
      color: var(--ink);
      min-height: 100vh;
    }
    header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 1rem 1.25rem; border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,.85); backdrop-filter: blur(8px);
      position: sticky; top: 0; z-index: 10;
    }
    .brand { display: flex; gap: .75rem; align-items: center; }
    .brand img.icon { width: 40px; height: 40px; }
    .brand img.word { height: 28px; }
    .brand .sub { color: var(--muted); font-size: .85rem; }
    main { max-width: 980px; margin: 0 auto; padding: 1.25rem; }
    h1 { font-size: 1.35rem; margin: 0 0 .25rem; }
    .lead { color: var(--muted); margin: 0 0 1.25rem; }
    .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit,minmax(280px,1fr)); }
    .card {
      background: var(--card); border: 1px solid var(--line); border-radius: 16px;
      padding: 1.1rem 1.15rem; box-shadow: 0 8px 24px rgba(15,23,42,.04);
    }
    .card h2 { margin: 0 0 .75rem; font-size: 1.05rem; }
    .metric { font-size: 1.7rem; font-weight: 700; letter-spacing: -.02em; }
    .meta { color: var(--muted); font-size: .9rem; margin-top: .2rem; }
    .chip {
      display: inline-block; padding: .2rem .55rem; border-radius: 999px;
      background: #eff6ff; color: var(--accent); font-size: .8rem; font-weight: 600;
    }
    .chip.warn { background: #fef2f2; color: var(--bad); }
    .chip.ok { background: #ecfdf5; color: var(--ok); }
    ul.legs { margin: .6rem 0 0; padding: 0; list-style: none; }
    ul.legs li {
      display: flex; justify-content: space-between; padding: .35rem 0;
      border-top: 1px dashed var(--line); font-size: .95rem;
    }
    .actions { display: grid; gap: .75rem; margin-top: 1.25rem; }
    a.btn, button.btn {
      display: block; text-align: center; text-decoration: none; cursor: pointer;
      border: none; border-radius: 14px; padding: 1rem 1.1rem; font-size: 1.05rem;
      font-weight: 650; color: white; background: var(--accent);
      box-shadow: 0 10px 20px rgba(37,99,235,.22);
    }
    a.btn.secondary { background: #0f172a; box-shadow: none; }
    a.btn.ghost { background: white; color: var(--ink); border: 1px solid var(--line); box-shadow: none; }
    .login {
      max-width: 380px; margin: 12vh auto; background: var(--card);
      border: 1px solid var(--line); border-radius: 18px; padding: 1.5rem;
    }
    .login input {
      width: 100%; padding: .8rem .9rem; border-radius: 10px; border: 1px solid var(--line);
      font-size: 1rem; margin: .75rem 0 1rem;
    }
    .err { color: var(--bad); font-size: .9rem; }
    footer { text-align: center; color: var(--muted); padding: 2rem 1rem; font-size: .85rem; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <img class="icon" src="/static/brand/logo-icon.png" alt=""/>
      <div>
        <img class="word" src="/static/brand/logo-wordmark.png" alt="{{ brand }}"/>
        <div class="sub">Control de trading · CDMX</div>
      </div>
    </div>
    {% if authed %}<a class="btn ghost" style="padding:.55rem .9rem;font-size:.9rem" href="/logout">Salir</a>{% endif %}
  </header>
  <main>
    {% if not authed %}
      <div class="login">
        <div class="brand" style="margin-bottom:1rem">
          <img class="icon" src="/static/brand/logo-icon.png" alt=""/>
          <strong>{{ brand }}</strong>
        </div>
        <p class="lead">Entra para ver posiciones y abrir el chat.</p>
        {% if error %}<p class="err">{{ error }}</p>{% endif %}
        <form method="post" action="/login">
          <label>Contraseña</label>
          <input type="password" name="password" autofocus required/>
          <button class="btn" type="submit">Entrar</button>
        </form>
      </div>
    {% else %}
      <h1>¿Qué quieres hacer?</h1>
      <p class="lead">Menú simple: mira el dinero real o habla con el copiloto.</p>

      <div class="grid">
        <section class="card">
          <h2>Binance <span class="chip">{{ bn.mode }}</span>
            {% if bn.halt %}<span class="chip warn">HALT</span>{% endif %}
          </h2>
          <div class="metric">${{ '%.2f'|format(bn.equity) }}</div>
          <div class="meta">usable ${{ '%.2f'|format(bn.usable) }} · day {{ '%+.2f'|format(bn.day_pnl_pct) }}% · {{ bn.regime }}</div>
          <ul class="legs">
            {% for L in bn.legs %}
              <li><span>{{ L.asset }}</span><strong>${{ '%.2f'|format(L.usd) }}</strong></li>
            {% else %}
              <li><span>Sin piernas abiertas</span><span>—</span></li>
            {% endfor %}
          </ul>
          <div class="meta" style="margin-top:.7rem">{{ bn.reason }}</div>
        </section>

        <section class="card">
          <h2>Alpaca <span class="chip">{{ ap.mode }}</span>
            {% if ap.halt %}<span class="chip warn">HALT</span>{% endif %}
          </h2>
          <div class="metric">${{ '%.2f'|format(ap.equity) }}</div>
          <div class="meta">day {{ '%+.2f'|format(ap.day_pnl_pct) }}% · {{ ap.regime }} · {{ ap.strategy }}</div>
          <ul class="legs">
            {% for L in ap.legs %}
              <li><span>{{ L.asset }}{% if L.sleeve %} · {{ L.sleeve }}{% endif %}</span><strong>${{ '%.2f'|format(L.usd) }}</strong></li>
            {% else %}
              <li><span>Sin piernas abiertas</span><span>—</span></li>
            {% endfor %}
          </ul>
        </section>
      </div>

      <div class="actions">
        <a class="btn" href="/chat/">Abrir chat IA (Open WebUI)</a>
        <a class="btn secondary" href="/ops/refresh">Actualizar datos</a>
        <a class="btn ghost" href="/api/status">Ver JSON de estado</a>
      </div>

      <section class="card" style="margin-top:1.25rem">
        <h2>Últimas decisiones Binance</h2>
        <ul class="legs">
          {% for d in decisions %}
            <li>
              <span>{{ d.get('kind') }} · {{ (d.get('decision') or {}).get('action') or d.get('action') or '-' }}</span>
              <strong>{{ (d.get('decision') or {}).get('reason') or d.get('reason') or '' }}</strong>
            </li>
          {% else %}
            <li><span>Sin ciclos aún</span><span>—</span></li>
          {% endfor %}
        </ul>
      </section>
    {% endif %}
  </main>
  <footer>{{ brand }} · synaptika-trade.duckdns.org</footer>
</body>
</html>
"""


@app.get("/login")
def login():
    if session.get("ok"):
        return redirect("/")
    return render_template_string(PAGE, brand=BRAND, authed=False, error=None)


@app.post("/login")
def login_post():
    if request.form.get("password") == PASSWORD:
        session["ok"] = True
        _audit("login_ok")
        return redirect("/")
    _audit("login_fail")
    return render_template_string(PAGE, brand=BRAND, authed=False, error="Contraseña incorrecta"), 401


@app.get("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.get("/")
@app.get("/ops")
@app.get("/ops/refresh")
def home():
    if not session.get("ok"):
        return redirect("/login")
    return render_template_string(
        PAGE,
        brand=BRAND,
        authed=True,
        bn=binance_snapshot(),
        ap=alpaca_snapshot(),
        decisions=list(reversed(recent_trace(6))),
        error=None,
    )


@app.get("/api/status")
@login_required
def api_status():
    return jsonify(
        {
            "binance": binance_snapshot(),
            "alpaca": alpaca_snapshot(),
            "decisions": recent_trace(8),
            "ts": time.time(),
        }
    )


@app.get("/static/brand/<path:filename>")
def brand_static(filename: str):
    return send_from_directory(BRAND_DIR, filename)


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "brand": BRAND})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787)
