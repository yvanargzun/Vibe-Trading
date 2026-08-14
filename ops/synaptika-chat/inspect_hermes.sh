#!/bin/bash
set -e
echo '=== hermes how started ==='
ps -eo pid,ppid,user,cmd | grep -i 'hermes_cli.main gateway' | grep -v grep
ls -la /etc/systemd/system/*hermes* /lib/systemd/system/*hermes* 2>/dev/null || true
systemctl list-units --all --no-pager | grep -i hermes || true
# maybe started from 1panel or crontab or screen
crontab -l 2>/dev/null | grep -i hermes || true
ls /etc/supervisor/conf.d/ 2>/dev/null || true
grep -RIn 'hermes_cli\|gateway run' /etc/systemd /root/.config 2>/dev/null | head -20 || true

echo '=== config.yaml (redacted-ish) ==='
# show structure without dumping secrets
python3 <<'PY'
from pathlib import Path
import yaml, json, re
p=Path('/root/.hermes/config.yaml')
text=p.read_text(encoding='utf-8', errors='replace')
# redact keys
red=re.sub(r'(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*["\']?[^"\'\n]+', r'\1: ***', text)
print(red[:4000])
print('---LEN', len(text))
PY

echo '=== auth.json keys only ==='
python3 <<'PY'
import json
from pathlib import Path
p=Path('/root/.hermes/auth.json')
if p.exists():
  d=json.loads(p.read_text())
  def walk(x, prefix=''):
    if isinstance(x, dict):
      for k,v in x.items():
        if any(s in k.lower() for s in ('key','token','secret','password')):
          print(prefix+k, '=', ('***len'+str(len(str(v))) if v else None))
        else:
          walk(v, prefix+k+'.')
    elif isinstance(x, list) and x and not isinstance(x[0], (dict,list)):
      print(prefix, 'list', len(x))
  walk(d)
  print('top_keys', list(d.keys())[:40])
else:
  print('no auth.json')
PY

echo '=== env around hermes process ==='
PID=$(pgrep -f 'hermes_cli.main gateway' | head -1)
if [ -n "$PID" ]; then
  tr '\0' '\n' < /proc/$PID/environ | grep -iE 'OPENAI|BASE_URL|API_KEY|MODEL|HERMES|OLLAMA|OPENROUTER|GEMINI' | sed -E 's/(KEY|TOKEN|SECRET|PASSWORD)=.*/\1=***/'
fi
