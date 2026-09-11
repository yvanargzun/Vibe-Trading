#!/bin/bash
set -u
echo "=== containers ==="
docker ps -a --format '{{.Names}} {{.Image}} {{.Status}}' | grep -i webui || echo "(none)"
echo "=== images ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}' | grep -i webui || echo "(none)"
echo "=== volumes ==="
docker volume ls | grep -i webui || echo "(none)"
echo "=== compose / dirs ==="
find /root /opt -maxdepth 4 \( -iname '*open-webui*' -o -iname '*openwebui*' -o -iname '*webui*' \) 2>/dev/null | head -40
echo "=== df before ==="
df -h /
