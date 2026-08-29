#!/usr/bin/env bash
set -euo pipefail
cd /root/synaptika-apps
sed -i 's/\r$//' deploy.sh
chmod +x deploy.sh

python3 <<'PY'
from pathlib import Path
dst = Path('secrets.env')
src = Path('secrets.from-pc.env')
data = {}
if dst.exists():
    for line in dst.read_text().splitlines():
        if not line.strip() or line.strip().startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        data[k] = v
if src.exists():
    for line in src.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line.strip() or line.strip().startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        if v.strip():
            data[k] = v
dst.write_text(''.join(f'{k}={v}\n' for k, v in data.items()))
dst.chmod(0o600)
src.unlink(missing_ok=True)
print('secrets_keys', len(data))
PY

docker image prune -f >/dev/null || true
bash deploy.sh
cd /root/synaptika-trade
docker compose --env-file secrets.env up -d --force-recreate caddy
echo MIGRATE_DONE
