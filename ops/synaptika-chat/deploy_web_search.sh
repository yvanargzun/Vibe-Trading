#!/bin/bash
set -e
DEST=/root/synaptika-chat
cd "$DEST"

docker compose --env-file secrets.env up -d chat-webui
sleep 8
for i in 1 2 3 4 5 6 7 8 9 10 12; do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait=$i code=$code
  [ "$code" = "200" ] && break
  sleep 4
done

docker exec synaptika-chat-webui python /srv/enable_web_search.py
docker exec synaptika-chat-webui python /srv/apply_system_prompt.py

# Verify env
docker exec synaptika-chat-webui printenv ENABLE_WEB_SEARCH WEB_SEARCH_ENGINE BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL ENABLE_PERSISTENT_CONFIG

# Test DuckDuckGo from inside container
docker exec synaptika-chat-webui python - <<'PY'
try:
    from ddgs import DDGS
except Exception:
    try:
        from duckduckgo_search import DDGS
    except Exception as e:
        print('DDGS_IMPORT_FAIL', e)
        raise
q = 'international news today'
try:
    with DDGS() as d:
        rows = list(d.text(q, max_results=3))
    print('DDGS_OK', len(rows))
    for r in rows[:3]:
        print('-', (r.get('title') or '')[:80])
except Exception as e:
    print('DDGS_FAIL', type(e).__name__, e)
PY

# API config feature flags
curl -s https://synaptika-chat.duckdns.org/api/config | python3 -c 'import sys,json; d=json.load(sys.stdin); print({k:d.get("features",{}).get(k) for k in d.get("features",{}) if "search" in k.lower() or "web" in k.lower()}); print("keys", sorted(d.get("features",{}).keys()))'
