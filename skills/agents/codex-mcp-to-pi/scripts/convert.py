#!/usr/bin/env python3
"""Convert Codex MCP TOML entries to Pi's native MCP JSON config."""

import argparse
import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9_-]+$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
STDIO = {"command", "args", "env", "cwd"}
HTTP = {"url", "http_headers", "env_http_headers", "bearer_token_env_var"}
COMMON = {"enabled", "tool_timeout_sec"}
CODEX_ONLY = {"__comment", "default_tools_approval_mode", "tools", "startup_timeout_sec", "enabled_tools", "disabled_tools"}


def convert(name, entry):
    if not NAME.fullmatch(name) or not isinstance(entry, dict):
        raise ValueError(f"invalid MCP server name or entry: {name!r}")
    unknown = set(entry) - STDIO - HTTP - COMMON - CODEX_ONLY
    if unknown:
        raise ValueError(f"{name}: unsupported fields: {', '.join(sorted(unknown))}")
    ignored = set(entry) & CODEX_ONLY
    if ignored:
        print(f"warning: {name}: Codex-only fields not migrated: {', '.join(sorted(ignored))}", file=sys.stderr)
    has_command = "command" in entry
    has_url = "url" in entry
    if has_command == has_url:
        raise ValueError(f"{name}: exactly one of command or url is required")
    incompatible = (HTTP if has_command else STDIO) & set(entry)
    if incompatible:
        raise ValueError(f"{name}: mixed transport fields: {', '.join(sorted(incompatible))}")
    result = {}
    if has_command:
        if not isinstance(entry["command"], str) or not entry["command"]:
            raise ValueError(f"{name}: command must be a nonempty string")
        result["command"] = entry["command"]
        for key in ("args", "env", "cwd"):
            if key in entry:
                result[key] = entry[key]
        if not isinstance(result.get("args", []), list) or not all(isinstance(x, str) for x in result.get("args", [])):
            raise ValueError(f"{name}: args must be strings")
        if not isinstance(result.get("env", {}), dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in result.get("env", {}).items()):
            raise ValueError(f"{name}: env must map strings to strings")
        if "cwd" in result and not isinstance(result["cwd"], str):
            raise ValueError(f"{name}: cwd must be a string")
    else:
        if not isinstance(entry["url"], str) or not entry["url"].startswith(("http://", "https://")):
            raise ValueError(f"{name}: url must be an HTTP(S) URL")
        result["url"] = entry["url"]
        headers = entry.get("http_headers", {})
        env_headers = entry.get("env_http_headers", {})
        if not isinstance(headers, dict) or not isinstance(env_headers, dict):
            raise ValueError(f"{name}: headers must be tables")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ValueError(f"{name}: http_headers must map strings to strings")
        if not all(isinstance(k, str) and isinstance(v, str) and ENV_NAME.fullmatch(v) for k, v in env_headers.items()):
            raise ValueError(f"{name}: env_http_headers must map header names to environment variable names")
        if set(headers) & set(env_headers):
            raise ValueError(f"{name}: duplicate HTTP header")
        headers = {**headers, **{k: '${' + v + '}' for k, v in env_headers.items()}}
        if "bearer_token_env_var" in entry:
            token_var = entry["bearer_token_env_var"]
            if not isinstance(token_var, str) or not ENV_NAME.fullmatch(token_var) or "Authorization" in headers:
                raise ValueError(f"{name}: invalid bearer_token_env_var or duplicate Authorization header")
            headers["Authorization"] = "Bearer ${" + token_var + "}"
        if headers:
            result["headers"] = headers
    if "enabled" in entry:
        if not isinstance(entry["enabled"], bool):
            raise ValueError(f"{name}: enabled must be a boolean")
        result["enabled"] = entry["enabled"]
    if "tool_timeout_sec" in entry:
        timeout = entry["tool_timeout_sec"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout < 1e9:
            raise ValueError(f"{name}: tool_timeout_sec must be a positive finite number")
        result["timeout"] = timeout
    result["exposure"] = "direct"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True, help="Codex config.toml (repeatable; later wins)")
    parser.add_argument("--output", type=Path, required=True, help="Pi mcp.json path")
    parser.add_argument("--dry-run", action="store_true", help="validate and print names without writing")
    parser.add_argument("--replace", action="store_true", help="replace existing entries with the same name")
    args = parser.parse_args()
    converted = {}
    for source in args.source:
        with source.expanduser().open("rb") as f:
            servers = tomllib.load(f).get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise ValueError(f"{source}: mcp_servers must be a table")
        for name, entry in servers.items():
            converted[name] = convert(name, entry)
    if not converted:
        raise ValueError("no Codex MCP servers found")
    output = args.output.expanduser()
    existing = json.loads(output.read_text()) if output.exists() else {"mcpServers": {}}
    if not isinstance(existing, dict) or not isinstance(existing.get("mcpServers"), dict):
        raise ValueError(f"{output}: expected JSON object with mcpServers object")
    target = existing["mcpServers"]
    normalized = {name.replace("-", "_"): name for name in target}
    for name, entry in converted.items():
        alias = normalized.get(name.replace("-", "_"))
        if alias is not None and (alias != name or target[alias] != entry) and not args.replace:
            raise ValueError(f"{name}: conflicts with existing Pi server {alias!r}; review and use --replace")
        if alias is not None and alias != name:
            del target[alias]
        target[name] = entry
        normalized[name.replace("-", "_")] = name
    print(("Would update" if args.dry_run else "Updating") + f" {output}: {', '.join(sorted(converted))}")
    if args.dry_run:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".mcp-", dir=output.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(existing, f, indent=2)
            f.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, output)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
