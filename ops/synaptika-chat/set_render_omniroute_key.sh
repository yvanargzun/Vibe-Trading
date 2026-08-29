#!/usr/bin/env bash
# Set OMNIROUTE_API_KEY on both Render services from the VPS gateway key file.
# Usage (on PC with RENDER_API_KEY exported):
#   scp -i ~/.ssh/hetzner_vibe root@46.225.50.87:/root/synaptika-chat/OMNIROUTE_GATEWAY_API_KEY.txt /tmp/omni.key
#   RENDER_API_KEY=rnd_... bash ops/synaptika-chat/set_render_omniroute_key.sh /tmp/omni.key
set -euo pipefail
KEY_FILE="${1:-}"
if [[ -z "${RENDER_API_KEY:-}" ]]; then
  echo "Set RENDER_API_KEY first (https://dashboard.render.com/u/settings#api-keys)" >&2
  exit 1
fi
if [[ -z "$KEY_FILE" || ! -f "$KEY_FILE" ]]; then
  echo "Usage: $0 /path/to/OMNIROUTE_GATEWAY_API_KEY.txt" >&2
  exit 1
fi
KEY="$(tr -d '\r\n' < "$KEY_FILE")"
OWNER="${RENDER_OWNER_ID:-}"
if [[ -z "$OWNER" ]]; then
  OWNER=$(curl -fsS -H "Authorization: Bearer $RENDER_API_KEY" \
    https://api.render.com/v1/owners | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[0]["owner"]["id"] if isinstance(d,list) else d["owner"]["id"])')
fi
echo "owner=$OWNER"
SERVICES=$(curl -fsS -H "Authorization: Bearer $RENDER_API_KEY" \
  "https://api.render.com/v1/services?limit=50")
python3 - <<PY
import json,os,urllib.request
services=json.loads('''$SERVICES''')
want={"Synaptika-demos","synaptika-messengerfb"}
key=os.environ.get("KEY") or open("$KEY_FILE").read().strip()
api=os.environ["RENDER_API_KEY"]
for row in services:
  s=row.get("service") or row
  name=s.get("name")
  if name not in want: continue
  sid=s["id"]
  body=json.dumps({"value":key}).encode()
  # PUT env var
  req=urllib.request.Request(
    f"https://api.render.com/v1/services/{sid}/env-vars/OMNIROUTE_API_KEY",
    data=body, method="PUT",
    headers={"Authorization":f"Bearer {api}","Content-Type":"application/json","Accept":"application/json"},
  )
  with urllib.request.urlopen(req) as r:
    print(name, sid, r.status)
  # trigger deploy
  req2=urllib.request.Request(
    f"https://api.render.com/v1/services/{sid}/deploys",
    data=b"{}", method="POST",
    headers={"Authorization":f"Bearer {api}","Content-Type":"application/json","Accept":"application/json"},
  )
  with urllib.request.urlopen(req2) as r2:
    print("deploy", name, r2.status)
PY
