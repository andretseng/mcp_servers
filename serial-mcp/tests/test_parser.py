"""Tests for FrameDecoder.

These verify *intent*, not just "returns something": the decoder must recover
the exact float values the firmware sent, must not be fooled by 0xAA 0x55
appearing inside a payload, and must re-lock after a byte is lost on the wire.
"""

import struct

from serial_mcp.parser import SYNC0, SYNC1, FrameDecoder


def frame(d1: float, d2: float) -> bytes:
    return bytes([SYNC0, SYNC1]) + struct.pack("<ff", d1, d2)


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_decodes_clean_stream():
    samples = [(0.1, 1.0), (-3.14159, 2.71828), (0.0, -42.5)]
    stream = b"".join(frame(*s) for s in samples)
    out = FrameDecoder().feed(stream)
    assert len(out) == len(samples)
    for got, want in zip(out, samples):
        assert approx(got[0], want[0]) and approx(got[1], want[1])


def test_garbage_prefix_is_skipped():
    stream = b"\x00\x11\xaa\x22junk" + frame(1.5, 2.5) + frame(3.5, 4.5)
    out = FrameDecoder().feed(stream)
    # First confirmable boundary onward must decode exactly.
    assert out[-2] == struct.unpack("<ff", struct.pack("<ff", 1.5, 2.5))
    assert approx(out[-1][0], 3.5) and approx(out[-1][1], 4.5)


def test_aa55_inside_payload_does_not_cause_false_lock():
    # A float whose bytes contain 0xAA 0x55. struct: find a value with those bytes.
    # 0x000055AA little-endian as float32:
    tricky = struct.unpack("<f", bytes([0xAA, 0x55, 0x00, 0x00]))[0]
    samples = [(tricky, tricky), (1.0, 2.0), (3.0, 4.0)]
    stream = b"".join(frame(*s) for s in samples)
    out = FrameDecoder().feed(stream)
    # Must decode all three frames correctly, not desync on the embedded AA55.
    assert len(out) == 3
    assert approx(out[1][0], 1.0) and approx(out[2][1], 4.0)


def test_resync_after_dropped_byte():
    # Drop one byte mid-stream so alignment is lost. Because the frame has no
    # checksum/length, the decoder cannot detect the loss immediately and may
    # emit ONE mis-decoded frame in the damaged region; it then needs two
    # consecutive valid headers to re-lock. We assert it recovers to correct
    # values on the clean frames that follow (the always-present "next frames"
    # of a continuous stream).
    good = frame(1.0, 2.0) + frame(3.0, 4.0)
    corrupted = good[:-1]
    tail = frame(5.0, 6.0) + frame(7.0, 8.0) + frame(9.0, 10.0) + frame(11.0, 12.0)
    dec = FrameDecoder()
    out = dec.feed(corrupted + tail)
    assert approx(out[0][0], 1.0)  # first clean frame survives
    assert dec.locked  # re-locked
    assert approx(out[-1][0], 11.0) and approx(out[-1][1], 12.0)  # recovered tail
    # And the clean post-corruption frames are all present, in order.
    recovered = [s for s in out if approx(s[0], 7.0) or approx(s[0], 9.0) or approx(s[0], 11.0)]
    assert len(recovered) == 3


def test_chunked_feed_across_frame_boundary():
    stream = frame(1.0, 2.0) + frame(3.0, 4.0) + frame(5.0, 6.0)
    dec = FrameDecoder()
    out = []
    for i in range(0, len(stream), 3):  # awkward 3-byte chunks
        out += dec.feed(stream[i : i + 3])
    assert len(out) == 3
    assert approx(out[2][0], 5.0) and approx(out[2][1], 6.0)


def test_garbage_only_never_locks_and_stays_bounded():
    dec = FrameDecoder(max_buffer=4096)
    for _ in range(100):
        out = dec.feed(b"\x01\x02\x03\x04\x05\x06\x07\x08" * 64)
        assert out == []
    assert not dec.locked
    assert len(dec._buf) <= 4096
