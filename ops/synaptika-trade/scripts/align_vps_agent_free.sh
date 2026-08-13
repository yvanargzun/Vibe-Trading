#!/bin/bash
set -eu
AE=/root/.vibe-trading/agent.env
if [ ! -f "$AE" ]; then
  echo "missing $AE"
  exit 0
fi
sed -i 's|^LANGCHAIN_MODEL_NAME=.*|LANGCHAIN_MODEL_NAME=google/gemma-4-31b-it:free|' "$AE"
for kv in \
  'SWARM_VRAM_MODE=balanced' \
  'SWARM_LEAD_MODEL=google/gemma-4-31b-it:free' \
  'SWARM_WORKER_MODEL=inclusionai/ling-3.0-flash:free' \
  'SWARM_FREE_TIER=auto' \
  'SWARM_FREE_MAX_ITER=20' \
  'SWARM_GROUNDING_CACHE_TTL_S=300'
do
  key="${kv%%=*}"
  if grep -q "^${key}=" "$AE"; then
    sed -i "s|^${key}=.*|${kv}|" "$AE"
  else
    echo "$kv" >> "$AE"
  fi
done
echo "updated agent.env free-tier lines:"
grep -E '^LANGCHAIN_MODEL_NAME=|^SWARM_' "$AE" | sed 's/=.*/=***/'
