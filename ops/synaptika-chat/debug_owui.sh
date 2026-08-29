#!/bin/bash
set -e
echo '===ENV==='
docker exec synaptika-chat-webui sh -c 'printenv | sort | grep -E "AUTH|LOGIN|SIGNUP|WEBSOCKET|WEBUI_|CORS"'
echo '===SIGNIN==='
rm -f /tmp/owui.jar
curl -s -c /tmp/owui.jar -b /tmp/owui.jar -u 'admin:m59466Fr' \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":""}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | head -c 800
echo
echo '===MODELS==='
curl -s -c /tmp/owui.jar -b /tmp/owui.jar -u 'admin:m59466Fr' \
  https://synaptika-chat.duckdns.org/api/models | head -c 800
echo
echo '===WS==='
curl -s -o /tmp/ws.out -w 'ws=%{http_code}\n' -c /tmp/owui.jar -b /tmp/owui.jar -u 'admin:m59466Fr' \
  'https://synaptika-chat.duckdns.org/ws/socket.io/?EIO=4&transport=polling'
head -c 300 /tmp/ws.out; echo
echo '===RECENT LOGS==='
docker logs synaptika-chat-webui --tail 25 2>&1
