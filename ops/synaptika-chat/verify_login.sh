#!/bin/bash
set -e
for i in 1 2 3 4 5 6 7 8 9 10 12 14; do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/ || true)
  echo "wait i=$i code=$code"
  [ "$code" = "200" ] && break
  sleep 5
done
echo "NO_BASIC=$(curl -s -o /dev/null -w '%{http_code}' https://synaptika-chat.duckdns.org/)"
rm -f /tmp/j.jar
curl -s -c /tmp/j.jar -H 'Content-Type: application/json' \
  -d '{"email":"admin@localhost","password":"m59466Fr"}' \
  https://synaptika-chat.duckdns.org/api/v1/auths/signin | head -c 220
echo
echo "models=$(curl -s -o /dev/null -w '%{http_code}' -b /tmp/j.jar https://synaptika-chat.duckdns.org/api/models)"
curl -s https://synaptika-chat.duckdns.org/api/config
echo
docker exec synaptika-chat-webui printenv WEBUI_AUTH ENABLE_SIGNUP ENABLE_LOGIN_FORM
