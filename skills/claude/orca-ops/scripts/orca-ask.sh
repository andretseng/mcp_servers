#!/usr/bin/env bash
# Ask an agent in another Orca pane a question and get the COMPLETE answer back.
#   orca-ask.sh <handle> '<question>' [timeout_seconds=180]
# Screen reads only show the visible rows of a pane, so the agent is told to write its answer to a file;
# this script waits for the agent to go idle, then prints that file. Exit codes:
#   0 answer printed   2 refused by orca-send.sh   3 agent went idle without writing the file   4 timeout
set -euo pipefail

handle="${1:-}"; question="${2:-}"; timeout="${3:-180}"
[ -n "$handle" ] && [ -n "$question" ] || { echo "usage: $0 <handle> '<question>' [timeout_seconds]" >&2; exit 64; }

here="$(cd "$(dirname "$0")" && pwd)"
if [ -n "${ORCA_CLI_COMMAND:-}" ]; then CLI="$ORCA_CLI_COMMAND"
elif [ "$(uname)" = Linux ] && command -v orca-ide >/dev/null; then CLI=orca-ide
else CLI=orca; fi

dir="$HOME/.cache/orca-ops"; mkdir -p "$dir"
ans="$dir/answer-$(date +%s)-$$.md"
rm -f "$ans"

"$here/orca-send.sh" agent "$handle" "$question

Write your complete final answer to the file $ans with your write tool (plain text or markdown). Then reply with only the word DONE." >/dev/null || exit $?

start=$(date +%s); idle_no_file=0
while :; do
  "$CLI" terminal wait --terminal "$handle" --for tui-idle --timeout-ms 15000 --json >/dev/null 2>&1 || true
  if [ -s "$ans" ]; then
    "$CLI" terminal wait --terminal "$handle" --for tui-idle --timeout-ms 60000 --json >/dev/null 2>&1 || true
    cat "$ans"; exit 0
  fi
  now=$(date +%s)
  [ $((now - start)) -lt "$timeout" ] || { echo "TIMEOUT after ${timeout}s, no answer file ($ans)" >&2; exit 4; }
  # after a 20 s grace (the agent may not have started its turn yet), idle with no file three times in a row = it gave up
  if [ $((now - start)) -gt 20 ]; then
    idle_no_file=$((idle_no_file + 1))
    [ "$idle_no_file" -lt 3 ] || { echo "agent is idle but wrote no answer file ($ans); read the pane to see why" >&2; exit 3; }
  fi
  sleep 2
done
