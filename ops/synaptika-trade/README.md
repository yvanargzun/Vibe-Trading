# Synaptika Trade portal (Ops + Open WebUI)

Menus simples en `https://synaptika-trade.duckdns.org`:

| Ruta | Qué es |
|------|--------|
| `/` | Panel Ops (posiciones Binance + Alpaca) + menú |
| `/chat/` | Open WebUI (copiloto) |
| `/api/status` | JSON de estado (tras login) |

Brand: logos de Synaptica `public/brand/`.

## Secrets (solo en VPS)

`/root/synaptika-trade/secrets.env` — **nunca commits**:

```env
DUCKDNS_DOMAIN=synaptika-trade
DUCKDNS_TOKEN=...
OPS_PASSWORD=...
OPENAI_API_KEY=...
LETSENCRYPT_EMAIL=tu@email.com
```

## Deploy

```powershell
cd C:\Users\Pc\Vibe-Trade\ops\synaptika-trade
# pack
tar -cf $env:TEMP\synaptika-trade.tar --exclude=.git .
scp -i $env:USERPROFILE\.ssh\hetzner_vibe $env:TEMP\synaptika-trade.tar root@46.225.50.87:/tmp/
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 @"
mkdir -p /tmp/synaptika-trade /root/synaptika-trade
tar -xf /tmp/synaptika-trade.tar -C /tmp/synaptika-trade
bash /tmp/synaptika-trade/deploy.sh
"@
```

## DuckDNS

1. Entra a https://www.duckdns.org  
2. Crea dominio `synaptika-trade`  
3. Copia el **token** de la cuenta (UUID)  
4. Pégalo en `secrets.env` y: `systemctl start duckdns-synaptika.service`

IP VPS: `46.225.50.87` · abre firewall 80/443 si aplica.

## Acceso

1. Abre https://synaptika-trade.duckdns.org  
2. Login con `OPS_PASSWORD`  
3. Botón **Abrir chat IA** → Open WebUI (crea el admin la primera vez)

Alternativa SSH: `ssh -L 8787:127.0.0.1:8787 -L 3000:127.0.0.1:8080 ...` si el DNS aún no resuelve.
