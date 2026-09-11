#!/bin/bash
# After backup: delete trading stack except OpenBB; free disk; start OpenBB MCP.
set -euo pipefail

echo "=== BEFORE df ==="
df -h /

echo "=== remove Open WebUI containers + images ==="
docker rm -f synaptika-chat-webui synaptika-trade-open-webui-1 2>/dev/null || true
docker rmi ghcr.io/open-webui/open-webui:main ghcr.io/open-webui/open-webui:v0.11.0 2>/dev/null || true

echo "=== docker prune build cache / unused ==="
docker builder prune -af 2>/dev/null || true
docker image prune -f 2>/dev/null || true

echo "=== truncate huge container logs ==="
find /var/lib/docker/containers -name '*-json.log' -size +20M -print -exec sh -c 'truncate -s 0 "$1"' _ {} \; 2>/dev/null || true

echo "=== delete vibe-trade ==="
rm -rf /opt/vibe-trade

echo "=== delete hermes-tools except openbb venv + small bins ==="
# keep openbb venv
if [[ -d /opt/hermes-tools/venvs/openbb ]]; then
  for d in /opt/hermes-tools/venvs/*; do
    base=$(basename "$d")
    if [[ "$base" != "openbb" ]]; then
      echo "rm venv $base"
      rm -rf "$d"
    fi
  done
fi
rm -rf /opt/hermes-tools/freqtrade /opt/hermes-tools/repos /opt/hermes-tools/logs
# keep /opt/hermes-tools/bin if useful
find /opt/hermes-tools -maxdepth 1 -type f -name '*.log' -delete 2>/dev/null || true

echo "=== delete alpaca state dirs ==="
rm -rf /root/.alpaca-paper /root/.alpaca-scalp15

echo "=== misc small cleans ==="
rm -f /root/1panel-*.tar.gz
rm -f /root/.hermes/logs/agent.log.1
journalctl --vacuum-size=40M >/dev/null 2>&1 || true
apt-get clean >/dev/null 2>&1 || true

echo "=== ensure trading units stay disabled ==="
for u in vibe-autotrade vibe-trading vibe-telegram; do
  systemctl disable --now "$u" 2>/dev/null || true
done

echo "=== OpenBB MCP systemd unit ==="
cat >/etc/systemd/system/openbb-mcp.service <<'UNIT'
[Unit]
Description=OpenBB MCP for Hermes Agent (market analysis)
After=network.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/hermes-tools/venvs/openbb/bin/openbb-mcp --transport streamable-http --host 127.0.0.1 --port 8100 --allowed-categories crypto,equity,news,economy --default-categories crypto,equity,news
Restart=on-failure
RestartSec=5
Environment=HOME=/root

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now openbb-mcp.service
sleep 2
systemctl is-active openbb-mcp.service || true
curl -sS -m 3 http://127.0.0.1:8100/health 2>&1 | head -c 200 || curl -sS -m 3 http://127.0.0.1:8100/ 2>&1 | head -c 200 || true
echo

echo "=== AFTER df ==="
df -h /
du -sh /opt/hermes-tools /opt/hermes-tools/venvs/openbb 2>/dev/null
echo
echo "=== still running synaptika ==="
docker ps --format '{{.Names}}'
systemctl is-active vibe-telegram-control hermes-gateway 2>/dev/null || true
pgrep -af 'hermes_cli.main gateway' | head -2 || true
systemctl is-active openbb-mcp
