# Open WebUI → Synaptika Ops tools

El chat debe llamar la API read-only de Ops (misma red Docker).

## Tool Server (OpenAPI)

En Open WebUI → **Admin** → **Settings** → **Tools** (o External Tools):

| Campo | Valor |
|-------|--------|
| URL | `http://ops:8787/ops/api/openapi.json` |
| Auth header | `X-Ops-Key` |
| Auth value | el mismo `OPS_API_KEY` de `/root/synaptika-trade/secrets.env` |

Endpoints útiles:
- `GET /ops/api/digest` — resumen texto ES
- `GET /ops/api/status` — snapshot completo
- `GET /ops/api/strategy` — briefs + modo live
- `GET /ops/api/activity` — ciclos / fills / skips
- `GET /ops/api/equity` — series de equity

## System prompt + tools

Deploy aplica automáticamente:
1. `apply_free_models.py` — allowlist `:free`
2. `apply_copilot.py` — modelo **Synaptika Copiloto**, system prompt on-topic, Tool Server Ops

Manual:

```bash
cd /root/synaptika-trade
set -a; source secrets.env; set +a
docker compose --env-file secrets.env exec -T open-webui python3 /srv/webui/apply_free_models.py
docker compose --env-file secrets.env exec -T -e OPS_API_KEY="$OPS_API_KEY" \
  open-webui python3 /srv/webui/apply_copilot.py
docker compose --env-file secrets.env restart open-webui
```

El copiloto **solo** responde sobre bots Binance/Alpaca del VPS; el resto lo rechaza.

Cada turno inyecta el digest live vía filter `synaptika_ops_context` + Tool Server Ops
(`http://ops:8787` `/ops/api/openapi.json`, Bearer `OPS_API_KEY`).

Modelo default: **Synaptika Copiloto**.

## OpenRouter (solo modelos :free)

`OPENAI_API_KEY` debe ser una key de OpenRouter (`OPENAI_API_BASE_URL=https://openrouter.ai/api/v1`).

Sin créditos de pago, los modelos de pago fallan con respuesta vacía (p. ej. “requires more credits”).
Por eso el portal restringe la lista a IDs que terminan en `:free`.

Tras cambiar la key o refrescar free models:

```bash
cd /root/synaptika-trade
docker compose --env-file secrets.env up -d open-webui
docker compose --env-file secrets.env exec -T open-webui python3 /srv/webui/apply_free_models.py
docker compose --env-file secrets.env restart open-webui
```
