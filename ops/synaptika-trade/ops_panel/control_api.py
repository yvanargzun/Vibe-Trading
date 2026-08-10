#!/usr/bin/env python3
"""POST /ops/api/control/* routes + OpenAPI write tools."""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, request

import control as opsctl


def register(
    app: Flask,
    *,
    vibe: Path,
    alpaca: Path,
    audit,
    api_auth_required,
) -> None:
    def body() -> dict:
        return request.get_json(silent=True) or {}

    @app.get("/ops/api/control")
    @api_auth_required
    def api_control_status():
        return jsonify(opsctl.control_status(vibe, alpaca))

    @app.post("/ops/api/control/halt")
    @api_auth_required
    def api_control_halt():
        b = body()
        res = opsctl.set_halt(
            vibe,
            alpaca,
            venue=str(b.get("venue") or "all"),
            halt=bool(b.get("halt", True)),
            reason=str(b.get("reason") or ""),
            confirm=True if "confirm" not in b else bool(b.get("confirm")),
        )
        audit("control_halt", json.dumps(res)[:400])
        return jsonify(res), (200 if res.get("ok") else 400)

    @app.post("/ops/api/control/mode")
    @api_auth_required
    def api_control_mode():
        b = body()
        res = opsctl.set_mode(
            vibe,
            alpaca,
            venue=str(b.get("venue") or ""),
            mode=str(b.get("mode") or ""),
            locked=bool(b.get("locked", True)),
            reason=str(b.get("reason") or ""),
            confirm=True if "confirm" not in b else bool(b.get("confirm")),
        )
        audit("control_mode", json.dumps(res)[:400])
        return jsonify(res), (200 if res.get("ok") else 400)

    @app.post("/ops/api/control/unlock")
    @api_auth_required
    def api_control_unlock():
        b = body()
        res = opsctl.clear_mode_lock(
            vibe,
            alpaca,
            venue=str(b.get("venue") or ""),
            confirm=True if "confirm" not in b else bool(b.get("confirm")),
        )
        audit("control_unlock", json.dumps(res)[:400])
        return jsonify(res), (200 if res.get("ok") else 400)

    @app.post("/ops/api/control/notify")
    @api_auth_required
    def api_control_notify():
        b = body()
        res = opsctl.set_notify_filter(
            vibe=vibe,
            mode=str(b.get("mode") or ""),
            confirm=True if "confirm" not in b else bool(b.get("confirm")),
        )
        audit("control_notify", json.dumps(res)[:400])
        return jsonify(res), (200 if res.get("ok") else 400)

    @app.post("/ops/api/control/knobs")
    @api_auth_required
    def api_control_knobs():
        b = body()
        res = opsctl.set_knobs(
            vibe,
            alpaca,
            venue=str(b.get("venue") or ""),
            knobs=dict(b.get("knobs") or {}),
            confirm=True if "confirm" not in b else bool(b.get("confirm")),
        )
        audit("control_knobs", json.dumps(res)[:400])
        return jsonify(res), (200 if res.get("ok") else 400)

    @app.post("/ops/api/control/intent")
    @api_auth_required
    def api_control_intent():
        b = body()
        res = opsctl.enqueue_intent(
            vibe,
            alpaca,
            venue=str(b.get("venue") or ""),
            action=str(b.get("action") or ""),
            symbol=b.get("symbol"),
            usd=b.get("usd"),
            confirm=True if "confirm" not in b else bool(b.get("confirm")),
            reason=str(b.get("reason") or ""),
        )
        audit("control_intent", json.dumps(res)[:400])
        return jsonify(res), (200 if res.get("ok") else 400)


def openapi_write_paths() -> dict:
    confirm_note = (
        "WRITE tool. Ejecuta de inmediato cuando el usuario lo pida. "
        "No preguntes ni esperes OK extra: llama esta tool en el mismo turno. "
        "Aplica a Binance live Spot y Alpaca paper."
    )
    write_body = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {"type": "object", "additionalProperties": True}
            }
        },
    }
    return {
        "/ops/api/control": {
            "get": {
                "operationId": "get_control_status",
                "summary": "Estado de control (mode lock, halt, knobs, intents)",
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/control/halt": {
            "post": {
                "operationId": "set_bot_halt",
                "summary": "HALT o resume Binance/Alpaca/all",
                "description": confirm_note,
                "requestBody": write_body,
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/control/mode": {
            "post": {
                "operationId": "set_strategy_mode",
                "summary": "Forzar modo (sticky) Binance o Alpaca paper",
                "description": confirm_note
                + " Binance: recap|standby|defensive|v6_primary."
                + " Alpaca: canonical_v2|smart_time|scalp|swing|…",
                "requestBody": write_body,
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/control/unlock": {
            "post": {
                "operationId": "unlock_strategy_mode",
                "summary": "Quitar lock de modo",
                "description": confirm_note,
                "requestBody": write_body,
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/control/notify": {
            "post": {
                "operationId": "set_notify_filter",
                "summary": "Filtro Telegram vibe|scalp15|fb|all",
                "description": confirm_note,
                "requestBody": write_body,
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/control/knobs": {
            "post": {
                "operationId": "set_strategy_knobs",
                "summary": "Overlay knobs runtime (Binance o Alpaca)",
                "description": confirm_note,
                "requestBody": write_body,
                "responses": {"200": {"description": "OK"}},
            }
        },
        "/ops/api/control/intent": {
            "post": {
                "operationId": "enqueue_trade_intent",
                "summary": "Encolar buy/sell/close Binance o Alpaca paper",
                "description": confirm_note,
                "requestBody": write_body,
                "responses": {"200": {"description": "OK"}},
            }
        },
    }
