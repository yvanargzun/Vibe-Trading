#!/bin/bash
docker exec -i synaptika-chat-webui python - <<'PY'
import sqlite3, json
c=sqlite3.connect('/app/backend/data/webui.db')
print('cols:')
for r in c.execute('PRAGMA table_info(model)'):
    print(r)
row=c.execute('select * from model where id=?',('synaptika-chat-auto',)).fetchone()
cols=[r[1] for r in c.execute('PRAGMA table_info(model)')]
print('ROW:')
for k,v in zip(cols,row):
    print(repr(k), type(v).__name__, repr(v)[:200])
PY

echo '==== ModelModel ===='
docker exec synaptika-chat-webui sh -c "grep -n 'class ModelModel\|updated_at\|created_at\|base_model_id\|meta:' /app/backend/open_webui/models/models.py | head -60"
docker exec synaptika-chat-webui sed -n '1,120p' /app/backend/open_webui/models/models.py
