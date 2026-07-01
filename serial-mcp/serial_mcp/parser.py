"""Streaming decoder for the drone/motor serial-plot telemetry frame.

Wire format produced by HAL_updateSerialPlot()
(solutions/.../hal/communication_interface/include/hal_debug_interface.h:170):

    byte 0   : 0xAA            sync
    byte 1   : 0x55            sync
    byte 2-5 : data1           IEEE-754 float32, little-endian
    byte 6-9 : data2           IEEE-754 float32, little-endian

10 bytes, repeating. There is no checksum and no length field, so the sync
bytes 0xAA 0x55 can legitimately occur inside the float payload. The decoder
therefore never trusts a single 0xAA 0x55: it confirms a candidate boundary by
requiring the *next* frame header to also be 0xAA 0x55 before locking, and it
drops the lock and rescans if an expected header is missing (e.g. a byte was
dropped on the wire). This is the only non-trivial part of the whole tool, so
it is covered directly by tests in tests/test_parser.py.
"""

import struct

SYNC0 = 0xAA
SYNC1 = 0x55
FRAME_LEN = 10
_PAYLOAD = struct.Struct("<ff")  # two LE float32, starting at byte 2

# Current firmware assignment (Debug_Datalog_介面與選項.md §四). Overridable at
# the server layer because pDacCtrl1/2->pDacOutAddr get re-pointed per session.
DEFAULT_CHANNELS = ("focDacDelta_rad", "angleObs_rad")


class FrameDecoder:
    """Feed raw bytes, get back decoded (data1, data2) tuples.

    Maintains lock state across calls so it works on a byte stream arriving in
    arbitrary chunks (as it does from a serial reader thread).
    """

    def __init__(self, max_buffer: int = 1 << 16):
        self._buf = bytearray()
        self._locked = False
        self._max_buffer = max_buffer

    @property
    def locked(self) -> bool:
        return self._locked

    def feed(self, data: bytes) -> list[tuple[float, float]]:
        """Append bytes and return all complete frames decodable so far."""
        self._buf.extend(data)
        out: list[tuple[float, float]] = []

        if not self._locked and not self._acquire_lock():
            self._bound_buffer()
            return out

        while len(self._buf) >= FRAME_LEN:
            if self._buf[0] == SYNC0 and self._buf[1] == SYNC1:
                out.append(_PAYLOAD.unpack_from(self._buf, 2))
                del self._buf[:FRAME_LEN]
            else:
                # Lost alignment: drop one byte, try to re-lock, else wait for more.
                self._locked = False
                del self._buf[0]
                if not self._acquire_lock():
                    break
        return out

    def _acquire_lock(self) -> bool:
        """Find offset i where frames i and i+1 both start with 0xAA 0x55."""
        buf = self._buf
        last = len(buf) - (FRAME_LEN + 2)  # need two headers to confirm
        i = 0
        while i <= last:
            if (
                buf[i] == SYNC0
                and buf[i + 1] == SYNC1
                and buf[i + FRAME_LEN] == SYNC0
                and buf[i + FRAME_LEN + 1] == SYNC1
            ):
                del buf[:i]
                self._locked = True
                return True
            i += 1
        return False

    def _bound_buffer(self) -> None:
        """Cap memory while scanning a stream that never locks (pure garbage)."""
        if len(self._buf) > self._max_buffer:
            keep = 2 * FRAME_LEN
            del self._buf[:-keep]
