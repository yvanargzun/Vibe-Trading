#!/usr/bin/env python3
"""Equity-over-time chart for Telegram digests (Alpaca / Binance)."""

from __future__ import annotations

import json
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

MAX_POINTS = 1200
MAX_MARKERS = 80


def _load(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"points": [], "markers": [], "start_equity": None}


def _save(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def record_equity(
    history_path: Path,
    equity: float,
    *,
    min_interval_sec: float = 240.0,
) -> dict:
    doc = _load(history_path)
    points = list(doc.get("points") or [])
    now = time.time()
    if points:
        last = points[-1]
        if now - float(last.get("ts") or 0) < min_interval_sec and abs(
            float(last.get("equity") or 0) - equity
        ) < 1e-6:
            return doc
    if doc.get("start_equity") is None:
        doc["start_equity"] = round(equity, 6)
    points.append({"ts": now, "equity": round(equity, 6)})
    doc["points"] = points[-MAX_POINTS:]
    _save(history_path, doc)
    return doc


def record_goal_marker(
    history_path: Path,
    *,
    kind: str,
    equity: float,
    label: str | None = None,
) -> dict:
    doc = _load(history_path)
    markers = list(doc.get("markers") or [])
    markers.append(
        {
            "ts": time.time(),
            "kind": kind,
            "equity": round(equity, 6),
            "label": label or ("Meta dia" if kind == "daily" else "Meta semana"),
        }
    )
    doc["markers"] = markers[-MAX_MARKERS:]
    points = list(doc.get("points") or [])
    points.append({"ts": time.time(), "equity": round(equity, 6)})
    doc["points"] = points[-MAX_POINTS:]
    _save(history_path, doc)
    return doc


def record_trade_marker(
    history_path: Path,
    *,
    kind: str,
    equity: float,
    label: str | None = None,
    price: float | None = None,
    bot: str | None = None,
    symbol: str | None = None,
) -> dict:
    """Record buy/sell/win/loss/mode_change marker for annotated alerts."""
    doc = _load(history_path)
    markers = list(doc.get("markers") or [])
    markers.append(
        {
            "ts": time.time(),
            "kind": kind,
            "equity": round(float(equity), 6),
            "label": label or kind,
            "price": price,
            "bot": bot,
            "symbol": symbol,
        }
    )
    # Keep last 40 trade-ish + goals
    doc["markers"] = markers[-MAX_MARKERS:]
    _save(history_path, doc)
    return doc


def _fmt_money(v: float) -> str:
    if abs(v) >= 10000:
        return f"${v:,.0f}"
    if abs(v) >= 100:
        return f"${v:,.2f}"
    return f"${v:.2f}"


def render_chart(
    history_path: Path,
    out_path: Path,
    *,
    venue_tag: str,
    equity_now: float,
    day_open: float | None = None,
    daily_target: float | None = None,
    week_open: float | None = None,
    weekly_target: float | None = None,
) -> Path | None:
    """Clean, sharp, simple equity chart. Returns PNG path or None."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        print("CHART_IMPORT_FAIL", exc)
        return None

    doc = _load(history_path)
    points = list(doc.get("points") or [])
    markers = list(doc.get("markers") or [])
    if len(points) < 1:
        points = [{"ts": time.time(), "equity": equity_now}]
    if len(points) == 1:
        points = [
            {
                "ts": points[0]["ts"] - 3600,
                "equity": float(doc.get("start_equity") or points[0]["equity"]),
            },
            points[0],
        ]

    xs = [datetime.fromtimestamp(float(p["ts"]), tz=timezone.utc) for p in points]
    ys = [float(p["equity"]) for p in points]
    start_eq = float(doc.get("start_equity") or ys[0])

    # Light, high-contrast, mobile-friendly
    bg = "#ffffff"
    ink = "#111827"
    mute = "#6b7280"
    grid_c = "#e5e7eb"
    line_c = "#0f766e"
    start_c = "#b45309"
    now_c = "#1d4ed8"
    day_c = "#d97706"
    week_c = "#7c3aed"
    hit_day = "#ea580c"
    hit_week = "#6d28d9"

    fig, ax = plt.subplots(figsize=(9.0, 4.8), dpi=180)
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(bg)

    y_min = min(ys)
    y_max = max(ys)
    guides = []
    if day_open is not None:
        guides.append(float(day_open))
    if day_open and daily_target and daily_target > 0:
        guides.append(float(day_open) + float(daily_target))
    if week_open and weekly_target and weekly_target > 0:
        guides.append(float(week_open) + float(weekly_target))
    if guides:
        y_min = min(y_min, *guides)
        y_max = max(y_max, *guides)
    pad = max((y_max - y_min) * 0.12, abs(y_max) * 0.0015, 0.05)
    ax.set_ylim(y_min - pad, y_max + pad)

    # Goal guides (behind series)
    if day_open is not None:
        ax.axhline(
            float(day_open),
            color=mute,
            linestyle=":",
            linewidth=1.2,
            label=f"Apertura dia {_fmt_money(float(day_open))}",
            zorder=1,
        )
    if day_open and daily_target and daily_target > 0:
        day_goal = float(day_open) + float(daily_target)
        ax.axhline(
            day_goal,
            color=day_c,
            linestyle="--",
            linewidth=1.6,
            label=f"Meta dia {_fmt_money(day_goal)}",
            zorder=1,
        )
    if week_open and weekly_target and weekly_target > 0:
        week_goal = float(week_open) + float(weekly_target)
        ax.axhline(
            week_goal,
            color=week_c,
            linestyle="--",
            linewidth=1.6,
            label=f"Meta semana {_fmt_money(week_goal)}",
            zorder=1,
        )

    ax.plot(
        xs,
        ys,
        color=line_c,
        linewidth=2.6,
        solid_capstyle="round",
        zorder=3,
        label="Capital",
    )
    ax.fill_between(xs, ys, y_min - pad, color=line_c, alpha=0.08, zorder=2)

    ax.scatter([xs[0]], [ys[0]], s=55, color=start_c, zorder=5, edgecolors="white", linewidths=1.2)
    ax.scatter(
        [xs[-1]],
        [ys[-1]],
        s=65,
        color=now_c,
        zorder=5,
        edgecolors="white",
        linewidths=1.2,
    )

    # Goal hits + trade markers
    kind_style = {
        "daily": (hit_day, "*", 120, "Meta dia"),
        "weekly": (hit_week, "*", 120, "Meta semana"),
        "buy": ("#16a34a", "^", 70, "Compra"),
        "sell": ("#2563eb", "v", 70, "Venta"),
        "win": ("#15803d", "v", 90, "Ganancia"),
        "loss": ("#dc2626", "x", 80, "Perdida"),
        "mode_change": ("#a855f7", "|", 0, "Cambio modo"),
    }
    seen = set()
    # Only last 40 trade-related markers in view
    trade_kinds = {"buy", "sell", "win", "loss", "mode_change"}
    trade_ms = [m for m in markers if str(m.get("kind")) in trade_kinds][-40:]
    goal_ms = [m for m in markers if str(m.get("kind")) in ("daily", "weekly")]
    for m in goal_ms + trade_ms:
        try:
            mt = datetime.fromtimestamp(float(m["ts"]), tz=timezone.utc)
            me = float(m.get("equity") or equity_now)
        except (TypeError, ValueError, KeyError):
            continue
        if mt < xs[0] or mt > xs[-1]:
            continue
        kind = str(m.get("kind") or "")
        color, mark, size, legend = kind_style.get(kind, ("#dc2626", "*", 100, kind))
        if kind == "mode_change":
            ax.axvline(mt, color=color, linestyle="--", linewidth=1.0, alpha=0.55, zorder=4)
            lab = legend if legend not in seen else None
            if lab:
                seen.add(legend)
            ax.text(
                mt,
                y_max - pad * 0.15,
                str(m.get("label") or "")[:8],
                color=color,
                fontsize=7,
                rotation=90,
                va="top",
                ha="right",
                alpha=0.85,
            )
            continue
        lab = legend if legend not in seen else None
        if lab:
            seen.add(legend)
        ax.scatter(
            [mt],
            [me],
            s=size,
            marker=mark,
            color=color,
            zorder=6,
            edgecolors="white",
            linewidths=0.8,
            label=lab,
        )

    delta = equity_now - start_eq
    sign = "+" if delta >= 0 else ""
    pct = (delta / start_eq * 100.0) if start_eq else 0.0

    ax.set_title(
        f"[{venue_tag}]  Capital  ·  inicio {_fmt_money(start_eq)}  →  ahora {_fmt_money(equity_now)}  ({sign}{pct:.2f}%)",
        color=ink,
        fontsize=12.5,
        fontweight="bold",
        pad=12,
        loc="left",
    )
    ax.set_ylabel("USD", color=mute, fontsize=10)
    ax.tick_params(colors=ink, labelsize=9)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(grid_c)
    ax.spines["bottom"].set_color(grid_c)
    ax.grid(True, axis="y", color=grid_c, linewidth=0.9)
    ax.grid(False, axis="x")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m\n%H:%M"))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _p: _fmt_money(v)))

    # Footer summary (clear, no floating clutter)
    ax.text(
        0.0,
        -0.18,
        f"Inicio {_fmt_money(start_eq)}   ·   Ahora {_fmt_money(equity_now)}   ·   Cambio {sign}{_fmt_money(delta)} ({sign}{pct:.2f}%)",
        transform=ax.transAxes,
        color=mute,
        fontsize=9.5,
        ha="left",
        va="top",
    )

    leg = ax.legend(
        loc="upper left",
        fontsize=8.5,
        frameon=True,
        fancybox=False,
        edgecolor=grid_c,
        framealpha=0.96,
        facecolor=bg,
        labelcolor=ink,
        borderpad=0.6,
    )
    leg.get_frame().set_linewidth(1.0)

    fig.tight_layout()
    fig.subplots_adjust(bottom=0.22)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        out_path,
        facecolor=bg,
        edgecolor="none",
        bbox_inches="tight",
        pad_inches=0.25,
        dpi=180,
    )
    plt.close(fig)
    return out_path


def render_composite_alert(
    text: str,
    history_path: Path,
    out_path: Path,
    *,
    venue_tag: str,
    equity_now: float,
    day_open: float | None = None,
    daily_target: float | None = None,
    week_open: float | None = None,
    weekly_target: float | None = None,
) -> Path | None:
    """One PNG: alert text on top, equity chart below. Returns path or None."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.image as mpimg
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        print("COMPOSITE_IMPORT_FAIL", exc)
        return None

    tmp = out_path.parent / f"_tmp_chart_{uuid.uuid4().hex}.png"
    chart = render_chart(
        history_path,
        tmp,
        venue_tag=venue_tag,
        equity_now=equity_now,
        day_open=day_open,
        daily_target=daily_target,
        week_open=week_open,
        weekly_target=weekly_target,
    )
    if chart is None or not chart.exists():
        return None

    try:
        img = mpimg.imread(str(chart))
        # Estimate text block height from line count
        lines = max(4, min(18, text.count("\n") + 1))
        text_h = 0.55 + lines * 0.11
        fig = plt.figure(figsize=(9.2, 5.2 + text_h), dpi=150, facecolor="#ffffff")
        gs = fig.add_gridspec(2, 1, height_ratios=[text_h, 5.0], hspace=0.06)
        ax_t = fig.add_subplot(gs[0])
        ax_t.set_facecolor("#ffffff")
        ax_t.axis("off")
        ax_t.text(
            0.02,
            0.98,
            text.strip(),
            transform=ax_t.transAxes,
            va="top",
            ha="left",
            fontsize=11.5,
            color="#111827",
            family="DejaVu Sans",
            linespacing=1.35,
            wrap=True,
        )
        ax_c = fig.add_subplot(gs[1])
        ax_c.axis("off")
        ax_c.imshow(img)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(
            out_path,
            facecolor="#ffffff",
            edgecolor="none",
            bbox_inches="tight",
            pad_inches=0.2,
            dpi=150,
        )
        plt.close(fig)
        return out_path
    except Exception as exc:  # noqa: BLE001
        print("COMPOSITE_RENDER_FAIL", exc)
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def send_text(token: str, chat: str, text: str) -> bool:
    body = json.dumps(
        {"chat_id": chat, "text": text[:3500], "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception as exc:  # noqa: BLE001
        print("TG_TEXT_FAIL", exc)
        return False


def send_photo(
    token: str,
    chat: str,
    photo_path: Path,
    caption: str = "",
) -> bool:
    if not token or not chat or not photo_path.exists():
        return False
    boundary = f"----TgBoundary{uuid.uuid4().hex}"
    caption = (caption or "")[:1024]
    file_bytes = photo_path.read_bytes()
    parts: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )

    add_field("chat_id", str(chat))
    if caption:
        add_field("caption", caption)
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="{photo_path.name}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return bool(json.loads(r.read()).get("ok"))
    except Exception as exc:  # noqa: BLE001
        print("TG_PHOTO_FAIL", exc)
        return False


def build_and_send(
    *,
    history_path: Path,
    chart_path: Path,
    token: str,
    chat: str,
    venue_tag: str,
    equity: float,
    text: str,
    day_open: float | None = None,
    daily_target: float | None = None,
    week_open: float | None = None,
    weekly_target: float | None = None,
    chart_caption: str | None = None,
    channel: str = "vibe",
    force: bool = False,
) -> tuple[bool, bool, Path | None]:
    """One Telegram message: composite PNG (text above, chart below).

    ``chart_caption`` is ignored (kept for call-site compatibility).
    Returns ``(ok, ok, png)`` on photo success, or ``(text_ok, False, None)``
    on text-only fallback. Never sends two messages.

    Respects telegram_notify_prefs unless ``force=True`` (on-demand /status).
    """
    _ = chart_caption
    if not force:
        try:
            import sys

            sys.path.insert(0, "/root/.vibe-trading")
            from telegram_notify_prefs import should_notify

            if not should_notify(channel):
                print(f"TG_FILTER_SKIP build_and_send channel={channel}")
                return False, False, None
        except Exception as exc:  # noqa: BLE001
            print("TG_PREFS_WARN", exc)
    record_equity(history_path, equity)
    png = render_composite_alert(
        text,
        history_path,
        chart_path,
        venue_tag=venue_tag,
        equity_now=equity,
        day_open=day_open,
        daily_target=daily_target,
        week_open=week_open,
        weekly_target=weekly_target,
    )
    if png and send_photo(token, chat, png, caption=""):
        return True, True, png
    # Fallback: single text bubble only
    ok = send_text(token, chat, text)
    return ok, False, None
