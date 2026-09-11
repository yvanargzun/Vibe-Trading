#!/bin/bash
set -u
echo "=== host ==="
hostname; date -u; uptime; echo
echo "=== memory/disk ==="
free -h | head -3
df -h / /var /root 2>/dev/null | awk 'NR==1 || /\/$|\/var|\/root/'
echo
echo "=== synaptika services ==="
for u in vibe-telegram-control synaptika-messenger synaptika-demos nginx docker; do
  systemctl is-active "$u.service" 2>/dev/null | awk -v n="$u" '{print n": "$0}'
done
# guess unit names
systemctl list-units --type=service --state=running --no-pager 2>/dev/null | grep -Ei 'synaptika|vibe|hermes|messenger|omni|nginx|docker|caddy' || true
echo
echo "=== listeners ==="
ss -lntp 2>/dev/null | grep -E ':80 |:443 |:8642|:20128|:8897|:4173|:3000|:5432' || true
echo
echo "=== hermes/omni ==="
curl -sS -m 3 http://127.0.0.1:8642/health 2>&1 | head -c 200; echo
curl -sS -m 3 http://127.0.0.1:20128/api/monitoring/health 2>&1 | head -c 200; echo
echo
echo "=== telegram control recent ==="
journalctl -u vibe-telegram-control.service -n 8 --no-pager 2>/dev/null || true
echo
echo "=== messenger health (public) ==="
curl -sS -m 5 https://synaptika-messenger.duckdns.org/health 2>&1 | head -c 400; echo
echo
echo "=== demos ==="
curl -sS -m 5 -o /dev/null -w "demos HTTP %{http_code}\n" https://synaptika-demos.duckdns.org/ 2>&1 || true
echo
echo "=== docker (top) ==="
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null | head -20 || true
echo
echo "=== load / failed units ==="
systemctl --failed --no-pager 2>/dev/null | head -20
ps -eo pid,pcpu,pmem,cmd --sort=-pcpu | head -8
