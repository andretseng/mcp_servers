"""Lifecycle tests for serial disconnect cleanup and nominal frame timing."""

import struct

import pytest
import serial

from serial_mcp.broker import Broker
from serial_mcp.parser import SYNC0, SYNC1


def frame(d1: float, d2: float) -> bytes:
    return bytes([SYNC0, SYNC1]) + struct.pack("<ff", d1, d2)


class OneShotSerial:
    port = "/dev/test"

    def __init__(self, broker, payload):
        self._broker = broker
        self._payload = payload
        self._read = False
        self.closed = False
        self.in_waiting = len(payload)

    def read(self, _size):
        if self._read:
            self._broker._stop.set()
            return b""
        self._read = True
        self.in_waiting = 0
        return self._payload

    def close(self):
        self.closed = True


def test_reader_closes_handle_when_port_disappears(monkeypatch):
    broker = Broker()
    payload = frame(1.0, 2.0) + frame(3.0, 4.0)
    fake = OneShotSerial(broker, payload)
    broker._ser = fake
    broker._baud = 4_000_000
    broker._thread = __import__("threading").current_thread()
    broker._stop.clear()

    broker._run()

    assert fake.closed is True
    assert broker._ser is None
    assert broker._thread is None
    assert broker.buffered_samples == 2
    rows = broker.samples_since(float("-inf"))
    assert rows[1][0] - rows[0][0] == pytest.approx(25e-6)


def test_reader_closes_handle_on_serial_exception():
    broker = Broker()

    class GoneSerial:
        port = "/dev/gone"
        in_waiting = 0

        def __init__(self):
            self.closed = False

        def read(self, _size):
            raise serial.SerialException("device disconnected")

        def close(self):
            self.closed = True

    fake = GoneSerial()
    broker._ser = fake
    broker._baud = 4_000_000
    broker._thread = __import__("threading").current_thread()
    broker._stop.clear()

    broker._run()

    assert fake.closed is True
    assert broker._ser is None
    assert broker._thread is None
