#!/usr/bin/env bash
# One-command repro: ./run.sh  -> prints results matrix (also out/matrix.txt)
set -u
cd "$(dirname "$0")"
BUN=${BUN:-$HOME/.bun/bin/bun}
mkdir -p out
$BUN build --compile spike.ts --outfile spike-bin >/dev/null 2>&1 || { echo "compile failed"; exit 1; }
OUT=out/matrix.txt
fmt='%-9s %-10s %-10s %-9s | %-8s %-8s %-9s %-6s %-6s\n'
printf "$fmt" launcher mode child signal handler finally child-fate exit alive > $OUT
cat $OUT
killpat() { pgrep -f "$1" | xargs -r kill -9 2>/dev/null; }
n=0
for launcher in bun-run bun-file compiled; do
for mode in bun-sync node-sync bun-async; do
for kind in direct grandchild; do
for target in pid:INT pid:TERM pgrp:INT pgrp:TERM; do
  n=$((n+1)); token=$((7000+n)); log=out/run$n.log; : > $log; rm -f out/run$n.exit
  case $launcher in
    bun-run)  cmd="$BUN run spike -- $mode $kind $token $log"; pat="bun run spike -- $mode $kind $token" ;;
    bun-file) cmd="$BUN spike.ts $mode $kind $token $log"; pat="bun spike.ts $mode $kind $token" ;;
    compiled) cmd="./spike-bin $mode $kind $token $log"; pat="spike-bin $mode $kind $token" ;;
  esac
  setsid bash -c "$cmd; echo \$? > out/run$n.exit" >/dev/null 2>&1 &
  sleep 1.5
  tp=$(pgrep -f "$pat" | grep -v "^$(pgrep -f "bash -c .*$log" | head -1)$" | head -1)
  if [ "${target%%:*}" = pid ]; then
    kill -${target#*:} $tp 2>/dev/null          # the bun process (wrapper for bun-run)
  else
    pgid=$(ps -o pgid= -p $tp | tr -d ' ')
    kill -${target#*:} -- -$pgid 2>/dev/null   # whole group, like terminal Ctrl-C
  fi
  sleep 1.5
  alive=no; kill -0 $tp 2>/dev/null && alive=yes
  h=$(grep -c HANDLER $log); f=$(grep -c FINALLY $log)
  if pgrep -f "sleep $token" >/dev/null; then fate=ORPHANED; else fate=killed; fi
  killpat "sleep $token"; killpat "$pat"
  sleep 0.2
  ex=$(cat out/run$n.exit 2>/dev/null || echo "?")
  printf "$fmt" $launcher $mode $kind $target $([ $h -gt 0 ] && echo yes || echo no) $([ $f -gt 0 ] && echo yes || echo no) $fate $ex $alive | tee -a $OUT
done; done; done; done
echo; echo "matrix saved to $OUT"
