#!/usr/bin/env bash
# One-command repro: ./run.sh -> out/evidence.txt (Linux; Bun at $BUN)
set -u
cd "$(dirname "$0")"
BUN=${BUN:-$HOME/.bun/bin/bun}
rm -rf out; mkdir -p out
OUT=out/evidence.txt
exec > >(tee $OUT) 2>&1
echo "bun $($BUN --version) | $(uname -sr) | $(ldd --version | head -1)"
$BUN build --compile spike.ts --outfile spike-bin >/dev/null || { echo "compile failed"; exit 1; }
waitline() { for _ in $(seq 50); do [ -s "$1" ] && return; sleep 0.1; done; }
for launcher in bun-file compiled; do
  [ $launcher = bun-file ] && S="$BUN spike.ts" || S=./spike-bin
  L=$PWD/out/$launcher.lock
  echo; echo "=== $launcher ==="
  echo "-- 1. exclusion + errno"
  $S hold $L > out/a.log & A=$!; waitline out/a.log; cat out/a.log
  $S try $L
  echo "-- same-process second open (fresh file)"
  $S same $PWD/out/$launcher.same.lock
  echo "-- 2. SIGKILL holder $A, then new process"
  kill -9 $A; wait $A 2>/dev/null
  $S try $L
  echo "-- 3. holder spawns child, holder SIGKILLed"
  L3=$PWD/out/$launcher.spawn.lock
  $S hold $L3 spawn > out/d.log & D=$!; waitline out/d.log; cat out/d.log
  C=$(grep -o '"child":[0-9]*' out/d.log | cut -d: -f2)
  echo "child $C fds: $(ls -l /proc/$C/fd | awk 'NR>1{printf "%s->%s ", $9, $11}')"
  echo "child $C fds -> lockfile: $(ls -l /proc/$C/fd 2>/dev/null | grep -c "$L3")"
  $S try $L3 | sed 's/^/while holder alive: /'
  kill -9 $D; wait $D 2>/dev/null
  echo "holder killed; child $C alive: $(kill -0 $C 2>/dev/null && echo yes || echo no)"
  $S try $L3 | sed 's/^/after holder kill: /'
  kill -9 $C 2>/dev/null
done
