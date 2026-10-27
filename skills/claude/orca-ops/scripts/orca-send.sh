#!/usr/bin/env bash
# Safe send into an Orca terminal pane.
#   orca-send.sh shell <handle> '<command>'   run a command in a plain shell pane
#   orca-send.sh agent <handle> '<prompt>'    give a prompt to an agent (Pi, Codex, Claude...) running in a pane
# Refuses (exit 2) instead of guessing. Reads the live screen, not the terminal-list preview.
set -euo pipefail

mode="${1:-}"; handle="${2:-}"; text="${3:-}"
[ -n "$mode" ] && [ -n "$handle" ] && [ -n "$text" ] || { echo "usage: $0 shell|agent <handle> <text>" >&2; exit 64; }

if [ -n "${ORCA_CLI_COMMAND:-}" ]; then CLI="$ORCA_CLI_COMMAND"
elif [ "$(uname)" = Linux ] && command -v orca-ide >/dev/null; then CLI=orca-ide   # bare `orca` can be the GNOME screen reader
else CLI=orca; fi

refuse() { echo "REFUSED: $*" >&2; exit 2; }

[ "$handle" != "${ORCA_TERMINAL_HANDLE:-}" ] || refuse "that is your own terminal"

show=$("$CLI" terminal show --terminal "$handle" --json) || refuse "cannot read terminal $handle"
fields=$(printf '%s' "$show" | python3 -c '
import json,sys
t=json.load(sys.stdin)["result"]["terminal"]
print(int(bool(t.get("connected"))), int(bool(t.get("writable"))), t.get("agentIdentity") or "-")')
read -r connected writable agent <<<"$fields"
[ "$connected" = 1 ] && [ "$writable" = 1 ] || refuse "terminal not connected/writable"

case "$mode" in
  shell)
    [ "$agent" = "-" ] || refuse "an agent ($agent) is running in that pane; use agent mode or another pane"
    last=$("$CLI" terminal read --terminal "$handle" --json | python3 -c '
import json,sys
tail=json.load(sys.stdin)["result"]["terminal"].get("tail") or []
lines=[l.rstrip() for l in tail if l.strip()]
print(lines[-1] if lines else "")')
    printf '%s' "$last" | grep -Eq '[$#%] ?$' || refuse "no shell prompt on the last screen line: [$last]"
    "$CLI" terminal send --terminal "$handle" --text "$text" --enter --json ;;
  agent)
    [ "$agent" != "-" ] || refuse "no agent detected in that pane; use shell mode or launch one with: $CLI agent launch"
    "$CLI" terminal send --terminal "$handle" --text "$text" --enter --wait-submit 10 --json ;;
  *) echo "mode must be shell or agent" >&2; exit 64 ;;
esac
