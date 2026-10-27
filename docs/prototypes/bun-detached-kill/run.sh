#!/usr/bin/env bash
# One-command repro: ./run.sh  -> prints results matrix (also out/matrix.txt)
set -u
cd "$(dirname "$0")"
BUN=${BUN:-$HOME/.bun/bin/bun}
rm -rf out; mkdir -p out
$BUN build --compile spike.ts --outfile spike-bin >/dev/null 2>&1 || { echo "compile failed"; exit 1; }
OUT=out/matrix.txt
fmt='%-8s %-10s %-9s %-7s %-6s | %-7s %-8s %-6s %-6s %-5s %-5s %-6s %-5s\n'
printf "$fmt" launcher kind signal second clean handler escalate final guard force exit tree files > $OUT
cat $OUT
n=0
# case: launcher kind target second(none|pid) cleanupMs
run_case() {
  local launcher=$1 kind=$2 target=$3 second=$4 cleanup=$5
  n=$((n+1)); local token=$((8000+n)) log=out/run$n.log wd=$PWD/out/wd$n
  mkdir -p $wd; : > $log
  case $launcher in
    bun-file) cmd="$BUN spike.ts"; pat="^$cmd $kind $token " ;;
    compiled) cmd="./spike-bin"; pat="^$cmd $kind $token " ;;
  esac
  # setsid -f -w: Bun becomes its own session/group leader; setsid reports its exit code.
  ( setsid -f -w $cmd $kind $token $log $wd $cleanup >/dev/null 2>&1; echo $? > out/run$n.exit ) &
  sleep 1
  local tp; tp=$(pgrep -f "$pat" | head -1)
  local sig=${target#*:}
  if [ "${target%%:*}" = pid ]; then kill -$sig $tp; else kill -$sig -- -$tp; fi  # pgrp: Ctrl-C simulation
  if [ "$second" != none ]; then sleep $second; kill -$sig $tp 2>/dev/null; fi
  for _ in $(seq 40); do [ -s out/run$n.exit ] && break; sleep 0.1; done
  local tree=dead; pgrep -f "sleep $token" >/dev/null && tree=ALIVE
  local files=gone; { [ -e $wd/worktree ] || [ -e $wd/hook.json ]; } && files=LEFT
  pgrep -f "sleep $token" | xargs -r kill -9 2>/dev/null; kill -9 $tp 2>/dev/null
  local ex; ex=$(cat out/run$n.exit 2>/dev/null || echo "?")
  yn() { grep -q "$1" $log && echo yes || echo no; }
  printf "$fmt" $launcher $kind $target $second $cleanup $(yn HANDLER) $(yn ESCALATE) $(yn FINALLY_DONE) $(yn GUARD) $(yn FORCE) $ex $tree $files | tee -a $OUT
}
for launcher in bun-file compiled; do
  for kind in direct grandchild stubborn; do
    for target in pid:INT pid:TERM pgrp:INT pgrp:TERM; do run_case $launcher $kind $target none 0; done
  done
  for target in pid:INT pid:TERM; do
    run_case $launcher grandchild $target 0.5 3000   # 2nd signal during slow finally cleanup
    run_case $launcher stubborn $target 0.3 0        # 2nd signal during SIGKILL grace period
  done
done
echo; echo "matrix saved to $OUT"
