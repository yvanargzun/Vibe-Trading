#!/bin/bash
docker exec synaptika-chat-webui sh -c "grep -RIn --include='*.py' -E 'MODEL_FILTER|BASE_MODELS_CACHE|DEFAULT_PINNED|OPENAI_API_BASE_URLS|ENABLE_BASE_MODELS' /app/backend/open_webui/env.py /app/backend/open_webui/config.py 2>/dev/null | head -60"
echo '---'
docker exec synaptika-chat-webui sh -c "grep -RIn --include='*.py' 'model_order\|pinned_models\|MODEL_ORDER' /app/backend/open_webui/config.py 2>/dev/null | head -30"
