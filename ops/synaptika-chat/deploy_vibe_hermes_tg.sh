#!/bin/bash
# Remove ALL freqtrade; restore Vibe Binance + Telegram; deploy new keyboard (Hermes).
set -euo pipefail
ARCHIVE=/root/disabled-systemd-units
VIBE=/root/.vibe-trading

echo "=== 1) Kill freqtrade completely ==="
systemctl stop freqtrade-binance-futures.service 2>/dev/null || true
systemctl stop ft-research-all.service 2>/dev/null || true
systemctl stop ft-research-all.timer 2>/dev/null || true
systemctl disable freqtrade-binance-futures.service 2>/dev/null || true
systemctl disable ft-research-all.timer 2>/dev/null || true

for u in freqtrade-binance-futures.service freqtrade-alpaca-paper.service \
         freqtrade-alpaca-stocks.service freqtrade-telegram.service \
         ft-research-all.service; do
  systemctl stop "$u" 2>/dev/null || true
  systemctl disable "$u" 2>/dev/null || true
  if [ -f "/etc/systemd/system/$u" ] && [ ! -L "/etc/systemd/system/$u" ]; then
    mkdir -p "$ARCHIVE"
    mv "/etc/systemd/system/$u" "$ARCHIVE/$u" 2>/dev/null || true
  fi
  ln -sfn /dev/null "/etc/systemd/system/$u"
  echo "masked $u"
done
if [ -f /etc/systemd/system/ft-research-all.timer ]; then
  systemctl stop ft-research-all.timer 2>/dev/null || true
  systemctl disable ft-research-all.timer 2>/dev/null || true
  mv /etc/systemd/system/ft-research-all.timer "$ARCHIVE/" 2>/dev/null || true
  ln -sfn /dev/null /etc/systemd/system/ft-research-all.timer
fi
systemctl daemon-reload

pkill -9 -f 'freqtrade trade' 2>/dev/null || true
pkill -9 -f 'ft_telegram_loop' 2>/dev/null || true
pkill -9 -f 'config_binance_futures' 2>/dev/null || true
sleep 1
pgrep -af freqtrade || echo "freqtrade gone"

echo "=== 2) Restore Vibe Binance + Telegram units from archive ==="
RESTORE=(
  vibe-autotrade.service
  vibe-telegram-control.service
  vibe-telegram.service
  vibe-trading.service
)
for u in "${RESTORE[@]}"; do
  rm -f "/etc/systemd/system/$u"
  if [ -f "$ARCHIVE/$u" ]; then
    cp "$ARCHIVE/$u" "/etc/systemd/system/$u"
    echo "restored $u"
  else
    echo "MISSING archive for $u"
  fi
done
systemctl daemon-reload

echo "=== 3) Deploy updated Telegram UI files ==="
cp -a /tmp/tg_deploy/telegram_notify_prefs.py "$VIBE/"
cp -a /tmp/tg_deploy/telegram_control_bot.py "$VIBE/"
rm -f "$VIBE/telegram_ui_cleared.flag"

echo "=== 4) OpenBB + Hermes SOUL for Vibe research ==="
pgrep -af openbb-mcp || echo "WARN: openbb-mcp not running"
curl -s -o /dev/null -w "openbb_mcp=%{http_code}\n" --connect-timeout 3 http://127.0.0.1:8100/mcp || true

if ! grep -q 'Telegram polling owned by vibe-telegram-control' /root/.hermes/.env 2>/dev/null; then
  echo "# Telegram polling owned by vibe-telegram-control; Hermes research via API_SERVER :8642" >> /root/.hermes/.env
fi

if ! grep -q 'OpenBB→Vibe' /root/.hermes/SOUL.md 2>/dev/null; then
  cat >> /root/.hermes/SOUL.md <<'EOF'

## OpenBB→Vibe (research)
Cuando te pidan research de mercado desde Telegram (vía API):
- Usa OpenBB MCP (crypto/equity/news) para datos reales.
- Entrega sesgo, pares Binance relevantes, niveles y riesgos.
- Guarda hallazgos útiles en `/root/.vibe-trading/hermes_research_latest.json` si puedes escribir archivos.
- Objetivo: informar decisiones de Vibe Autotrade (Binance), no freqtrade.
EOF
fi

cat > "$VIBE/OPENBB_VIBE.md" <<'EOF'
# OpenBB → Vibe research

- OpenBB MCP: http://127.0.0.1:8100/mcp (Hermes mcp_servers.openbb)
- Hermes LLM: OmniRoute http://127.0.0.1:20128/v1 (auto/chat)
- Telegram: botones en vibe-telegram-control; Modo Hermes reenvía preguntas a Hermes API :8642
- Última research: /root/.vibe-trading/hermes_research_latest.json
- Freqtrade: eliminado
EOF

echo "=== 5) Start services ==="
systemctl enable --now vibe-autotrade.service
systemctl enable --now vibe-telegram-control.service
systemctl enable --now vibe-telegram.service
systemctl enable --now vibe-trading.service || true
sleep 4

echo "=== 6) Force-send new keyboard ==="
cd "$VIBE"
/opt/vibe-trade/.venv/bin/python - <<'PY' || true
from telegram_notify_prefs import load_env, tg_api, filter_keyboard, mode_label, load_prefs
env = load_env()
chat = env.get("TELEGRAM_CHAT_ID")
if not chat:
    print("NO_CHAT_ID")
else:
    tg_api("deleteWebhook", {"drop_pending_updates": False})
    tg_api("deleteMyCommands", {})
    tg_api("setMyCommands", {"commands": [
        {"command": "estado", "description": "Panorama Vibe + Hermes"},
        {"command": "binance", "description": "Estado Binance / Vibe"},
        {"command": "hermes", "description": "Hermes research + OpenBB"},
        {"command": "filtro", "description": "Filtro de avisos"},
        {"command": "ayuda", "description": "Ayuda"},
    ]})
    text = (
        "Teclado actualizado.\n"
        f"Filtro actual: {mode_label(load_prefs().get('mode','all'))}\n\n"
        "Botones:\n"
        "• Solo Binance / Vibe\n"
        "• Modo Hermes (research) ← OpenBB + OmniRoute\n"
        "• Solo clientes FB\n"
        "• Todos (Vibe + FB + Hermes)\n\n"
        "Freqtrade eliminado. En Modo Hermes escribe tu pregunta de mercado."
    )
    r = tg_api("sendMessage", {
        "chat_id": chat,
        "text": text,
        "reply_markup": filter_keyboard(),
        "disable_web_page_preview": True,
    })
    print("send", r.get("ok"), r.get("description"))
PY

echo "=== STATUS ==="
systemctl is-active vibe-autotrade.service vibe-telegram-control.service vibe-telegram.service || true
systemctl is-active vibe-trading.service 2>&1 || true
systemctl is-active freqtrade-binance-futures.service 2>&1 || true
pgrep -af 'freqtrade|vibe_autotrade|telegram_control|telegram_monitor|openbb-mcp|hermes_cli' | grep -v grep || true
free -h | head -2
