#!/usr/bin/env python3
"""Export Open WebUI chats to shared chat_history/ for Ops historial."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

DB = Path(os.environ.get("WEBUI_DB", "/app/backend/data/webui.db"))
OUT = Path(os.environ.get("CHAT_HISTORY_DIR", "/data/chat_history"))


def _preview(text: str, n: int = 160) -> str:
    t = " ".join(str(text or "").split())
    return t[:n] + ("…" if len(t) > n else "")


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

    # Prefer tree order via messages[] ids if present
    ordered_ids = []
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

    out = []
    seen = set()
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
    # include orphans
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


def export_all() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "chats").mkdir(parents=True, exist_ok=True)
    if not DB.exists():
        print(f"missing db {DB}")
        return 1

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    try:
        rows = con.execute(
            "SELECT id, user_id, title, created_at, updated_at, archived, pinned, chat "
            "FROM chat ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        con.close()

    index = []
    turns_path = OUT / "turns.jsonl"
    # Rebuild turns from full export for consistency
    turn_lines: list[str] = []

    for cid, user_id, title, created_at, updated_at, archived, pinned, blob in rows:
        try:
            chat = json.loads(blob) if isinstance(blob, str) else blob
        except Exception:
            chat = {}
        messages = _flatten_messages(chat if isinstance(chat, dict) else {})
        snap = {
            "id": cid,
            "user_id": user_id,
            "title": title or "Sin título",
            "created_at": created_at,
            "updated_at": updated_at,
            "archived": bool(archived),
            "pinned": bool(pinned),
            "models": (chat or {}).get("models") if isinstance(chat, dict) else [],
            "message_count": len(messages),
            "messages": messages,
            "exported_at": int(time.time()),
            "open_url": f"https://synaptika-trade.duckdns.org:8443/c/{cid}",
        }
        (OUT / "chats" / f"{cid}.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # Append user/assistant pairs into turns rebuild
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
                "models": (chat or {}).get("models") if isinstance(chat, dict) else [],
                "open_url": f"https://synaptika-trade.duckdns.org:8443/c/{cid}",
            }
        )

    (OUT / "index.json").write_text(
        json.dumps(
            {"ts": int(time.time()), "count": len(index), "chats": index},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    turns_path.write_text("\n".join(turn_lines) + ("\n" if turn_lines else ""), encoding="utf-8")
    print(f"ok exported chats={len(index)} turns={len(turn_lines)} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(export_all())
