#!/bin/bash
docker exec synaptika-chat-webui sed -n '140,220p' /app/backend/open_webui/utils/models.py
echo '====440===='
docker exec synaptika-chat-webui sed -n '420,480p' /app/backend/open_webui/utils/models.py
echo '====310===='
docker exec synaptika-chat-webui sed -n '300,360p' /app/backend/open_webui/utils/models.py
