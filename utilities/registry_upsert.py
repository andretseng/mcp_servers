"""Atomic upsert of one entry into analysis_registry.json.

Cross-platform. Normalizes the registry so all PDF entries live under the
top-level "entries" object (migrating any stray root-level entries), then
inserts or replaces a single entry keyed by its virtual PDF name.

Usage:
    python3 registry_upsert.py <registry_path> <key> <json_value>

<json_value> is a JSON object string, e.g.
    '{"status":"DONE","module":"FSI",...}'

Writes via a temp file + os.replace for atomicity.
"""
import json
import os
import sys


def main():
    registry_path, key, value_json = sys.argv[1], sys.argv[2], sys.argv[3]
    value = json.loads(value_json)

    if os.path.exists(registry_path):
        with open(registry_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    if "entries" not in data or not isinstance(data.get("entries"), dict):
        # flat layout -> wrap everything into "entries"
        data = {"entries": dict(data)}

    entries = data["entries"]

    # migrate any stray root-level PDF entries (everything except "entries")
    for k in [k for k in list(data.keys()) if k != "entries"]:
        if isinstance(data[k], dict):
            entries.setdefault(k, data[k])
        del data[k]

    entries[key] = value

    tmp = registry_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, registry_path)
    print("OK: upserted", key, "| total entries:", len(entries))


if __name__ == "__main__":
    main()
