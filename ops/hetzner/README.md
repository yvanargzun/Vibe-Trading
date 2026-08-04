# Hetzner Vibe runtime (canonical copies)

Code of record for `/root/.vibe-trading` on the VPS.
Secrets/state live only in that runtime home (and local `~/.vibe-trading`) — **never commit `.env`**.

## Boundary

| Location | Role |
|----------|------|
| `ops/hetzner/` | Source for Binance/Vibe loops, Telegram control, digests |
| `/root/.vibe-trading` | Runtime: `.env`, state, chart history |
| `/opt/vibe-trade` | Vibe-Trading agent install |
| [Alpaca-Paper-Trading](https://github.com/yvanargzun/Alpaca-Paper-Trading) | Alpaca only |

Telegram: `/root/.vibe-trading/.env` (local: `C:\Users\Pc\.vibe-trading\.env`).
Do **not** read `Projects\Synaptica\.env`.

## Deploy

```powershell
cd C:\Users\Pc\Vibe-Trade\ops\hetzner
scp -i $env:USERPROFILE\.ssh\hetzner_vibe `
  telegram_control_bot.py telegram_notify_prefs.py telegram_dynamic_monitor.py `
  telegram_monitor_loop.py vibe_autotrade_loop.py `
  equity_chart.py dynamic_goals.py market_orchestrator.py trade_events.py `
  binance_wallets.py v6_config.py v6_trace.py PROMPT_V6.md `
  deploy_vibe_runtime.sh `
  root@46.225.50.87:/tmp/
ssh -i $env:USERPROFILE\.ssh\hetzner_vibe root@46.225.50.87 "bash /tmp/deploy_vibe_runtime.sh"
```

`vibe-eth-scalp` is **retired** (deploy stops + disables it). v6 owns the full Spot book.

## smart-fast-v6 layout

| File | Role |
|------|------|
| `PROMPT_V6.md` | Ley operativa canónica v2 |
| `v6_config.py` | Knobs (única fuente) |
| `v6_trace.py` | Trazas por fase → `v6_cycles.jsonl` |
| `vibe_autotrade_loop.py` | Loop de ejecución |
| `market_orchestrator.py` | Modos (sin scalp_primary activo) |

Post-mortem en el VPS:

```bash
python3 /root/.vibe-trading/v6_trace.py dump 10
python3 /root/.vibe-trading/v6_trace.py last-error
journalctl -u vibe-autotrade -n 200 --no-pager
systemctl is-active vibe-eth-scalp   # expect: inactive
```

`patch_binance_*.py` / chart patches are historical one-offs; prefer editing canonical `.py` files and redeploying.
