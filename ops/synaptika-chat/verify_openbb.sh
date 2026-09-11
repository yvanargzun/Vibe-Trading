#!/bin/bash
systemctl status openbb-mcp.service --no-pager -l | head -45
echo "---"
ss -lntp | grep 8100 || echo "port 8100 not listening"
journalctl -u openbb-mcp.service -n 40 --no-pager
echo "---"
free -h
df -h /
du -sh /opt/hermes-tools/venvs/openbb
curl -sS -m 3 http://127.0.0.1:8642/health; echo
systemctl is-active vibe-telegram-control openbb-mcp
