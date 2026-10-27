#!/usr/bin/env bash
# One-command repro: ./run.sh  -> bun test output (saved to evidence.txt). Needs bun on PATH or ~/.bun/bin.
# Regenerate the skeleton from the real Python sources with: python gen_skeleton.py
set -u
cd "$(dirname "$0")"
export PATH="$HOME/.bun/bin:$PATH"
bun install --frozen-lockfile >/dev/null 2>&1 || bun install >/dev/null
{
  echo "bun $(bun --version), dependency-cruiser $(bun -e 'console.log(require("dependency-cruiser/package.json").version)'), typescript $(bun -e 'console.log(require("typescript/package.json").version)')"
  bun test 2>&1
  status=$?
  rm -rf out/cases
  exit $status
} | tee evidence.txt
exit "${PIPESTATUS[0]}"
