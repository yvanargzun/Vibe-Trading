#!/bin/bash
docker exec synaptika-chat-webui sh -c "grep -RIn --include='*.py' -E 'ENABLE_RAG_WEB_SEARCH|RAG_WEB_SEARCH_ENGINE|ENABLE_WEB_SEARCH|WEB_SEARCH_ENGINE' /app/backend/open_webui 2>/dev/null | head -50"
