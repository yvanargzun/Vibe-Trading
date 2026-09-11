#!/bin/bash
set -u
echo "=== df ==="
df -h /
echo
echo "=== top dirs / ==="
du -xh --max-depth=1 / 2>/dev/null | sort -hr | head -20
echo
echo "=== /var ==="
du -xh --max-depth=1 /var 2>/dev/null | sort -hr | head -15
echo
echo "=== /root ==="
du -xh --max-depth=1 /root 2>/dev/null | sort -hr | head -20
echo
echo "=== /usr ==="
du -xh --max-depth=1 /usr 2>/dev/null | sort -hr | head -12
echo
echo "=== docker ==="
docker system df 2>/dev/null || true
echo
echo "=== journal ==="
journalctl --disk-usage 2>/dev/null || true
echo
echo "=== large logs (top 25) ==="
find /var/log /root /opt /tmp -type f \( -name '*.log' -o -name '*.log.*' -o -name '*.gz' \) -size +20M 2>/dev/null | head -40 | while read f; do du -h "$f"; done | sort -hr | head -25
echo
echo "=== hermes logs/cache ==="
du -sh /root/.hermes/logs /root/.hermes/cache /root/.hermes/sessions /root/.hermes/image_cache /root/.hermes/audio_cache /root/.hermes/sandboxes 2>/dev/null || true
ls -lhS /root/.hermes/logs 2>/dev/null | head -12
echo
echo "=== apt / tmp / old ==="
du -sh /var/cache/apt /var/tmp /tmp /var/lib/docker 2>/dev/null
echo
echo "=== biggest files >200M ==="
find / -xdev -type f -size +200M 2>/dev/null | head -40 | while read f; do du -h "$f"; done | sort -hr | head -25
echo
echo "=== docker unused ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}} {{.ID}}' 2>/dev/null | head -30
docker ps -a --filter status=exited --format '{{.Names}} {{.Status}}' 2>/dev/null | head -20
