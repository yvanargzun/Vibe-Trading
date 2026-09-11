#!/bin/bash
set -euo pipefail
VIBE=/root/.vibe-trading
cp -a /tmp/tg_buttons/telegram_notify_prefs.py "$VIBE/"
cp -a /tmp/tg_buttons/telegram_control_bot.py "$VIBE/"
cd "$VIBE"
/opt/vibe-trade/.venv/bin/python <<'PY'
from telegram_notify_prefs import (
    set_chat_mode,
    CHAT_CONTROL,
    load_env,
    tg_api,
    filter_keyboard,
    mode_label,
    load_prefs,
    BTN_EXIT_CHAT,
    BTN_HERMES_AGENT,
    BTN_MESSENGER_SALES,
)
import time

set_chat_mode(CHAT_CONTROL)
env = load_env()
chat = env["TELEGRAM_CHAT_ID"]
tg_api(
    "sendMessage",
    {"chat_id": chat, "text": "…", "reply_markup": {"remove_keyboard": True}},
)
time.sleep(0.25)
text = (
    "Teclado corregido ✅\n\n"
    f"• {BTN_HERMES_AGENT}\n"
    f"• {BTN_MESSENGER_SALES}\n"
    f"• {BTN_EXIT_CHAT} ahora sí vuelve al menú\n\n"
    f"Filtro: {mode_label(load_prefs().get('mode', 'all'))}"
)
r = tg_api(
    "sendMessage",
    {
        "chat_id": chat,
        "text": text,
        "reply_markup": filter_keyboard(),
        "disable_web_page_preview": True,
    },
)
print("send", r.get("ok"), r.get("description"))
PY
systemctl restart vibe-telegram-control.service
sleep 3
systemctl is-active vibe-telegram-control.service
journalctl -u vibe-telegram-control.service -n 15 --no-pager
