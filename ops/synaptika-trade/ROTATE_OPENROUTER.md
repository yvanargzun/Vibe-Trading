# Rotar OpenRouter API key

Si la key `sk-or-…` se pegó en un chat, log o ticket, **rótala**:

1. Entra a https://openrouter.ai/settings/keys
2. Crea una key nueva (solo free / sin créditos si quieres $0)
3. Revoca o borra la key vieja
4. Actualiza en el VPS:

```bash
# /root/synaptika-trade/secrets.env
OPENROUTER_API_KEY=sk-or-NUEVA
OPENAI_API_KEY=sk-or-NUEVA   # si el portal la usa como alias

# /root/.vibe-trading/agent.env  (si el agent corre en el VPS)
LANGCHAIN_MODEL_NAME=google/gemma-4-31b-it:free
OPENROUTER_API_KEY=sk-or-NUEVA
SWARM_LEAD_MODEL=google/gemma-4-31b-it:free
SWARM_WORKER_MODEL=inclusionai/ling-3.0-flash:free
SWARM_FREE_TIER=auto
SWARM_VRAM_MODE=balanced

cd /root/synaptika-trade && bash deploy.sh
```

5. En tu PC: actualiza `agent/.env` con la misma key nueva (no la subas a git).

El proxy Ops ignora modelos de pago salvo `ALLOW_PAID_OPENROUTER=1`.
