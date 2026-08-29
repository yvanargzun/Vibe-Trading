#!/bin/bash
set -e
docker cp /root/synaptika-chat/apply_stream_defaults.py synaptika-chat-webui:/tmp/apply_stream_defaults.py
# wait healthy
for i in 1 2 3 4 5 6 7 8 9 10 12 14; do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo wait=$i code=$code
  [ "$code" = "200" ] && break
  sleep 4
done
docker exec synaptika-chat-webui python /tmp/apply_stream_defaults.py
curl -fsS http://127.0.0.1:4001/healthz; echo
echo '=== proxy stream ==='
curl -s -N -m 45 -H 'Content-Type: application/json' \
  -d '{"model":"synaptika-chat-auto","stream":true,"messages":[{"role":"user","content":"Di solo: ok"}],"max_tokens":24}' \
  http://127.0.0.1:4001/v1/chat/completions | head -c 600
echo
docker logs synaptika-chat-llm-proxy --tail 20
