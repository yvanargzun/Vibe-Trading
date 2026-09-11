#!/bin/bash
set -euo pipefail
cp -a /tmp/tg_buttons/telegram_control_bot.py /root/.vibe-trading/
cd /root/.vibe-trading
/opt/vibe-trade/.venv/bin/python -m py_compile telegram_control_bot.py
echo "syntax ok"

# Probe auto/fast latency
/opt/vibe-trade/.venv/bin/python <<'PY'
import json, time, urllib.request
from pathlib import Path
key=""
for line in Path("/root/.hermes/.env").read_text().splitlines():
    if line.startswith("API_SERVER_KEY="):
        key=line.split("=",1)[1].strip(); break
for model in ("auto/fast", "auto/chat"):
    payload=json.dumps({"model":model,"messages":[{"role":"user","content":"di solo: ok"}],"max_tokens":8}).encode()
    req=urllib.request.Request(
        "http://127.0.0.1:8642/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type":"application/json",
            "Authorization":f"Bearer {key}",
            "X-Hermes-Session-Id":"synaptika-tg-probe-"+model.replace("/","-"),
        },
        method="POST",
    )
    t0=time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data=json.loads(r.read().decode())
        msg=((data.get("choices") or [{}])[0].get("message") or {})
        print(f"{model}: {time.time()-t0:.1f}s -> {repr((msg.get('content') or '')[:40])} model_out={data.get('model')}")
    except Exception as e:
        print(f"{model}: FAIL {time.time()-t0:.1f}s {e}")
PY

systemctl restart vibe-telegram-control.service
sleep 2
systemctl is-active vibe-telegram-control.service

# Reset to main menu + notify user
/opt/vibe-trade/.venv/bin/python <<'PY'
from telegram_notify_prefs import (
    set_chat_mode, CHAT_CONTROL, load_env, tg_api, filter_keyboard, mode_label, load_prefs,
    BTN_HERMES_AGENT, BTN_MESSENGER_SALES, BTN_EXIT_CHAT, BTN_VIBE, BTN_FB, BTN_ALL,
)
import time
set_chat_mode(CHAT_CONTROL)
chat = load_env()["TELEGRAM_CHAT_ID"]
tg_api("sendMessage", {"chat_id": chat, "text": "…", "reply_markup": {"remove_keyboard": True}})
time.sleep(0.2)
text = (
    "Hermes más rápido ✅\n\n"
    "• Feedback instantáneo «Pensando…»\n"
    "• Modelo OmniRoute fast + warm-up al entrar\n"
    "• Salir / Sales / filtros sin bloquearse\n\n"
    f"Prueba: {BTN_HERMES_AGENT} → escribe algo → debe aparecer Pensando al toque."
)
r = tg_api("sendMessage", {"chat_id": chat, "text": text, "reply_markup": filter_keyboard()})
print("notify", r.get("ok"))
PY
journalctl -u vibe-telegram-control.service -n 8 --no-pager
