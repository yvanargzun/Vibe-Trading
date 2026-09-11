#!/bin/bash
set -euo pipefail
cp -a /tmp/tg_buttons/telegram_notify_prefs.py /root/.vibe-trading/
cp -a /tmp/tg_buttons/telegram_control_bot.py /root/.vibe-trading/
cd /root/.vibe-trading
/opt/vibe-trade/.venv/bin/python -m py_compile telegram_notify_prefs.py telegram_control_bot.py
systemctl restart vibe-telegram-control.service
sleep 2
systemctl is-active vibe-telegram-control.service
# force keyboard refresh
/opt/vibe-trade/.venv/bin/python <<'PY'
from telegram_notify_prefs import (
    set_chat_mode, CHAT_CONTROL, load_env, tg_api, filter_keyboard,
    BTN_HERMES_AGENT, BTN_MESSENGER_SALES, BTN_FB, BTN_ALL,
)
import time
set_chat_mode(CHAT_CONTROL)
chat = load_env()["TELEGRAM_CHAT_ID"]
tg_api("sendMessage", {"chat_id": chat, "text": "…", "reply_markup": {"remove_keyboard": True}})
time.sleep(0.25)
text = (
    "Teclado actualizado ✅\n\n"
    "Trading quitado del menú.\n\n"
    f"• {BTN_HERMES_AGENT}\n"
    f"• {BTN_MESSENGER_SALES}\n"
    f"• {BTN_FB}\n"
    f"• {BTN_ALL}"
)
r = tg_api("sendMessage", {"chat_id": chat, "text": text, "reply_markup": filter_keyboard()})
print("send", r.get("ok"))
# refresh bot commands
tg_api("deleteMyCommands", {})
tg_api("setMyCommands", {"commands": [
    {"command": "hermes", "description": "Entrar a Hermes Agent"},
    {"command": "sales", "description": "Messenger Sales (página FB)"},
    {"command": "menu", "description": "Salir al menú principal"},
    {"command": "filtro", "description": "Ver/cambiar filtro de avisos"},
    {"command": "ayuda", "description": "Lista de comandos"},
]})
print("commands refreshed")
PY
journalctl -u vibe-telegram-control.service -n 6 --no-pager
