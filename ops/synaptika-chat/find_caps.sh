#!/bin/bash
docker exec synaptika-chat-webui sh -c "grep -RIn --include='*.py' --include='*.svelte' --include='*.ts' -E 'capabilities.*web_search|web_search.*capabilities|defaultFeatureIds|feature_ids' /app/backend/open_webui /app/build 2>/dev/null | head -40"
docker exec synaptika-chat-webui sh -c "grep -RIn --include='*.py' 'web_search' /app/backend/open_webui/utils/models.py /app/backend/open_webui/routers/models.py /app/backend/open_webui/models 2>/dev/null | head -40"
