# Synaptika Trade portal

Stack en el VPS Hetzner: **Caddy (HTTPS)** + **Ops panel** + **Open WebUI**.

- Ops: https://synaptika-trade.duckdns.org/
- Chat (Open WebUI): https://synaptika-trade.duckdns.org:8443/

Open WebUI se sirve en el **puerto 8443** en la raíz del sitio (no bajo `/chat`). El subpath `/chat` rompe la SPA.

## Secrets

`/root/synaptika-trade/secrets.env` — **nunca commits**:

```bash
DUCKDNS_DOMAIN=synaptika-trade
DUCKDNS_TOKEN=...
DUCKDNS_IP=46.225.50.87
OPS_PASSWORD=...
OPS_API_KEY=...          # para Open WebUI tools (header X-Ops-Key)
OPENAI_API_KEY=...                 # OpenRouter key
OPENAI_API_BASE_URL=https://openrouter.ai/api/v1
```

El chat solo lista modelos OpenRouter con sufijo `:free` (sin créditos de pago).
Default copiloto: `synaptika-auto` (Gemini → Ollama Cloud → OpenRouter `:free`).
Failover OpenRouter prioriza Gemma-4 / Nemotron free; **no** usa gpt-4o-mini/Haiku salvo
`ALLOW_PAID_OPENROUTER=1`. Tras deploy corre `webui/apply_free_models.py`.


Symlink: `ln -sfn secrets.env .env`

## Ops API (lectura + control)

Auth: cookie de sesión **o** `X-Ops-Key` / `Authorization: Bearer $OPS_API_KEY`.

| Path | Descripción |
|------|-------------|
| `/ops/api/status` | Snapshot + equity + activity |
| `/ops/api/digest` | Texto ES para el modelo |
| `/ops/api/copilot` | Brief live (incluye instrucciones de control) |
| `/ops/api/strategy` | Briefs + modo live |
| `/ops/api/activity` | Ciclos / trades / skips |
| `/ops/api/equity` | Series Chart.js |
| `/ops/api/control` | GET estado control (locks/HALT/knobs/intents) |
| `/ops/api/control/mode` | POST forzar modo (`confirm=true`) |
| `/ops/api/control/unlock` | POST soltar lock de modo |
| `/ops/api/control/halt` | POST halt/resume |
| `/ops/api/control/notify` | POST filtro Telegram |
| `/ops/api/control/knobs` | POST overlay TP/SL/ORDER_USD/… |
| `/ops/api/control/intent` | POST buy/sell/close (cola del bot) |
| `/ops/api/openapi.json` | Spec OpenAPI para Tool Server (read+write) |

Write tools (POST) requieren `confirm=true` tras OK del usuario en el chat Ops.
El copiloto Open WebUI las llama vía Tool Server (`server:0`); no hace falta Cursor.

Ver [webui/README.md](webui/README.md).

## Caddy / chat

Open WebUI bajo `/chat` carga `/api` y `/static` en la **raíz**. El Caddyfile enruta esos paths a `open-webui`, y deja `/static/brand` + `/static/ops` + `/ops*` + `/` en Ops.

## Deploy

```bash
# desde el VPS con el árbol en /tmp/synaptika-trade o ya en DEST
bash /root/synaptika-trade/deploy.sh
```

Local sync tip (Windows → VPS): `scp -r ops/synaptika-trade root@VPS:/tmp/` luego `deploy.sh`.

## DuckDNS

1. Token UUID en duckdns.org
2. IP actual = VPS
3. `systemctl start duckdns-synaptika.service`
