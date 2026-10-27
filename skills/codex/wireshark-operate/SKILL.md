---
name: wireshark-operate
description: Operate the Wireshark MCP server for live captures, pcap/pcapng inspection, EtherCAT troubleshooting, packet evidence collection, and capture coordination with CCS tests. Use whenever Codex needs Wireshark MCP tools, network-interface capture, packet-level diagnosis, or evidence from Ethernet traffic; prefer the native MCP tools and resources over shell tshark/dumpcap commands.
---

# Wireshark Operate

Act as the Wireshark executor. Own MCP tool selection, capture lifecycle, packet-evidence collection, and artifact handling. Keep protocol interpretation and report conclusions evidence-based; use the package-provided `wireshark-traffic-analysis` guidance when a full investigation is needed.

## Ownership boundary

- Own Wireshark MCP tools, MCP resources, interface selection, live-capture timing, pcap paths, filters, pagination, and raw packet evidence.
- Keep CCStudio operations under `$ccs-operate`. For a combined hardware test, coordinate the two skills but do not call CCS MCP tools from this skill's workflow.
- Do not decide firmware correctness, hardware root cause, or safety authorization. Return observations, exact evidence, and unresolved engineering questions for review.
- Do not require or launch the Wireshark GUI. The MCP server uses `tshark` for analysis and prefers `dumpcap` for live capture.

## Tool and resource priority

1. Confirm the MCP surface with `wireshark_get_capabilities` before a new workflow. For live capture, also call `wireshark_list_interfaces`.
2. Use the MCP resources when syntax or field names are uncertain:
   - `wireshark://guide/usage`
   - `wireshark://reference/display-filters`
   - `wireshark://reference/protocol-fields`
   - `wireshark://capabilities`
3. For an existing capture, start with `wireshark_open_file`, then use `wireshark_quick_analysis` or the focused statistics tools.
4. Prefer `wireshark_get_packet_list`, `wireshark_extract_fields`, `wireshark_get_packet_details`, `wireshark_get_packet_context`, `wireshark_get_packet_bytes`, and `wireshark_search_packets` for evidence.
5. Use `offset` and `limit` to paginate. Do not treat the first page of a large capture as representative.
6. Do not use deprecated `wireshark_read_packets` when the newer packet-list/detail tools are available.
7. Use `wireshark_analyze_protocol` only for its advertised protocol enum. It is not the EtherCAT workflow; use the generic packet tools and the EtherCAT reference instead.

## Existing-capture workflow

1. Resolve the exact readable `.pcap` or `.pcapng` path. Check ownership and permissions before calling MCP; do not silently use a root- or `nobody`-owned mode-600 file that the MCP process cannot read.
2. Call `wireshark_open_file`.
3. Establish capture-wide context with `wireshark_quick_analysis`, `wireshark_stats_protocol_hierarchy`, and, when relevant, endpoints or conversations.
4. Narrow with a display filter and a bounded packet list. Use custom columns for the exact fields needed.
5. Confirm every material lead with packet details and surrounding context. Include frame numbers, timestamps, fields, filters, and artifact paths in the handoff.
6. Keep observations separate from inferences. Label a conclusion as confirmed, likely, possible, or unresolved.

## Live capture workflow

Treat live capture as an open-world, file-writing operation.

1. Call `wireshark_get_capabilities` and `wireshark_list_interfaces`.
2. Select an explicit interface. Use `any` only when its broader vantage point is intentional and recorded.
3. Choose a bounded `duration_seconds` or `packet_count` and a user-readable output path. Preserve the raw capture.
4. For boot, reset, link, or discovery timing questions, capture unfiltered. Apply display filters after capture; do not use a capture filter that would hide non-EtherCAT frames.
5. When the test includes CCS load/run, start `wireshark_capture` and the `$ccs-operate` execution concurrently. `wireshark_capture` waits for its duration or packet limit, so a sequential call can miss the very frames being investigated. If the runtime cannot issue the calls concurrently, report that limitation rather than claiming capture-first evidence.
6. After capture completion, call `wireshark_get_file_info`, then analyze the saved file through MCP.
7. Record the interface, capture bounds, raw path, packet count, drop information if reported, and exact display filters used.

For EtherCAT capture sequencing and field guidance, read [references/ethercat-workflow.md](references/ethercat-workflow.md).

## Permissions and fallback

- Use MCP first whenever its corresponding tool is exposed and working.
- Fall back to shell `tshark`/`dumpcap` only when MCP is unavailable, cannot access the artifact, or returns a reproducible tool error. State the reason and preserve the same raw artifact/evidence contract.
- Do not add `sudo`, change capture-file ownership, or alter interface state merely to bypass an error. Report the exact permission failure and request the smallest authorized remediation.
- Treat `wireshark_capture`, `wireshark_filter_save`, `wireshark_extract_frames`, `wireshark_merge_pcaps`, editcap operations, object export, and text2pcap import as writes. Bound them and avoid overwriting source captures.
- Prefer a user-readable project or `/tmp` artifact path over a privileged capture path. Reopen the resulting file through MCP before interpreting it.

## Evidence handoff

Return a compact handoff with:

```text
Plan: <capture or analysis goal>
MCP preflight: <capabilities, interface, and path checks>
Execution: <MCP calls in order; note concurrent CCS execution if used>
Raw evidence: <pcap path, metadata, filters, fields, frame numbers, timestamps>
Observation: <what the tools directly show>
Inference: <confirmed | likely | possible | unresolved, with rationale>
Status: COMPLETE | ABORT | NEED-CLARIFICATION
Review handoff: <engineering questions or next exact filter/tool call>
```
