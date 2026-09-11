#!/bin/bash
set -euo pipefail
echo "=== BEFORE ==="
free -h
BEFORE_USED=$(free -b | awk '/Mem:/{print $3}')
BEFORE_AVAIL=$(free -b | awk '/Mem:/{print $7}')
BEFORE_SWAP=$(free -b | awk '/Swap:/{print $3}')
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | grep -i webui || echo "(no webui stats)"
echo
echo "=== STOPPING Open WebUI ==="
docker stop synaptika-chat-webui synaptika-trade-open-webui-1
sleep 4
echo
echo "=== AFTER ==="
free -h
AFTER_USED=$(free -b | awk '/Mem:/{print $3}')
AFTER_AVAIL=$(free -b | awk '/Mem:/{print $7}')
AFTER_SWAP=$(free -b | awk '/Swap:/{print $3}')
docker ps -a --filter name=webui --format '{{.Names}}  {{.Status}}'
echo
python3 - <<PY
bu,ba,bs=int("$BEFORE_USED"),int("$BEFORE_AVAIL"),int("$BEFORE_SWAP")
au,aa,ass=int("$AFTER_USED"),int("$AFTER_AVAIL"),int("$AFTER_SWAP")
def mb(x): return x/1024/1024
print(f"RAM used:      {mb(bu):.0f} -> {mb(au):.0f} MB   (delta {mb(bu-au):+.0f} MB)")
print(f"RAM available: {mb(ba):.0f} -> {mb(aa):.0f} MB   (delta {mb(aa-ba):+.0f} MB)")
print(f"Swap used:     {mb(bs):.0f} -> {mb(ass):.0f} MB   (delta {mb(bs-ass):+.0f} MB)")
print(f"Freed vs used+swap approx: {mb((bu+bs)-(au+ass)):.0f} MB")
PY
echo
echo "=== remaining docker mem ==="
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}'
