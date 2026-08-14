#!/bin/bash
# Point Hermes Agent at OmniRoute (same gateway as Synaptika Chat) + lighten RAM knobs.
set -euo pipefail

CFG=/root/.hermes/config.yaml
SVC=/root/.config/systemd/user/hermes-gateway.service
BACKUP_DIR=/root/hermes-omniroute-backup
mkdir -p "$BACKUP_DIR"
cp -a "$CFG" "$BACKUP_DIR/config.yaml.$(date +%Y%m%d%H%M%S)"

python3 <<'PY'
from pathlib import Path
import yaml

p = Path("/root/.hermes/config.yaml")
data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

# Primary model → OmniRoute OpenAI-compatible endpoint
model = data.get("model") if isinstance(data.get("model"), dict) else {}
model.update(
    {
        "default": "auto/chat",
        "provider": "custom",
        "base_url": "http://127.0.0.1:20128/v1",
        "api_key": "omniroute",
        "max_tokens": 2048,
    }
)
# Prefer aliases that reuse OmniRoute combos
aliases = model.get("aliases") if isinstance(model.get("aliases"), dict) else {}
aliases.update(
    {
        "chat": "custom/auto/chat",
        "fast": "custom/auto/fast",
        "free": "custom/auto/best-free",
    }
)
model["aliases"] = aliases
data["model"] = model

# Fallbacks also through OmniRoute (no direct OpenRouter round-trips)
data["fallback_providers"] = [
    {
        "provider": "custom",
        "model": "auto/fast",
        "base_url": "http://127.0.0.1:20128/v1",
        "api_key": "omniroute",
    },
    {
        "provider": "custom",
        "model": "auto/best-free",
        "base_url": "http://127.0.0.1:20128/v1",
        "api_key": "omniroute",
    },
]

# Memory / speed knobs (service stays on)
term = data.get("terminal") if isinstance(data.get("terminal"), dict) else {}
# Was 5120 MB — absurd on a 3.7GB VPS; keep docker terminal usable but capped
term["container_memory"] = 768
term["container_disk"] = 4096
term["container_cpu"] = 1
term["lifetime_seconds"] = 900
data["terminal"] = term

agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
agent["max_turns"] = 40
agent["verbose"] = False
agent["reasoning_effort"] = "low"
data["agent"] = agent

aux = data.get("auxiliary") if isinstance(data.get("auxiliary"), dict) else {}
aux["free_only"] = True
data["auxiliary"] = aux

# Compression already on — tighten a bit for speed
comp = data.get("compression") if isinstance(data.get("compression"), dict) else {}
comp["enabled"] = True
comp["threshold"] = 0.45
comp["target_ratio"] = 0.2
data["compression"] = comp

# Streaming off already; keep off to reduce gateway churn
stream = data.get("streaming") if isinstance(data.get("streaming"), dict) else {}
stream["enabled"] = False
data["streaming"] = stream

p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
print("config updated")
print("model=", data["model"])
print("terminal.container_memory=", data["terminal"].get("container_memory"))
print("agent.max_turns=", data["agent"].get("max_turns"))
PY

# Inject OmniRoute env into user systemd unit (idempotent)
if [ -f "$SVC" ]; then
  cp -a "$SVC" "$BACKUP_DIR/hermes-gateway.service.bak"
  # Remove prior OmniRoute env lines then add
  sed -i '/OPENAI_BASE_URL=/d;/OPENAI_API_KEY=/d;/OMNIROUTE_/d' "$SVC"
  # Insert after HERMES_HOME env if present, else after [Service]
  if grep -q 'Environment="HERMES_HOME=' "$SVC"; then
    sed -i '/Environment="HERMES_HOME=/a Environment="OPENAI_BASE_URL=http://127.0.0.1:20128/v1"\nEnvironment="OPENAI_API_KEY=omniroute"' "$SVC"
  else
    sed -i '/\[Service\]/a Environment="OPENAI_BASE_URL=http://127.0.0.1:20128/v1"\nEnvironment="OPENAI_API_KEY=omniroute"' "$SVC"
  fi
  echo "systemd unit patched"
fi

# Ensure OmniRoute is up on localhost
curl -sf -o /dev/null -w 'omni_health=%{http_code}\n' http://127.0.0.1:20128/api/monitoring/health || {
  echo "OmniRoute not healthy on :20128 — starting chat stack"
  cd /root/synaptika-chat && docker compose --env-file secrets.env up -d omniroute-redis omniroute
  sleep 5
}

# Restart hermes user service
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/0}"
systemctl --user daemon-reload
systemctl --user restart hermes-gateway.service
sleep 3
systemctl --user is-active hermes-gateway.service
systemctl --user show hermes-gateway.service -p Environment --no-pager | tr ' ' '\n' | grep -E 'OPENAI_|HERMES_HOME' | sed -E 's/(KEY)=.*/\1=***/'

# Quick models probe via OmniRoute as Hermes would
curl -s -m 20 http://127.0.0.1:20128/v1/models | python3 -c 'import sys,json;d=json.load(sys.stdin);ids=[m["id"] for m in d.get("data") or []];print("omni_models",len(ids),"has_auto_chat", "auto/chat" in ids)'

echo "=== hermes model section ==="
python3 -c 'import yaml;print(yaml.safe_load(open("/root/.hermes/config.yaml"))["model"])'

echo "=== mem ==="
free -h | head -2
ps -eo rss,cmd --sort=-rss | grep -E 'hermes_cli|omniroute|open_webui|freqtrade' | grep -v grep | awk '{printf "%.0fMB %s\n",$1/1024,substr($0,index($0,$2))}'
