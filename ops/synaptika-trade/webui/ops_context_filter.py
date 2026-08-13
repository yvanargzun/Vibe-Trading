"""
title: Synaptika Ops Context
author: synaptika
version: 0.3.0
description: Injects Ops brief into each turn and saves chat history to disk.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        copilot_url: str = Field(
            default="http://ops:8787/ops/api/copilot",
            description="Rich copiloto brief endpoint",
        )
        digest_url: str = Field(
            default="http://ops:8787/ops/api/digest",
            description="Ops digest endpoint (fallback)",
        )
        winloss_url: str = Field(
            default="http://ops:8787/ops/api/winloss",
            description="Wins/losses endpoint (fallback)",
        )
        activity_url: str = Field(
            default="http://ops:8787/ops/api/activity?limit=40",
            description="Activity endpoint (fallback)",
        )
        status_url: str = Field(
            default="http://ops:8787/ops/api/status",
            description="Ops status endpoint (last fallback)",
        )
        ops_api_key: str = Field(default="", description="OPS_API_KEY bearer")
        timeout_sec: int = Field(default=12)
        history_dir: str = Field(
            default="/data/chat_history",
            description="Shared folder for chat historial export",
        )
        save_history: bool = Field(
            default=True,
            description="Append each completed turn to historial JSONL",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    def _get(self, url: str) -> dict[str, Any] | None:
        key = (self.valves.ops_api_key or "").strip()
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
            headers["X-Ops-Key"] = key
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.valves.timeout_sec) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
            return None

    def _fallback_body(self) -> str:
        parts: list[str] = []
        digest = self._get(self.valves.digest_url)
        if digest and isinstance(digest.get("text"), str):
            parts.append(digest["text"].strip())
        wl = self._get(self.valves.winloss_url)
        if wl:
            parts.append("## Winloss JSON\n" + json.dumps(wl, ensure_ascii=False)[:4000])
        act = self._get(self.valves.activity_url)
        if act:
            slim = {
                "trades_binance": (act.get("trades_binance") or [])[:20],
                "trades_alpaca": (act.get("trades_alpaca") or [])[:20],
                "skips_binance": (act.get("skips_binance") or [])[:15],
                "cycles": (act.get("cycles") or [])[:10],
                "feed": (act.get("feed") or [])[:25],
            }
            parts.append("## Activity JSON\n" + json.dumps(slim, ensure_ascii=False)[:5000])
        if not parts:
            status = self._get(self.valves.status_url)
            if status:
                parts.append(json.dumps(status, ensure_ascii=False)[:8000])
        return "\n\n".join(parts).strip()

    def _live_block(self) -> str:
        copilot = self._get(self.valves.copilot_url)
        if copilot and isinstance(copilot.get("text"), str) and copilot["text"].strip():
            body = copilot["text"].strip()
        else:
            body = self._fallback_body()
        if not body:
            body = (
                "No pude leer Ops ahora. Di que no hay datos en vivo y no inventes números. "
                "No pidas get_bot_digest al usuario."
            )
        return (
            "## Estado live Ops (auto)\n"
            "Fuente PRIMARIA y COMPLETA de este turno (incluye trades/W-L/skips).\n"
            "NO pidas al usuario get_bot_digest, status, CSV ni capturas: ya está abajo.\n"
            "Puedes proponer y, tras confirmar con el usuario, ejecutar controles "
            "(modo/HALT/knobs/intents) con las write tools Ops.\n"
            "Si la pregunta NO es sobre estos bots del VPS, responde solo con el mensaje "
            "de fuera de alcance del system prompt.\n\n"
            f"{body}"
        )

    def _msg_text(self, msg: dict) -> str:
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    parts.append(part)
            return "\n".join(parts)
        return str(content or "")

    def _save_turn(
        self,
        body: dict,
        __user__: dict | None,
        __metadata__: dict | None,
    ) -> None:
        if not self.valves.save_history:
            return
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return

        user_msg = None
        assistant_msg = None
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "assistant" and assistant_msg is None:
                assistant_msg = msg
            elif role == "user" and user_msg is None and assistant_msg is not None:
                user_msg = msg
                break
            elif role == "user" and user_msg is None and assistant_msg is None:
                user_msg = msg

        user_text = self._msg_text(user_msg) if user_msg else ""
        asst_text = self._msg_text(assistant_msg) if assistant_msg else ""
        if not user_text.strip() and not asst_text.strip():
            return

        meta = __metadata__ or {}
        chat_id = (
            meta.get("chat_id")
            or body.get("chat_id")
            or body.get("id")
            or "unknown"
        )
        title = meta.get("chat_title") or body.get("title") or "Chat"
        model = (
            (assistant_msg or {}).get("model")
            or body.get("model")
            or (body.get("models") or [None])[0]
        )
        turn = {
            "ts": time.time(),
            "chat_id": str(chat_id),
            "title": str(title),
            "model": model,
            "user": user_text,
            "assistant": asst_text,
            "user_email": ((__user__ or {}).get("email") if __user__ else None),
            "source": "filter_outlet",
        }

        root = Path(
            os.environ.get("CHAT_HISTORY_DIR")
            or self.valves.history_dir
            or "/data/chat_history"
        )
        root.mkdir(parents=True, exist_ok=True)
        (root / "chats").mkdir(parents=True, exist_ok=True)
        with (root / "turns.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(turn, ensure_ascii=False) + "\n")

        # Lightweight live index bump
        idx_path = root / "index.json"
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {}
        except Exception:
            idx = {}
        chats = list(idx.get("chats") or [])
        preview = " ".join(user_text.split())[:160]
        found = False
        for c in chats:
            if c.get("id") == str(chat_id):
                c["updated_at"] = turn["ts"]
                c["title"] = title
                c["preview"] = preview
                c["message_count"] = int(c.get("message_count") or 0) + 2
                found = True
                break
        if not found:
            chats.insert(
                0,
                {
                    "id": str(chat_id),
                    "title": title,
                    "created_at": turn["ts"],
                    "updated_at": turn["ts"],
                    "message_count": 2,
                    "preview": preview,
                    "open_url": f"https://synaptika-trade.duckdns.org:8443/c/{chat_id}",
                },
            )
        chats.sort(key=lambda x: float(x.get("updated_at") or 0), reverse=True)
        idx_path.write_text(
            json.dumps(
                {"ts": int(time.time()), "count": len(chats), "chats": chats},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def inlet(self, body: dict, __user__: dict | None = None) -> dict:
        block = self._live_block()
        messages = body.get("messages")
        if not isinstance(messages, list):
            return body

        marker = "## Estado live Ops (auto)"
        updated = False
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "system":
                continue
            content = msg.get("content")
            if isinstance(content, str) and marker in content:
                prefix = content.split(marker, 1)[0].rstrip()
                msg["content"] = f"{prefix}\n\n{block}".strip()
                updated = True
                break

        if not updated:
            messages.insert(0, {"role": "system", "content": block})
            body["messages"] = messages
        return body

    def outlet(
        self,
        body: dict,
        __user__: dict | None = None,
        __metadata__: dict | None = None,
    ) -> dict:
        try:
            self._save_turn(body, __user__, __metadata__)
        except Exception:
            pass
        return body
