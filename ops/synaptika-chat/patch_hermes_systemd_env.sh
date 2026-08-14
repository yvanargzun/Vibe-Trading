#!/bin/bash
set -euo pipefail
SVC=/root/.config/systemd/user/hermes-gateway.service
python3 <<'PY'
from pathlib import Path
p = Path("/root/.config/systemd/user/hermes-gateway.service")
text = p.read_text(encoding="utf-8")
lines = [ln for ln in text.splitlines() if "OPENAI_BASE_URL=" not in ln and "OPENAI_API_KEY=" not in ln]
out = []
inserted = False
for ln in lines:
    out.append(ln)
    if (not inserted) and ln.startswith('Environment="HERMES_HOME='):
        out.append('Environment="OPENAI_BASE_URL=http://127.0.0.1:20128/v1"')
        out.append('Environment="OPENAI_API_KEY=omniroute"')
        inserted = True
if not inserted:
    # after [Service]
    out2 = []
    for ln in out:
        out2.append(ln)
        if ln.strip() == "[Service]":
            out2.append('Environment="OPENAI_BASE_URL=http://127.0.0.1:20128/v1"')
            out2.append('Environment="OPENAI_API_KEY=omniroute"')
            inserted = True
    out = out2
p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("inserted", inserted)
PY
grep -n Environment "$SVC"
export XDG_RUNTIME_DIR=/run/user/0
systemctl --user daemon-reload
systemctl --user restart hermes-gateway.service
sleep 2
PID=$(pgrep -f 'hermes_cli.main gateway' | head -1)
echo "pid=$PID"
tr '\0' '\n' < /proc/$PID/environ | grep -iE 'OPENAI|HERMES_HOME' | sed -E 's/(KEY)=.*/\1=***/'
systemctl --user is-active hermes-gateway.service
