# Synaptika Chat

General-purpose Open WebUI on the Synaptika Trade VPS.

- URL: https://synaptika-chat.duckdns.org
- Failover: **OpenRouter `:free` → Gemini free → Groq free**
- Separate from Ops copiloto (`synaptika-trade.duckdns.org:8443`)

## Deploy

```bash
# from Windows
scp -i $env:USERPROFILE\.ssh\hetzner_vibe -r ops/synaptika-chat root@46.225.50.87:/tmp/
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 "bash /tmp/synaptika-chat/deploy.sh"
```

Secrets: `/root/synaptika-chat/secrets.env` (seeded from trade keys).

Add Groq:

```bash
# on VPS
sed -i 's/^GROQ_API_KEY=.*/GROQ_API_KEY=gsk_.../' /root/synaptika-chat/secrets.env
cd /root/synaptika-chat && docker compose --env-file secrets.env up -d
```

Access: Open WebUI login **once** (session cookie). No HTTP basic auth.

- URL: https://synaptika-chat.duckdns.org
- Email: `admin@localhost`
- Password: configured on the server (see `set_password.py`)
- Signup disabled (`ENABLE_SIGNUP=false`)

## DuckDNS

`synaptika-chat` must point to **46.225.50.87** (this VPS). Deploy updates it via DuckDNS API when `DUCKDNS_TOKEN` is set.
