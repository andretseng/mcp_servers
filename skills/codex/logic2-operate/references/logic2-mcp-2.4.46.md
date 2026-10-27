# Logic2 MCP 2.4.46 reference

This reference records the current bundled Logic2 MCP contract used by `logic2-operate`. Treat tool responses from the running Logic2 instance as authoritative if a later Logic2 release changes the schema.

## Endpoint and tools

The official Logic2 MCP server listens on `http://127.0.0.1:10530` when enabled in Logic2 Automation settings. The exposed operations are:

- `get_devices`
- `start_capture`, `stop_capture`, `wait_capture`, `close_capture`
- `save_capture`, `load_capture`
- `add_analyzer`, `remove_analyzer`
- `add_high_level_analyzer`, `remove_high_level_analyzer`
- `export_raw_data_csv`, `export_raw_data_binary`
- `export_data_table_csv`, `legacy_export_analyzer`

Capture and analyzer IDs are generated per Logic2 session.

## Analyzer setting encoding

The 2.4.46 `add_analyzer` MCP schema is a map from setting names to objects. The handler reads these fields:

| Logic2 setting type | MCP value |
|---|---|
| `Channel` | `{ "numberValue": 0 }` |
| `Integer` | `{ "numberValue": 9600 }` |
| numeric `NumberList` | `{ "numberValue": 8 }` |
| dropdown `NumberList` by display text | `{ "stringValue": "8 Bits per Transfer (Standard)" }` |
| `Text` | `{ "stringValue": "..." }` |
| `Bool` | `{ "boolValue": false }` |

Primitive values such as `"Input Channel": 0` are rejected by 2.4.46 MCP validation. This differs from the older Automation API examples that show primitive maps.

Use exact UI setting names. Validated minimal examples:

```json
{"Input Channel":{"numberValue":0}}
```

```json
{"SDA":{"numberValue":0},"SCL":{"numberValue":1}}
```

```json
{
  "MOSI":{"numberValue":0},
  "MISO":{"numberValue":1},
  "Clock":{"numberValue":2},
  "Enable":{"numberValue":3},
  "Bits per Transfer":{"stringValue":"8 Bits per Transfer (Standard)"}
}
```

The channel value must be present in the capture's enabled digital-channel list. Adding an analyzer after capture completion is required.

## HLA and built-in measurement extensions

`add_high_level_analyzer` uses the same map shape for settings but only reads `numberValue` and `stringValue`. Its `extensionDirectory` must contain `extension.json`, and `hlaName` must be one of the extension names in that file.

The bundled Logic2 resources currently expose these built-in measurement packages:

| Directory | `extension.json` name | Extension names |
|---|---|---|
| `clock-statistics-measurement` | `Clock Stats` | `clockStats` |
| `voltage-statistics-measurement` | `Voltage RMS` | `voltageData` |
| `saleae-measurements-v2` | `Saleae Measurements V2` | `Voltage Measurements`, `Pulse Measurements`, `Time Measurements` |

In an AppImage session, resolve the live path with a read-only search such as:

```text
find /tmp/.mount_Logic*/usr/lib/logic/resources/builtInExtensions \\
  -maxdepth 2 -name extension.json -print
```

The mounted directory name changes each launch. If the AppImage is started with `--appimage-extract-and-run`, resolve the active extraction directory instead. Do not hardcode a stale temporary path.

The current MCP surface does not expose a generic measurement command. Use the built-in measurement extension only when its input model is compatible with the capture/analyzer and the MCP call succeeds; otherwise report the missing capability rather than silently switching to an external measurement implementation.

## Export rules

- `export_data_table_csv` requires at least one analyzer and an absolute filepath. Omit `analyzers` to include all analyzers, or pass objects such as `{ "analyzerId": 10003 }`.
- `export_raw_data_csv` and `export_raw_data_binary` require `analogDownsampleRatio` even for digital-only captures; use `1` for an un-downsampled raw probe.
- `legacy_export_analyzer` is for consumers that specifically require the legacy analyzer format.
- Save `.sal` before close when the capture must be reproduced. Close captures after export to release device and memory resources.

## Launcher diagnostics for Ubuntu/VMware

If the Logic2 AppImage cannot mount because `/dev/fuse` is unavailable, use `--appimage-extract-and-run`. On the current Ubuntu 22.04 VMware SVGA II environment, the tested desktop entry is:

```text
/usr/local/bin/logic2 --appimage-extract-and-run --no-sandbox --disable-gpu --in-process-gpu
```

`--disable-gpu` selects software rendering; `--in-process-gpu` avoids the separate Chromium GPU process crash observed with the VMware adapter. These flags affect launch/rendering only, not Logic2 capture or protocol-analyzer semantics.
