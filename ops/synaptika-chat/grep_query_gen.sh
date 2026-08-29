#!/bin/bash
docker exec synaptika-chat-webui sh -c 'grep -RIn --include="*.py" -E "ENABLE_SEARCH_QUERY|search_query_generation|SearchQuery" /app/backend/open_webui | head -50'
