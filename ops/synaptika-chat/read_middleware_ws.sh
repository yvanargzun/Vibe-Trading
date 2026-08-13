#!/bin/bash
docker exec synaptika-chat-webui sh -c "grep -n 'web_search' /app/backend/open_webui/utils/middleware.py | head -50"
echo '===='
docker exec synaptika-chat-webui sed -n '2520,2620p' /app/backend/open_webui/utils/middleware.py
echo '====2640===='
docker exec synaptika-chat-webui sed -n '2640,2720p' /app/backend/open_webui/utils/middleware.py
