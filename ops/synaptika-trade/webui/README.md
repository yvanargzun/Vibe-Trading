# Open WebUI → Synaptika Ops tools

El chat Ops controla los bots del VPS (Binance live + Alpaca paper) vía API
de **lectura + escritura**. Cursor no es necesario para operar.

## Tool Server (OpenAPI)

`apply_copilot.py` lo cablea solo. Manual (Admin → Settings → Tools):

| Campo | Valor |
|-------|--------|
| URL | `http://ops:8787` |
| Path | `/ops/api/openapi.json` |
| Auth | Bearer `OPS_API_KEY` |
| ID | `0` (el modelo usa `toolIds: ["server:0"]`) |

Write tools: `set_strategy_mode`, `unlock_strategy_mode`, `set_bot_halt`,
`set_notify_filter`, `set_strategy_knobs`, `enqueue_trade_intent`
(todas con `confirm=true` tras OK del usuario).

Lectura: `get_bot_digest`, `get_bot_status`, `get_control_status`,
`get_strategy_briefs`, `get_bot_activity`, `get_equity_history`, `get_win_loss`.

## System prompt + tools

Deploy (`deploy.sh`) aplica:

1. `apply_free_models.py` — allowlist `:free`
2. `apply_copilot.py` — modelo **Synaptika Copiloto**, prompt con control,
   Tool Server Ops, `function_calling=native`, `toolIds=["server:0"]`
3. restart `open-webui` (recarga cache de tool servers)

Manual:

```bash
cd /root/synaptika-trade
set -a; source secrets.env; set +a
# Actualizar archivos bind-mount IN PLACE (cat >, no cp) para no romper inodes Docker
docker compose --env-file secrets.env exec -T -e OPS_API_KEY="$OPS_API_KEY" \
  open-webui python3 /srv/webui/apply_copilot.py
docker compose --env-file secrets.env restart open-webui
```

El copiloto solo responde sobre bots Binance/Alpaca del VPS; el resto lo rechaza.
Cada turno inyecta el brief live vía filter `synaptika_ops_context`.

Modelo default: **Synaptika Copiloto**.

## OpenRouter (solo modelos :free)

`OPENAI_API_KEY` debe ser una key de OpenRouter
(`OPENAI_API_BASE_URL=https://openrouter.ai/api/v1`).

Sin créditos de pago, los modelos de pago fallan con respuesta vacía.
Por eso el portal restringe la lista a IDs que terminan en `:free`.

Tras cambiar la key o refrescar free models:

```bash
cd /root/synaptika-trade
docker compose --env-file secrets.env up -d open-webui
docker compose --env-file secrets.env exec -T open-webui python3 /srv/webui/apply_free_models.py
docker compose --env-file secrets.env restart open-webui
```
