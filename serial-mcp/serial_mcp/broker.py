"""Owns the physical serial port: one reader thread, one decoder, one ring buffer.

The reader thread drains the port continuously while attached (required at
4 Mbaud — if we only read during a capture, the OS/driver buffer overflows and
both the live mirror and the next capture lose frames). Decoded samples land in
a timestamped ring buffer that capture() snapshots. The PTY mirror is opt-in:
when enabled, raw bytes are also copied to a pseudo-terminal that serialplot can
open, with non-blocking writes that drop when nothing is reading it.
"""

import os
import threading
import time
import tty
from collections import deque

import serial

from .parser import FrameDecoder


class Broker:
    def __init__(self, ring_size: int = 500_000):
        self._ser: serial.Serial | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ring: deque[tuple[float, float, float]] = deque(maxlen=ring_size)
        self._lock = threading.Lock()
        self._decoder = FrameDecoder()
        # Mirror state
        self._mirror_fd: int | None = None
        self._mirror_link: str | None = None

    # ---- lifecycle -------------------------------------------------------
    @property
    def attached(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def attach(self, port: str, baud: int) -> None:
        if self.attached:
            raise RuntimeError(f"already attached to {self._ser.port}; detach first")
        # Fail loud: if the port can't be opened (e.g. board unplugged), raise.
        self._ser = serial.Serial(port, baudrate=baud, timeout=0.05, exclusive=True)
        self._decoder = FrameDecoder()
        with self._lock:
            self._ring.clear()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="serial-reader", daemon=True)
        self._thread.start()

    def detach(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        # Leave the PTY mirror up: the symlink persists across detach so the
        # virtual port stays openable for the whole server lifetime (no data
        # flows while detached). It's torn down at process exit, not here.
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    # ---- reader thread ---------------------------------------------------
    def _run(self) -> None:
        ser = self._ser
        while not self._stop.is_set():
            try:
                data = ser.read(ser.in_waiting or 1)
            except (serial.SerialException, OSError):
                break  # port went away; thread exits, attached becomes False
            if not data:
                continue
            ts = time.monotonic()
            samples = self._decoder.feed(data)
            if samples:
                with self._lock:
                    for d1, d2 in samples:
                        self._ring.append((ts, d1, d2))
            if self._mirror_fd is not None:
                self._mirror_write(data)

    @property
    def locked(self) -> bool:
        return self._decoder.locked

    # ---- capture ---------------------------------------------------------
    def now(self) -> float:
        return time.monotonic()

    def samples_since(self, t0: float) -> list[tuple[float, float, float]]:
        with self._lock:
            return [s for s in self._ring if s[0] >= t0]

    # ---- opt-in mirror ---------------------------------------------------
    def start_mirror(self, link: str) -> str:
        if self._mirror_fd is not None:
            return self._mirror_link  # already running
        master_fd, slave_fd = os.openpty()
        # Raw mode: pass binary through untouched. Without this the default
        # cooked discipline line-buffers and translates NL/CR, which would
        # corrupt 0x0A/0x0D bytes inside the float payload. The setting persists
        # with the pty (master stays open), so a later serialplot open inherits it.
        tty.setraw(slave_fd)
        os.set_blocking(master_fd, False)
        slave_name = os.ttyname(slave_fd)
        os.close(slave_fd)  # serialplot opens the slave by path; master stays ours
        # Stable, user-facing path -> the kernel-assigned /dev/pts/N
        try:
            if os.path.islink(link) or os.path.exists(link):
                os.unlink(link)
        except OSError:
            pass
        os.symlink(slave_name, link)
        self._mirror_fd = master_fd
        self._mirror_link = link
        return link

    def stop_mirror(self) -> None:
        if self._mirror_fd is not None:
            os.close(self._mirror_fd)
            self._mirror_fd = None
        if self._mirror_link is not None:
            try:
                os.unlink(self._mirror_link)
            except OSError:
                pass
            self._mirror_link = None

    def _mirror_write(self, data: bytes) -> None:
        try:
            os.write(self._mirror_fd, data)
        except (BlockingIOError, OSError):
            pass  # no reader / buffer full -> drop, never block the reader thread
