#!/usr/bin/env python3
"""Patch vibe_autotrade_loop.py to prefix Telegram messages with [Binance]."""
from pathlib import Path

p = Path("/root/.vibe-trading/vibe_autotrade_loop.py")
text = p.read_text(encoding="utf-8")

if "VENUE_TAG" not in text:
    text = text.replace(
        'STRATEGY_TAG = "smart-fast-v6"',
        'STRATEGY_TAG = "smart-fast-v6"\nVENUE_TAG = "Binance"',
        1,
    )

replacements = [
    (
        '        f"{title} · {asset_label(base)}",\n        f"Pague unos ${usd:.2f}",',
        '        f"[{VENUE_TAG}] {title} · {asset_label(base)}",\n        f"Pague unos ${usd:.2f}",',
    ),
    (
        '        f"{title} · {asset_label(base)}",\n        "Volvio dinero a USDT",',
        '        f"[{VENUE_TAG}] {title} · {asset_label(base)}",\n        "Volvio dinero a USDT",',
    ),
    (
        '            "META CUMPLIDA · duplicaste",',
        '            f"[{VENUE_TAG}] META CUMPLIDA · duplicaste",',
    ),
    (
        '            "AVISO · no se pudo completar la operacion",',
        '            f"[{VENUE_TAG}] AVISO · no se pudo completar la operacion",',
    ),
]

changed = 0
for old, new in replacements:
    if old in text:
        text = text.replace(old, new, 1)
        changed += 1
    else:
        print("MISS", repr(old[:60]))

# avoid double-prefix if re-run
while "[{VENUE_TAG}] [{VENUE_TAG}]" in text:
    text = text.replace("[{VENUE_TAG}] [{VENUE_TAG}]", "[{VENUE_TAG}]")

p.write_text(text, encoding="utf-8")
print("patched", changed, "has_tag", "VENUE_TAG" in text)
