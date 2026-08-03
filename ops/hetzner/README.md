# Hetzner Vibe runtime (canonical copies)

These files are the **code of record** for `/root/.vibe-trading` on the VPS.
Runtime secrets and state stay on the server (and locally in `~/.vibe-trading`) — **never commit `.env`**.

## Boundary

| Location | Role |
|----------|------|
| This folder (`ops/hetzner/`) | Source for Binance/Vibe loops, Telegram control bot, digests |
| `/root/.vibe-trading` on VPS | Runtime home: `.env`, state, charts history |
| `/opt/vibe-trade` | Vibe-Trading product / agent install |
| [Alpaca-Paper-Trading](https://github.com/yvanargzun/Alpaca-Paper-Trading) | Alpaca only — do not patch from here the other way |

Telegram tokens: set in `/root/.vibe-trading/.env` (and local `C:\Users\Pc\.vibe-trading\.env`).
Do **not** read `Projects\Synaptica\.env`.

## Deploy to VPS

```powershell
cd C:\Users\Pc\Vibe-Trade\ops\hetzner
scp -i $env:USERPROFILE\.ssh\hetzner_vibe `
  telegram_control_bot.py telegram_notify_prefs.py telegram_dynamic_monitor.py `
  telegram_monitor_loop.py vibe_autotrade_loop.py vibe_eth_scalp_loop.py `
  equity_chart.py dynamic_goals.py market_orchestrator.py trade_events.py `
  deploy_vibe_runtime.sh `
  root@46.225.50.87:/tmp/
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 "bash /tmp/deploy_vibe_runtime.sh"
```

One-off historical patches under `patch_binance_*.py` are kept for reference; prefer editing the canonical `.py` files above and redeploying.
