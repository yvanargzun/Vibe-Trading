# Synaptika Chat

General-purpose Open WebUI on the Synaptika Trade VPS.

- Chat: https://synaptika-chat.duckdns.org
- LLM gateway: **[OmniRoute](https://github.com/diegosouzapw/OmniRoute)** (`http://omniroute:20128/v1` inside Docker)
- Fallback chain still includes local `llm-proxy` + OpenRouter/Gemini/Groq
- Default model: `auto/best-free`
- Separate from Ops copiloto (`synaptika-trade.duckdns.org:8443`)

## Deploy

```bash
# from Windows
scp -i $env:USERPROFILE\.ssh\hetzner_vibe -r ops/synaptika-chat root@46.225.50.87:/tmp/
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 "bash /tmp/synaptika-chat/deploy.sh"
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 "bash /root/synaptika-chat/deploy_omniroute.sh"
```

Secrets: `/root/synaptika-chat/secrets.env` (seeded from trade keys).

## OmniRoute dashboard

1. Create DuckDNS host **`synaptika-omni`** → `46.225.50.87`, then open https://synaptika-omni.duckdns.org
2. Or tunnel: `ssh -i ~/.ssh/hetzner_vibe -L 20128:127.0.0.1:20128 root@46.225.50.87` → http://127.0.0.1:20128
3. Password: `OMNIROUTE_INITIAL_PASSWORD` in `secrets.env`
4. In dashboard → Providers: connect OpenRouter / Gemini / OAuth free tiers as needed.

Open WebUI already points at OmniRoute (`OPENAI_API_BASE_URL=http://omniroute:20128/v1`).

## Access

- Chat URL: https://synaptika-chat.duckdns.org
- Email: `admin@localhost`
- Password: configured on the server (see `set_password.py`)
- Signup disabled (`ENABLE_SIGNUP=false`)

## DuckDNS

`synaptika-chat` (and optionally `synaptika-omni`) must point to **46.225.50.87**. Deploy updates them when `DUCKDNS_TOKEN` is set.
