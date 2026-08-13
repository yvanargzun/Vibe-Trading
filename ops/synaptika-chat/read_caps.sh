#!/bin/bash
docker exec synaptika-chat-webui sed -n '300,370p' /app/backend/open_webui/utils/automations.py
echo '===='
docker exec synaptika-chat-webui sh -c "grep -n 'defaultFeatureIds\|info\[.meta\|custom_models\|Models.get' /app/backend/open_webui/utils/models.py | head -40"
echo '===='
docker exec synaptika-chat-webui sed -n '1,80p' /app/backend/open_webui/utils/models.py
echo '===='
docker exec synaptika-chat-webui sh -c "grep -n 'defaultFeatureIds\|capabilities\|web_search' /app/backend/open_webui/utils/models.py | head -40"
