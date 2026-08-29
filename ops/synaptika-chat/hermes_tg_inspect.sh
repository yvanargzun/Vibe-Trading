#!/bin/bash
python3 <<'PY'
import yaml
from pathlib import Path
d=yaml.safe_load(Path('/root/.hermes/config.yaml').read_text())
# find telegram keys
for k,v in d.items():
    if 'tele' in str(k).lower() or (isinstance(v,dict) and 'telegram' in v):
        print('KEY', k, '->', type(v))
print('platform_toolsets', d.get('platform_toolsets'))
# dump any nested telegram
import json
def find(obj, path=''):
    if isinstance(obj, dict):
        for k,v in obj.items():
            p=f'{path}.{k}' if path else k
            if 'telegram' in str(k).lower():
                print('FOUND', p, json.dumps(v, default=str)[:500])
            find(v, p)
find(d)
PY
# also grep raw
grep -n 'telegram' /root/.hermes/config.yaml | head
