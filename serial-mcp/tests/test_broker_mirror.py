"""Mirror plumbing test: bytes written to the mirror are readable via the
symlinked slave path, exactly as serialplot would open it. No serial hardware."""

import os
import tempfile

from serial_mcp.broker import Broker


def test_mirror_roundtrip():
    link = os.path.join(tempfile.gettempdir(), "ttyDRONE_test")
    b = Broker()
    try:
        returned = b.start_mirror(link)
        assert returned == link
        assert os.path.islink(link)

        # serialplot side: open the symlinked slave for reading.
        reader = os.open(link, os.O_RDONLY | os.O_NONBLOCK)
        try:
            payload = bytes([0xAA, 0x55, 1, 2, 3, 4, 5, 6, 7, 8])
            b._mirror_write(payload)
            got = os.read(reader, 64)
            assert got == payload
        finally:
            os.close(reader)
    finally:
        b.stop_mirror()
    assert not os.path.exists(link)


def test_mirror_persists_across_detach():
    # detach() must leave the PTY mirror up so the virtual port stays openable
    # for the whole server lifetime; only stop_mirror() (atexit) tears it down.
    link = os.path.join(tempfile.gettempdir(), "ttyDRONE_persist")
    b = Broker()
    try:
        b.start_mirror(link)
        assert os.path.islink(link)

        b.detach()  # no physical port attached; should not touch the mirror
        assert os.path.islink(link)
        assert b._mirror_fd is not None

        # Still functional: a write round-trips after detach.
        reader = os.open(link, os.O_RDONLY | os.O_NONBLOCK)
        try:
            payload = bytes([0xAA, 0x55, 9, 8, 7, 6, 5, 4, 3, 2])
            b._mirror_write(payload)
            assert os.read(reader, 64) == payload
        finally:
            os.close(reader)
    finally:
        b.stop_mirror()
    assert not os.path.exists(link)


def test_mirror_write_with_no_reader_does_not_raise():
    # PTY buffer fills when nothing reads it; writes must drop, never block/raise.
    link = os.path.join(tempfile.gettempdir(), "ttyDRONE_noreader")
    b = Broker()
    try:
        b.start_mirror(link)
        for _ in range(10000):
            b._mirror_write(b"\xaa\x55" + b"\x00" * 8)  # no reader attached
    finally:
        b.stop_mirror()
