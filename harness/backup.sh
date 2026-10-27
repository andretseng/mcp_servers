#!/usr/bin/env bash
# Snapshot agent skills and thin-harness config into ~/tools (mirror, no dated copies; history = git in ~/tools).
# Symlinks are dereferenced so the snapshot is self-contained. Secrets: none of these files hold keys
# (models.json / opencode.json point at ~/.config/omlx/key); auth.json is deliberately not copied.
set -euo pipefail

T="$HOME/tools"
RS=(rsync -aL --delete --exclude='__pycache__' --exclude='node_modules' --exclude='*.bak*' --exclude='.venv')

# An empty or missing source is skipped: --delete would otherwise wipe the only remaining copy.
sync_dir() {
  if [ -z "$(ls -A "$1" 2>/dev/null)" ]; then echo "SKIP empty/missing $1 (backup at $2 kept)" >&2; return 0; fi
  mkdir -p "$2"; "${RS[@]}" "$1/" "$2/"; echo "dir  $1 -> $2"
}
sync_files() { # dest src...
  local dest="$1"; shift; mkdir -p "$dest"
  for f in "$@"; do
    if [ -e "$f" ]; then "${RS[@]}" "$f" "$dest/"; echo "file $f -> $dest/"; else echo "MISSING $f" >&2; fi
  done
}

# Skills, one directory per owner
sync_dir "$HOME/.claude/skills" "$T/skills/claude"
sync_dir "$HOME/.codex/skills"  "$T/skills/codex"
# ~/.agents/skills was emptied 2026-10-27; skills/agents is now the only copy of those 7 skills, so it must not be
# mirrored with --delete from the (empty) source. Only the instruction file is refreshed here.
sync_files "$T/skills/agents" "$HOME/.agents/AGENTS.md"
sync_dir   "$HOME/.agents/skills/orca-ops" "$T/skills/agents/orca-ops"   # the one live shared skill (2026-10-27)

# Harness (instructions, extensions/plugins, settings)
sync_files "$T/harness/agents-shared" "$HOME/.agents/AGENTS.md"
sync_dir   "$HOME/.agents/agents-md" "$T/harness/agents-shared/agents-md"
sync_dir   "$HOME/.agents/bin"       "$T/harness/agents-shared/bin"
sync_dir   "$HOME/.agents/rules"     "$T/harness/agents-shared/rules"

sync_files "$T/harness/pi" "$HOME/.pi/agent/AGENTS.md" "$HOME/.pi/agent/CLAUDE.md" \
  "$HOME/.pi/agent/settings.json" "$HOME/.pi/agent/mcp.json" "$HOME/.pi/agent/models.json"
sync_dir   "$HOME/.pi/agent/extensions" "$T/harness/pi/extensions"
sync_dir   "$HOME/.pi/agent/bin"        "$T/harness/pi/bin"

sync_files "$T/harness/opencode" "$HOME/.config/opencode/AGENTS.md" "$HOME/.config/opencode/opencode.json" \
  "$HOME/.config/opencode/service.json" "$HOME/.config/opencode/cli.json" "$HOME/.config/opencode/tui.json"
sync_dir   "$HOME/.config/opencode/plugins" "$T/harness/opencode/plugins"

sync_files "$T/harness/codex" "$HOME/.codex/AGENTS.md" "$HOME/.codex/config.toml" "$HOME/.codex/hooks.json"
sync_dir   "$HOME/.codex/rules" "$T/harness/codex/rules"

sync_files "$T/harness/claude" "$HOME/.claude/CLAUDE.md" "$HOME/.claude/settings.json"
sync_dir   "$HOME/.claude/hooks" "$T/harness/claude/hooks"

echo "done"
