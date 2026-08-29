# Synaptika Chat

General-purpose Open WebUI on the Synaptika Trade VPS.

- Chat: https://synaptika-chat.duckdns.org
- LLM gateway: **[OmniRoute](https://github.com/diegosouzapw/OmniRoute)** only (`http://omniroute:20128/v1`)
- Default model: `auto/chat` (stable multi-turn)
- Open WebUI image pinned to `v0.11.0`
- Separate from Ops copiloto (`synaptika-trade.duckdns.org:8443`)

## Deploy

```bash
scp -i $env:USERPROFILE\.ssh\hetzner_vibe -r ops/synaptika-chat root@46.225.50.87:/tmp/
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 "bash /tmp/synaptika-chat/deploy.sh"
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 "bash /root/synaptika-chat/deploy_omniroute.sh"
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 "bash /root/synaptika-chat/apply_stability_fix.sh"
```

## Stability notes

- OWUI uses a **single** OpenAI connection (OmniRoute). Extra OpenRouter/Groq/Gemini URLs were removed because they inflated the model list (~500+) and made the UI flicker.
- `llm-proxy` is on Compose profile `fallback` (not started by default).
- Web search capability remains, but the globe is **off** by default.
- Caddy SSE timeouts are 10 minutes.

## OmniRoute dashboard

1. Create DuckDNS host **`synaptika-omni`** → `46.225.50.87`
2. Or tunnel: `ssh -i ~/.ssh/hetzner_vibe -L 20128:127.0.0.1:20128 root@46.225.50.87` → http://127.0.0.1:20128
3. Password: `OMNIROUTE_INITIAL_PASSWORD` in `secrets.env`

## Access

- URL: https://synaptika-chat.duckdns.org
- Email: `admin@localhost`
- Password: configured on the server
