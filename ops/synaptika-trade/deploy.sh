#!/bin/bash
# Deploy Synaptika Trade portal (Ops + Open WebUI + Caddy) on Hetzner
# Normalize CRLF if this script was copied from Windows.
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  if grep -q $'\r' "${BASH_SOURCE[0]}" 2>/dev/null; then
    tmp="$(mktemp)"
    tr -d '\r' < "${BASH_SOURCE[0]}" > "$tmp"
    exec bash "$tmp" "$@"
  fi
fi
set -eu
DEST=/root/synaptika-trade
mkdir -p "$DEST/brand" "$DEST/ops_panel/static" "$DEST/chat_history"
# Strip CRLF from any scripts copied from Windows
if [ -d /tmp/synaptika-trade ]; then
  find /tmp/synaptika-trade -type f \( -name '*.sh' -o -name 'deploy.sh' \) -exec sed -i 's/\r$//' {} + 2>/dev/null || true
fi
touch "$DEST/ops_audit.jsonl"
touch "$DEST/chat_history/.gitkeep"
chmod 644 "$DEST/ops_audit.jsonl"
chmod 777 "$DEST/chat_history" || true

# Install Docker if missing
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y docker-compose-plugin
fi

# Copy stack from /tmp/synaptika-trade if present
if [ -d /tmp/synaptika-trade ]; then
  # Strip CRLF from shell scripts before overwrite (Windows scp)
  find /tmp/synaptika-trade -type f -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
  cp -a /tmp/synaptika-trade/. "$DEST/"
  find "$DEST" -type f -name '*.sh' -exec sed -i 's/\r$//' {} + 2>/dev/null || true
fi

# Secrets (do not overwrite existing)
if [ ! -f "$DEST/secrets.env" ]; then
  cat > "$DEST/secrets.env" <<'EOF'
DUCKDNS_DOMAIN=synaptika-trade
DUCKDNS_TOKEN=REPLACE_ME
DUCKDNS_IP=46.225.50.87
LETSENCRYPT_EMAIL=yvan@synaptika.local
OPS_PASSWORD=SynaptikaTrade2026!
OPS_API_KEY=REPLACE_WITH_RANDOM_HEX
OPENAI_API_KEY=
OPENAI_API_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OLLAMA_API_KEY=
OLLAMA_CLOUD_BASE_URL=https://ollama.com/v1
OLLAMA_DEFAULT_MODEL=deepseek-v4-flash
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.0-flash
LLM_PROXY_MODEL=synaptika-auto
ALLOW_PAID_OPENROUTER=0
OPENROUTER_FAILOVER_MODELS=google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free,nvidia/nemotron-3-nano-30b-a3b:free,nvidia/nemotron-3-super-120b-a12b:free,openai/gpt-oss-20b:free,inclusionai/ling-3.0-flash:free,google/gemma-3-27b-it:free,meta-llama/llama-3.3-70b-instruct:free,qwen/qwen-2.5-72b-instruct:free
EOF
  chmod 600 "$DEST/secrets.env"
fi

# Ensure OpenRouter / failover keys exist on older secrets files
ensure_kv() {
  local key="$1" val="$2"
  if ! grep -q "^${key}=" "$DEST/secrets.env" 2>/dev/null; then
    echo "${key}=${val}" >> "$DEST/secrets.env"
  fi
}
ensure_kv OPENAI_API_BASE_URL "https://openrouter.ai/api/v1"
ensure_kv OPENROUTER_BASE_URL "https://openrouter.ai/api/v1"
ensure_kv OLLAMA_CLOUD_BASE_URL "https://ollama.com/v1"
ensure_kv OLLAMA_DEFAULT_MODEL "deepseek-v4-flash"
ensure_kv GEMINI_MODEL "gemini-2.0-flash"
ensure_kv LLM_PROXY_MODEL "synaptika-auto"
ensure_kv ALLOW_PAID_OPENROUTER "0"
ensure_kv OPENROUTER_FAILOVER_MODELS "google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free,nvidia/nemotron-3-nano-30b-a3b:free,nvidia/nemotron-3-super-120b-a12b:free,openai/gpt-oss-20b:free,inclusionai/ling-3.0-flash:free,google/gemma-3-27b-it:free,meta-llama/llama-3.3-70b-instruct:free,qwen/qwen-2.5-72b-instruct:free"
ensure_kv COPILOT_MAX_TOKENS "3072"
# Prefer real OpenRouter base if older template pointed at api.openai.com
if grep -q '^OPENAI_API_BASE_URL=https://api.openai.com' "$DEST/secrets.env" 2>/dev/null; then
  sed -i 's#^OPENAI_API_BASE_URL=https://api.openai.com/v1#OPENAI_API_BASE_URL=https://openrouter.ai/api/v1#' "$DEST/secrets.env"
fi
# Strip legacy paid OpenRouter failover IDs unless operator opted in
if ! grep -qE '^ALLOW_PAID_OPENROUTER=(1|true|yes|on)$' "$DEST/secrets.env" 2>/dev/null; then
  if grep -qE 'openai/gpt-4o-mini|claude-3.5-haiku|gemini-2.0-flash-001' "$DEST/secrets.env" 2>/dev/null; then
    sed -i 's/^OPENROUTER_FAILOVER_MODELS=.*/OPENROUTER_FAILOVER_MODELS=google\/gemma-4-31b-it:free,google\/gemma-4-26b-a4b-it:free,nvidia\/nemotron-3-nano-30b-a3b:free,nvidia\/nemotron-3-super-120b-a12b:free,openai\/gpt-oss-20b:free,inclusionai\/ling-3.0-flash:free,google\/gemma-3-27b-it:free,meta-llama\/llama-3.3-70b-instruct:free,qwen\/qwen-2.5-72b-instruct:free/' "$DEST/secrets.env" || true
  fi
fi
# Import GEMINI / OPENROUTER from trading env if portal secrets are empty
for AGENT_ENV in /root/.vibe-trading/agent.env /root/.vibe-trading/.env; do
  if [ -f "$AGENT_ENV" ]; then
    for key in GEMINI_API_KEY GEMINI_MODEL OPENROUTER_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
      cur=$(grep -E "^${key}=" "$DEST/secrets.env" 2>/dev/null | head -1 | cut -d= -f2- || true)
      src=$(grep -E "^${key}=" "$AGENT_ENV" 2>/dev/null | head -1 | cut -d= -f2- || true)
      if [ -z "$cur" ] && [ -n "$src" ]; then
        if grep -q "^${key}=" "$DEST/secrets.env" 2>/dev/null; then
          sed -i "s|^${key}=.*|${key}=${src}|" "$DEST/secrets.env"
        else
          echo "${key}=${src}" >> "$DEST/secrets.env"
        fi
      fi
    done
  fi
done
# Mirror OPENAI_API_KEY into OPENROUTER_API_KEY when missing
or_cur=$(grep -E '^OPENROUTER_API_KEY=' "$DEST/secrets.env" 2>/dev/null | head -1 | cut -d= -f2- || true)
oa_cur=$(grep -E '^OPENAI_API_KEY=' "$DEST/secrets.env" 2>/dev/null | head -1 | cut -d= -f2- || true)
if [ -z "$or_cur" ] && [ -n "$oa_cur" ]; then
  if grep -q '^OPENROUTER_API_KEY=' "$DEST/secrets.env" 2>/dev/null; then
    sed -i "s|^OPENROUTER_API_KEY=.*|OPENROUTER_API_KEY=${oa_cur}|" "$DEST/secrets.env"
  else
    echo "OPENROUTER_API_KEY=${oa_cur}" >> "$DEST/secrets.env"
  fi
fi

# Ensure OPS_API_KEY exists on older secrets files
if ! grep -q '^OPS_API_KEY=' "$DEST/secrets.env" 2>/dev/null; then
  echo "OPS_API_KEY=$(openssl rand -hex 24)" >> "$DEST/secrets.env"
fi
if grep -q '^OPS_API_KEY=REPLACE_WITH_RANDOM_HEX$' "$DEST/secrets.env" 2>/dev/null; then
  sed -i "s/^OPS_API_KEY=REPLACE_WITH_RANDOM_HEX$/OPS_API_KEY=$(openssl rand -hex 24)/" "$DEST/secrets.env"
fi

# LF fix scripts
sed -i 's/\r$//' "$DEST/duckdns-update.sh" "$DEST/deploy.sh" 2>/dev/null || true
chmod +x "$DEST/duckdns-update.sh" "$DEST/deploy.sh" 2>/dev/null || true

# systemd duckdns timer
cat >/etc/systemd/system/duckdns-synaptika.service <<'EOF'
[Unit]
Description=Update DuckDNS synaptika-trade
After=network-online.target
[Service]
Type=oneshot
EnvironmentFile=/root/synaptika-trade/secrets.env
ExecStart=/root/synaptika-trade/duckdns-update.sh
EOF
cat >/etc/systemd/system/duckdns-synaptika.timer <<'EOF'
[Unit]
Description=Refresh DuckDNS every 5 minutes
[Timer]
OnBootSec=30
OnUnitActiveSec=5min
Unit=duckdns-synaptika.service
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now duckdns-synaptika.timer || true

# Export compose env
set -a
# shellcheck disable=SC1091
source "$DEST/secrets.env"
set +a
export LETSENCRYPT_EMAIL OPS_PASSWORD OPS_API_KEY OPENAI_API_KEY OPENAI_API_BASE_URL
export OLLAMA_API_KEY OLLAMA_CLOUD_BASE_URL OLLAMA_DEFAULT_MODEL
export OPENROUTER_API_KEY OPENROUTER_BASE_URL GEMINI_API_KEY GEMINI_MODEL LLM_PROXY_MODEL
export OPENROUTER_FAILOVER_MODELS ALLOW_PAID_OPENROUTER
export COPILOT_MAX_TOKENS TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID

cd "$DEST"
ln -sfn secrets.env .env
docker compose --env-file secrets.env pull || true
docker compose --env-file secrets.env up -d --build

sleep 8
# Force Open WebUI DB: free models + Ollama Cloud + copiloto
docker compose --env-file secrets.env exec -T \
  -e OPENAI_UPSTREAM_API_KEY="${OPENAI_API_KEY:-}" \
  -e OPENAI_UPSTREAM_BASE_URL="${OPENAI_API_BASE_URL:-https://openrouter.ai/api/v1}" \
  -e OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-${OPENAI_API_KEY:-}}" \
  -e OPENROUTER_BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}" \
  -e OLLAMA_API_KEY="${OLLAMA_API_KEY:-}" \
  -e OLLAMA_CLOUD_BASE_URL="${OLLAMA_CLOUD_BASE_URL:-https://ollama.com/v1}" \
  -e LLM_PROXY_MODEL="${LLM_PROXY_MODEL:-synaptika-auto}" \
  -e COPILOT_MAX_TOKENS="${COPILOT_MAX_TOKENS:-3072}" \
  open-webui python3 /srv/webui/apply_free_models.py || true
docker compose --env-file secrets.env exec -T \
  -e OPS_API_KEY="${OPS_API_KEY:-}" \
  -e OLLAMA_API_KEY="${OLLAMA_API_KEY:-}" \
  -e OLLAMA_CLOUD_BASE_URL="${OLLAMA_CLOUD_BASE_URL:-https://ollama.com/v1}" \
  -e OLLAMA_DEFAULT_MODEL="${OLLAMA_DEFAULT_MODEL:-deepseek-v4-flash}" \
  -e DEFAULT_MODELS="${LLM_PROXY_MODEL:-synaptika-auto}" \
  -e LLM_PROXY_MODEL="${LLM_PROXY_MODEL:-synaptika-auto}" \
  -e COPILOT_MAX_TOKENS="${COPILOT_MAX_TOKENS:-3072}" \
  -e CHAT_HISTORY_DIR=/data/chat_history \
  open-webui python3 /srv/webui/apply_copilot.py || true
docker compose --env-file secrets.env exec -T \
  -e CHAT_HISTORY_DIR=/data/chat_history \
  open-webui python3 /srv/webui/export_chat_history.py || true
# Restart so TOOL_SERVERS cache picks up OpenAPI write tools + model toolIds
docker compose --env-file secrets.env restart open-webui || true
sleep 5
# Smoke: OpenAPI must expose write ops for copiloto control
if curl -fsS --max-time 10 -H "X-Ops-Key: ${OPS_API_KEY}" http://127.0.0.1:8787/ops/api/openapi.json \
  | grep -q set_strategy_mode; then
  echo "OK: OpenAPI write tools (set_strategy_mode) present"
else
  echo "WARN: OpenAPI missing set_strategy_mode — check ops container mounts" >&2
fi

# Smoke: llm-proxy models + short failover chat
if curl -fsS --max-time 15 http://127.0.0.1:4000/v1/models | grep -q synaptika-auto; then
  echo "OK: llm-proxy /v1/models lists synaptika-auto"
else
  echo "WARN: llm-proxy /v1/models missing synaptika-auto" >&2
fi
smoke_chat=$(curl -fsS --max-time 90 http://127.0.0.1:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer synaptika-proxy' \
  -d '{"model":"synaptika-auto","messages":[{"role":"user","content":"ping"}],"max_tokens":8,"stream":false}' \
  2>/dev/null || true)
if echo "$smoke_chat" | grep -q '"choices"'; then
  via=$(echo "$smoke_chat" | python3 -c "import sys,json; d=json.load(sys.stdin); print((d.get('synaptika_proxy') or {}).get('via','ok'))" 2>/dev/null || echo ok)
  echo "OK: llm-proxy chat failover via=${via}"
else
  echo "WARN: llm-proxy chat smoke failed (check GEMINI/OLLAMA/OPENROUTER keys)" >&2
fi

# Reminder: rotate OpenRouter key if it was ever pasted into chat/logs
if [ -f "$DEST/ROTATE_OPENROUTER.md" ]; then
  echo "NOTE: see $DEST/ROTATE_OPENROUTER.md if the OpenRouter key may have leaked"
fi

docker compose ps
echo "==== health ===="
curl -fsS --max-time 5 http://127.0.0.1:8787/healthz || true
echo
echo "Portal: https://synaptika-trade.duckdns.org"
echo "Chat:   https://synaptika-trade.duckdns.org/ops/chat  (Ops login only)"
echo "Hist:   https://synaptika-trade.duckdns.org/ops/historial"
echo "Local:  http://127.0.0.1:8787"
