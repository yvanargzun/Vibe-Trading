#!/bin/bash
set -u
echo "=== df ==="
df -h /
echo
echo "=== reclaim candidates ==="
echo -n "docker build cache: "; docker builder du 2>/dev/null | awk '/Reclaimable:/{print $2,$3}' | head -1
echo -n "big container json.log: "; du -h /var/lib/docker/containers/*/*-json.log 2>/dev/null | sort -hr | head -1
echo -n "freqtrade logs: "; du -ch /opt/hermes-tools/freqtrade/*.log 2>/dev/null | tail -1
echo -n "1panel tar: "; du -h /root/1panel-*.tar.gz 2>/dev/null | head -1
echo -n "journal: "; journalctl --disk-usage 2>/dev/null
echo -n "hermes rotated logs: "; du -ch /root/.hermes/logs/agent.log* 2>/dev/null | tail -1
echo
echo "=== open-webui images (standby containers) ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}' | grep -i open-webui
docker ps -a --filter name=webui --format '{{.Names}} {{.Status}}'
echo
echo "=== unused-ish docker ==="
docker system df
echo
echo "=== trading stuff on disk (stopped but files remain) ==="
du -sh /opt/vibe-trade /opt/hermes-tools/venvs /opt/hermes-tools/freqtrade 2>/dev/null
