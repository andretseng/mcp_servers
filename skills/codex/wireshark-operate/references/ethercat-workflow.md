# EtherCAT Capture and Evidence Workflow

Use this reference for EtherCAT troubleshooting, especially master discovery, boot-time traffic, WKC anomalies, and host-frame contamination.

## Capture timing

- Select the physical Ethernet interface connected to the EtherCAT segment; record its name.
- Start an unfiltered capture before CCS loads or runs the device when the question involves boot, reset, link, or discovery behavior.
- Run capture and CCS operations concurrently. The MCP capture call returns only after its duration or packet limit completes.
- Preserve the original capture. Apply display filters during analysis rather than filtering the raw capture at acquisition time.

## Useful display filters

Use the MCP display-filter resource when syntax is uncertain. Common EtherCAT filters are:

```text
eth.type == 0x88a4
eth.type != 0x88a4
ecat
ecatf
frame.number == N
```

Use `eth.type != 0x88a4` to identify non-EtherCAT host traffic before, during, or after the test. Do not call those frames evidence of an EtherCAT failure until their timing and role are established.

## Packet evidence

Start with bounded `wireshark_get_packet_list` output. Useful custom columns include:

```text
frame.number,frame.time_relative,eth.src,eth.dst,frame.len,ecatf.length,ecatf.type
```

Then confirm candidate frames with:

- `wireshark_get_packet_details` using the `eth`, `ecatf`, and `ecat` layers;
- `wireshark_get_packet_context` for packets before and after the candidate;
- `wireshark_get_packet_bytes` when the decoded tree is incomplete;
- `wireshark_extract_fields` or `wireshark_search_packets` for bounded, repeatable comparisons.

The EtherCAT dissector exposes datagram information under the `ecat` layer and frame information under `ecatf`. Field names can vary by Wireshark/dissector version. Do not invent a WKC or register-address field name: inspect packet details or the protocol-field resource first, then use the exact returned field.

For a register access such as address `0x0120`, identify candidate frames from decoded `ecat` datagram fields or raw bytes, and confirm command, ADP, ADO, datagram length, WKC, and frame number from packet-level evidence. Keep the register interpretation separate from the observation that a frame exists.

## Comparison pattern

For a master-discovery or boot comparison, preserve these slices:

1. all frames in the bounded capture;
2. `eth.type == 0x88a4` EtherCAT frames;
3. `eth.type != 0x88a4` non-EtherCAT frames;
4. candidate WKC-zero or target-register frames with surrounding context.

Report counts and exact frame numbers for each slice. A small number of non-EtherCAT frames is a fact to correlate with timing, not by itself a root cause.

## Raw evidence checklist

- capture path and readable ownership;
- interface and capture bounds;
- file metadata from `wireshark_get_file_info`;
- packet counts for each display-filter slice;
- exact fields, filters, and offsets/limits used;
- frame numbers, relative times, decoded datagram fields, and relevant bytes;
- dropped-packet information when the capture backend reports it;
- unresolved dissector or permission limitations.
