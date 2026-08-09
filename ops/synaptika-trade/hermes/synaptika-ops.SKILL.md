---
name: synaptika-ops
description: Estado live de TODOS los bots y servicios Synaptika en el VPS. Usa cuando pregunten modo, estrategia, equity, halt, Ops, scalp15, Alpaca o Binance.
---

# Synaptika Ops (vista completa)

Cuando el usuario pregunte por bots / dinero / modo / halt / VPS:

1. **Primero** ejecuta `synaptika-status` (cubre Binance + Alpaca + scalp15 + systemd).
   - Fallback: `bash /opt/hermes-tools/bin/synaptika-status`
2. Si falla Ops API: `vibe-status` + leer:
   - `/root/.vibe-trading/autotrade_state.json` + `strategy_mode.json`
   - `/root/.alpaca-paper/state.json`
   - `/root/.alpaca-scalp15/state.json`
3. Opcional HTTP (misma info que Ops UI):
   ```bash
   source /root/synaptika-trade/secrets.env
   curl -sS -H "X-Ops-Key: $OPS_API_KEY" http://127.0.0.1:8787/ops/api/hermes
   curl -sS -H "X-Ops-Key: $OPS_API_KEY" http://127.0.0.1:8787/ops/api/status
   ```
4. Reporta en español claro (novato):
   - Dinero ahora (equity) de cada bot
   - Hoy % (PnL)
   - ¿Puede comprar? sí/no
   - Modo en palabras simples + HALT
   - Servicios encendidos/apagados

## Bots que DEBES conocer
| Bot | Dinero | Path |
|-----|--------|------|
| Binance smart-fast-v6 | REAL Spot | `/root/.vibe-trading` |
| Alpaca paper | paper | `/root/.alpaca-paper` |
| Alpaca scalp15 | paper 15m | `/root/.alpaca-scalp15` |
| Freqtrade crypto/stocks | paper laboratorio | `/opt/hermes-tools/freqtrade` |

## Portal Ops
- UI: https://synaptika-trade.duckdns.org/
- Chat: https://synaptika-trade.duckdns.org:8443/
- Código portal: `/root/synaptika-trade`

No inventes cifras. No pegues secretos. Para cambiar modo live o cerrar posiciones REALES: confirmación explícita del usuario.
