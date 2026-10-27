---
name: codex-mcp-to-pi
description: Convert Codex MCP server entries from config.toml into Pi's native mcp.json and validate the connection. Use when migrating Codex MCP servers (including CCStudio/CCS servers) to Pi, or when Pi cannot see tools configured in Codex.
---

# Convert Codex MCP servers to Pi (native MCP)

Pi 1.0+ reads `~/.pi/agent/mcp.json` (user) and `.pi/mcp.json` (trusted project); it does **not** read Codex's TOML directly. Use the bundled `scripts/convert.py` (resolve relative to this skill directory). Use `uv run --no-project --python 3.11` for the bundled Python script (`tomllib` requires Python 3.11+). No Pi adapter or npm package is required.

1. Inspect the chosen Codex TOML source(s) and any existing Pi MCP config **before writing**. Codex may define servers in both `~/.codex/config.toml` and `<project>/.codex/config.toml`. Do not assume one contains the other. Check the project trust and local overrides before choosing the destination. Never copy credentials into a project config or publish either config.
2. Preview the changes first (prints server names only, not secret values):
   ```bash
   uv run --no-project --python 3.11 /home/andre/.agents/skills/codex-mcp-to-pi/scripts/convert.py --source .codex/config.toml --output ~/.pi/agent/mcp.json --dry-run
   ```
3. When satisfied, repeat without `--dry-run`. To include global Codex servers, add `--source ~/.codex/config.toml` before `--source .codex/config.toml` (later source wins on matching names). To keep project-specific servers scoped to the trusted project, choose `--output .pi/mcp.json` instead of the user file. Existing different Pi entries are **not** overwritten; after reviewing a conflict, use `--replace` to update matching names. Unrelated entries are kept. The script writes JSON with private (`0600`) permissions.
4. Run `pi mcp list` and inspect connected status and tools. A nonzero exit code, invalid entry, or disconnected server is not a successful migration. In an active Pi session run `/reload`, then `/mcp` (or start a new session). For deferred/codemode tools use `tool_search` or `codemode`/`searchTools()` as documented in Pi's MCP docs. The script sets `exposure: direct` for converted servers so their tools are visible without discovery; if a server has a large tool set, adjust exposure in mcp.json to `codemode` or `deferred` after validating.
5. Codex approval configuration (`default_tools_approval_mode`, `[mcp_servers.<name>.tools.*]`) does **not** become a Pi permission gate. Set up a Pi tool-call policy separately before invoking dangerous tools; do not claim approvals carried over. For CCS projects read the installed CCS `CCS.md` and follow its project build requirements before using CCS tools. Build only with the `ccs-project` server's `buildProject`, not shell make.

The converter handles stdio `command`/`args`/`env`/`cwd`, streamable HTTP `url`/`http_headers`/`env_http_headers`/`bearer_token_env_var`, and `enabled` and `tool_timeout_sec` (converted to Pi seconds). It warns about known Codex-only metadata and rejects unknown fields or invalid/mixed transports rather than guessing. Pi does not support legacy SSE. Environment variables in HTTP headers are kept as `${NAME}` references, not resolved by the script. Review any inline secrets in the source before migrating.
