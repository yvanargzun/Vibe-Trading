#!/bin/bash
docker exec synaptika-chat-webui sh -c "grep -RIn --include='*.py' --include='*.svelte' -E \"function_calling.*legacy|Function Calling|DEFAULT.*function_calling|'legacy'|'native'\" /app/backend/open_webui/utils/middleware.py /app/backend/open_webui/utils/chat.py /app/src 2>/dev/null | head -40"
docker exec synaptika-chat-webui sh -c "grep -RIn --include='*.py' 'features.get..web_search|web_search.*True|process_web_search' /app/backend/open_webui 2>/dev/null | head -40"
