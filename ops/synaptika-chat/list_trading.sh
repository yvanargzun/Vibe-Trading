#!/bin/bash
set -u
echo "=== systemd trading-ish ==="
systemctl list-units --type=service --all --no-pager 2>/dev/null | grep -Ei 'vibe|trade|alpaca|freq|binance|scalp|paper' || true
echo
echo "=== unit files ==="
ls /etc/systemd/system/*vibe* /etc/systemd/system/*trade* /etc/systemd/system/*freq* /etc/systemd/system/*alpaca* 2>/dev/null || true
echo
echo "=== docker trade-ish ==="
docker ps -a --format '{{.Names}} {{.Status}}' | grep -Ei 'trade|vibe|alpaca|freq|llm-proxy|ops' || true
echo
echo "=== mem before ==="
free -h
