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
Default: `inclusionai/ling-3.0-flash:free`. Tras deploy corre
`webui/apply_free_models.py` para fijar la allowlist en la DB de Open WebUI.


Symlink: `ln -sfn secrets.env .env`

## Ops API (read-only)

Auth: cookie de sesión **o** `X-Ops-Key: $OPS_API_KEY`.

| Path | Descripción |
|------|-------------|
| `/ops/api/status` | Snapshot + equity + activity |
| `/ops/api/digest` | Texto ES para el modelo |
| `/ops/api/strategy` | Briefs + modo live |
| `/ops/api/activity` | Ciclos / trades / skips |
| `/ops/api/equity` | Series Chart.js |
| `/ops/api/openapi.json` | Spec OpenAPI para Tool Server |

Ver [webui/README.md](webui/README.md) para cablear Open WebUI.

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
