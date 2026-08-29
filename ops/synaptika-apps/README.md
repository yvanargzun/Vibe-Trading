# Synaptika apps on VPS (replaces Render)

- Demos: https://synaptika-demos.duckdns.org/
- Messenger: https://synaptika-messenger.duckdns.org/health
- Meta webhook: `https://synaptika-messenger.duckdns.org/webhook`

## Deploy (from PC)

```powershell
# 1) Sync ops + secrets (never commit secrets.env)
scp -i $env:USERPROFILE\.ssh\hetzner_vibe -r ops/synaptika-apps root@46.225.50.87:/root/
scp -i $env:USERPROFILE\.ssh\hetzner_vibe .env root@46.225.50.87:/root/synaptika-apps/secrets.from-pc.env

# 2) Merge PC env into secrets + deploy
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 @"
cd /root/synaptika-apps
# merge non-empty keys from PC .env without clobbering generated secrets
python3 - <<'PY'
from pathlib import Path
dst = Path('secrets.env')
src = Path('secrets.from-pc.env')
data = {}
if dst.exists():
    for line in dst.read_text().splitlines():
        if not line.strip() or line.strip().startswith('#') or '=' not in line: continue
        k,v = line.split('=',1); data[k]=v
if src.exists():
    for line in src.read_text(encoding='utf-8', errors='ignore').splitlines():
        if not line.strip() or line.strip().startswith('#') or '=' not in line: continue
        k,v = line.split('=',1)
        if v.strip(): data[k]=v
dst.write_text(''.join(f'{k}={v}\n' for k,v in data.items()))
dst.chmod(0o600)
src.unlink(missing_ok=True)
print('secrets keys', len(data))
PY
bash deploy.sh
cd /root/synaptika-trade && docker compose --env-file secrets.env up -d --force-recreate caddy
"@
```

## Notes

- Shares Docker network `synaptika-trade_default` with OmniRoute.
- Uses internal `http://synaptika-chat-omniroute:20128/v1` (API key from chat gateway).
- Create DuckDNS hosts `synaptika-demos` and `synaptika-messenger` (same account/token as trade).
