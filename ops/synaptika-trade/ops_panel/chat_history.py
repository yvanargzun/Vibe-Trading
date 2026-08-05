"""Load and sync Open WebUI chat history for Ops UI."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

CHAT_HISTORY_DIR = Path(os.environ.get("CHAT_HISTORY_DIR", "/data/chat_history"))
WEBUI_DB = Path(os.environ.get("WEBUI_DB", "/data/webui/webui.db"))
CHAT_PUBLIC_BASE = os.environ.get(
    "CHAT_PUBLIC_BASE", "https://synaptika-trade.duckdns.org:8443"
).rstrip("/")


def _preview(text: str, n: int = 160) -> str:
    t = " ".join(str(text or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _flatten_messages(chat: dict) -> list[dict]:
    hist = (chat.get("history") or {}).get("messages") or {}
    if not hist and isinstance(chat.get("messages"), list):
        out = []
        for i, m in enumerate(chat.get("messages") or []):
            if not isinstance(m, dict):
                continue
            out.append(
                {
                    "id": m.get("id") or f"m{i}",
                    "role": m.get("role"),
                    "content": m.get("content") or "",
                    "timestamp": m.get("timestamp"),
                    "model": m.get("model"),
                }
            )
        return out

    ordered_ids: list[str] = []
    for m in chat.get("messages") or []:
        if isinstance(m, dict) and m.get("id"):
            ordered_ids.append(m["id"])
        elif isinstance(m, str):
            ordered_ids.append(m)
    if not ordered_ids:
        ordered_ids = sorted(
            hist.keys(),
            key=lambda mid: float((hist.get(mid) or {}).get("timestamp") or 0),
        )

    out: list[dict] = []
    seen: set[str] = set()
    for mid in ordered_ids:
        if mid in seen:
            continue
        seen.add(mid)
        m = hist.get(mid) or {}
        out.append(
            {
                "id": mid,
                "role": m.get("role"),
                "content": m.get("content") or "",
                "timestamp": m.get("timestamp"),
                "model": m.get("model"),
                "done": m.get("done"),
            }
        )
    for mid, m in hist.items():
        if mid in seen:
            continue
        out.append(
            {
                "id": mid,
                "role": m.get("role"),
                "content": m.get("content") or "",
                "timestamp": m.get("timestamp"),
                "model": m.get("model"),
                "done": m.get("done"),
            }
        )
    return out


def sync_from_webui(db_path: Path | None = None) -> dict[str, Any]:
    """Export chats from Open WebUI SQLite into CHAT_HISTORY_DIR."""
    db = db_path or WEBUI_DB
    out_dir = CHAT_HISTORY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "chats").mkdir(parents=True, exist_ok=True)

    if not db.exists():
        return {"ok": False, "error": f"missing db {db}", "count": 0}

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
    try:
        rows = con.execute(
            "SELECT id, user_id, title, created_at, updated_at, archived, pinned, chat "
            "FROM chat ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        con.close()

    index: list[dict[str, Any]] = []
    turn_lines: list[str] = []

    for cid, user_id, title, created_at, updated_at, archived, pinned, blob in rows:
        try:
            chat = json.loads(blob) if isinstance(blob, str) else blob
        except Exception:
            chat = {}
        if not isinstance(chat, dict):
            chat = {}
        messages = _flatten_messages(chat)
        snap = {
            "id": cid,
            "user_id": user_id,
            "title": title or "Sin título",
            "created_at": created_at,
            "updated_at": updated_at,
            "archived": bool(archived),
            "pinned": bool(pinned),
            "models": chat.get("models") or [],
            "message_count": len(messages),
            "messages": messages,
            "exported_at": int(time.time()),
            "open_url": f"{CHAT_PUBLIC_BASE}/c/{cid}",
        }
        (out_dir / "chats" / f"{cid}.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        pending_user = None
        for m in messages:
            role = m.get("role")
            if role == "user":
                pending_user = m
            elif role == "assistant" and pending_user is not None:
                turn = {
                    "ts": m.get("timestamp") or updated_at,
                    "chat_id": cid,
                    "title": title or "Sin título",
                    "model": m.get("model"),
                    "user": pending_user.get("content") or "",
                    "assistant": m.get("content") or "",
                    "user_id": pending_user.get("id"),
                    "assistant_id": m.get("id"),
                }
                turn_lines.append(json.dumps(turn, ensure_ascii=False))
                pending_user = None

        preview = ""
        for m in reversed(messages):
            if m.get("role") == "user" and (m.get("content") or "").strip():
                preview = _preview(m.get("content") or "")
                break
        index.append(
            {
                "id": cid,
                "title": title or "Sin título",
                "created_at": created_at,
                "updated_at": updated_at,
                "archived": bool(archived),
                "pinned": bool(pinned),
                "message_count": len(messages),
                "preview": preview,
                "models": chat.get("models") or [],
                "open_url": f"{CHAT_PUBLIC_BASE}/c/{cid}",
            }
        )

    (out_dir / "index.json").write_text(
        json.dumps(
            {"ts": int(time.time()), "count": len(index), "chats": index},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out_dir / "turns.jsonl").write_text(
        "\n".join(turn_lines) + ("\n" if turn_lines else ""),
        encoding="utf-8",
    )
    return {"ok": True, "count": len(index), "turns": len(turn_lines)}


def append_turn(turn: dict[str, Any]) -> None:
    """Append one live turn from the Open WebUI filter outlet."""
    CHAT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    (CHAT_HISTORY_DIR / "chats").mkdir(parents=True, exist_ok=True)
    path = CHAT_HISTORY_DIR / "turns.jsonl"
    line = json.dumps(turn, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

    cid = str(turn.get("chat_id") or "").strip()
    if not cid or "/" in cid or ".." in cid:
        return
    chat_path = CHAT_HISTORY_DIR / "chats" / f"{cid}.json"
    snap = _read_json(chat_path)
    if not snap:
        snap = {
            "id": cid,
            "title": turn.get("title") or "Chat en vivo",
            "created_at": turn.get("ts") or time.time(),
            "updated_at": turn.get("ts") or time.time(),
            "messages": [],
            "open_url": f"{CHAT_PUBLIC_BASE}/c/{cid}",
        }
    msgs = list(snap.get("messages") or [])
    ts = turn.get("ts") or time.time()
    if turn.get("user"):
        msgs.append(
            {
                "id": turn.get("user_id") or f"u-{ts}",
                "role": "user",
                "content": turn.get("user") or "",
                "timestamp": ts,
            }
        )
    if turn.get("assistant"):
        msgs.append(
            {
                "id": turn.get("assistant_id") or f"a-{ts}",
                "role": "assistant",
                "content": turn.get("assistant") or "",
                "timestamp": ts,
                "model": turn.get("model"),
            }
        )
    snap["messages"] = msgs
    snap["message_count"] = len(msgs)
    snap["updated_at"] = ts
    if turn.get("title"):
        snap["title"] = turn["title"]
    chat_path.write_text(
        json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def list_chats(limit: int = 100, *, sync: bool = True) -> list[dict[str, Any]]:
    if sync and WEBUI_DB.exists():
        try:
            sync_from_webui()
        except Exception:
            pass
    data = _read_json(CHAT_HISTORY_DIR / "index.json")
    chats = list(data.get("chats") or [])
    return chats[: max(1, min(limit, 500))]


def get_chat(chat_id: str, *, sync: bool = False) -> dict[str, Any] | None:
    if not chat_id or "/" in chat_id or "\\" in chat_id or ".." in chat_id:
        return None
    if sync and WEBUI_DB.exists():
        try:
            sync_from_webui()
        except Exception:
            pass
    path = CHAT_HISTORY_DIR / "chats" / f"{chat_id}.json"
    data = _read_json(path)
    return data or None


def recent_turns(limit: int = 40) -> list[dict[str, Any]]:
    path = CHAT_HISTORY_DIR / "turns.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(limit * 2, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out[-limit:]))
