#!/bin/bash
set -u
echo "=== host ==="
hostname; date -u; uptime
echo
free -h
echo
df -h / | tail -1
echo
echo "=== docker running ==="
docker ps --format 'table {{.Names}}\t{{.Status}}'
echo
echo "=== webui standby ==="
docker ps -a --filter name=webui --format '{{.Names}}  {{.Status}}'
echo
echo "=== health ==="
curl -sS -m 3 http://127.0.0.1:8642/health; echo
curl -sS -m 3 http://127.0.0.1:20128/api/monitoring/health; echo
curl -sS -m 5 https://synaptika-messenger.duckdns.org/health; echo
curl -sS -m 5 -o /dev/null -w 'demos HTTP %{http_code}\n' https://synaptika-demos.duckdns.org/
echo
echo "=== systemd ==="
for u in vibe-telegram-control vibe-trading vibe-autotrade docker; do
  echo "$u: $(systemctl is-active $u.service 2>/dev/null)"
done
echo
echo "=== docker mem ==="
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}'
