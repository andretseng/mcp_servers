"""Server-layer tests with a stubbed broker (no serial hardware).

Verifies the analysis path encodes intent: the FFT must recover a known
frequency, summaries must be correct, and pre-capture calls must fail loud.
"""

import os

import numpy as np
import pytest

import serial_mcp.server as srv


class StubBroker:
    """Stands in for Broker: pretends to be attached and returns a clean sine."""

    def __init__(self, rate=1000.0, freq=50.0, n=1000):
        self.attached = True
        self.locked = True
        self._mirror_link = None
        self._buffered_samples = 0
        self.fail_mirror = False
        self.detached = False
        t = np.arange(n) / rate
        self._rows = [(float(ti), float(np.sin(2 * np.pi * freq * ti)), 3.0) for ti in t]

    def now(self):
        return 0.0

    def samples_since(self, t0):
        return self._rows

    @property
    def buffered_samples(self):
        return self._buffered_samples

    def attach(self, port, baud):
        self.attached = True

    def detach(self):
        self.attached = False
        self.detached = True

    def start_mirror(self, link):
        if self.fail_mirror:
            raise RuntimeError("mirror path unavailable")
        self._mirror_link = link
        return link


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    monkeypatch.setattr(srv, "_broker", StubBroker())
    monkeypatch.setattr(srv.time, "sleep", lambda _s: None)  # don't actually wait
    monkeypatch.setattr(srv, "_last", None)
    monkeypatch.setattr(srv, "_profile", None)
    monkeypatch.setattr(srv, "_labels", ("delta", "angle"))


def test_tools_are_registered():
    # All advertised tools must be present in the FastMCP registry.
    names = {t.name for t in srv.mcp._tool_manager.list_tools()}
    assert {"get_profiles", "attach", "detach", "status", "capture", "stats", "plot",
            "start_mirror", "stop_mirror"} <= names


def test_status_reports_buffered_sample_count():
    assert srv.status()["buffered_samples"] == 0


def test_attach_discards_capture_from_previous_port():
    srv._last = {"t": np.array([0.0]), "ch1": np.array([1.0]),
                 "ch2": np.array([2.0]), "rate": 0.0}

    result = srv.attach("/dev/test", baud=4_000_000,
                        profile="aa55-float32x2", channel1="new1", channel2="new2")

    assert result["attached"] is True
    assert result["profile"] == "aa55-float32x2"
    assert srv._last is None
    assert srv.status()["channels"] == ["new1", "new2"]


def test_attach_releases_port_if_mirror_setup_fails():
    srv._broker.fail_mirror = True

    with pytest.raises(RuntimeError, match="mirror path unavailable"):
        srv.attach("/dev/test", baud=4_000_000,
                   profile="aa55-float32x2", channel1="new1", channel2="new2")

    assert srv._broker.detached is True
    assert srv._profile is None


def test_stats_before_capture_fails_loud():
    with pytest.raises(RuntimeError, match="no capture"):
        srv.stats()


def test_capture_then_stats_recovers_frequency():
    summary = srv.capture(duration_s=1.0)
    assert summary["samples"] == 1000
    assert abs(summary["est_rate_hz"] - 1000.0) < 50  # ~1 kHz

    s = srv.stats()
    # The 50 Hz sine must be recovered by the FFT (intent: stats is meaningful).
    assert abs(s["delta"]["dominant_hz"] - 50.0) < 2.0
    assert abs(s["delta"]["mean"]) < 0.05  # zero-mean sine
    assert abs(s["angle"]["mean"] - 3.0) < 1e-6  # constant channel


def test_capture_lookback_reads_backward_without_full_forward_sleep(monkeypatch):
    # duration_s == lookback_s: the whole window is already in the past, so
    # capture() must not sleep forward at all.
    sleep_calls = []
    monkeypatch.setattr(srv.time, "sleep", lambda s: sleep_calls.append(s))
    t0_seen = {}

    def spy_samples_since(t0):
        t0_seen["t0"] = t0
        return srv._broker._rows

    monkeypatch.setattr(srv._broker, "samples_since", spy_samples_since)

    srv.capture(duration_s=0.5, lookback_s=0.5)

    assert t0_seen["t0"] == srv._broker.now() - 0.5
    assert sleep_calls == []


def test_capture_lookback_partial_sleeps_only_the_remainder(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(srv.time, "sleep", lambda s: sleep_calls.append(s))

    srv.capture(duration_s=1.0, lookback_s=0.4)

    assert sleep_calls == [pytest.approx(0.6)]


def test_capture_lookback_exceeding_duration_rejected():
    with pytest.raises(ValueError, match="lookback_s"):
        srv.capture(duration_s=0.5, lookback_s=1.0)


def test_plot_writes_png(tmp_path):
    srv.capture(duration_s=1.0)
    out = str(tmp_path / "cap.png")
    res = srv.plot(out_path=out)
    assert res["path"] == out
    assert os.path.getsize(out) > 1000  # a real PNG, not empty


def test_save_csv_full_resolution_and_values(tmp_path):
    srv.capture(duration_s=1.0)
    out = str(tmp_path / "cap.csv")
    res = srv.save_csv(out_path=out)

    lines = open(out).read().splitlines()
    assert lines[0] == "time_s,delta,angle"  # labels from the fixture
    assert res["rows"] == 1000
    assert len(lines) == 1001  # header + every sample (NOT downsampled)

    # Spot-check a row round-trips the 50 Hz sine the stub produced.
    import numpy as np
    t, d, a = (float(x) for x in lines[500].split(","))
    assert abs(d - np.sin(2 * np.pi * 50.0 * t)) < 1e-5
    assert abs(a - 3.0) < 1e-6


def test_save_csv_default_location_is_project_csv_dir(tmp_path, monkeypatch):
    # Default path = <cwd>/csv/<timestamp>_serial_capture.csv, cwd = project root.
    monkeypatch.chdir(tmp_path)
    srv.capture(duration_s=1.0)
    res = srv.save_csv()  # no explicit path

    import glob
    import re
    files = glob.glob(str(tmp_path / "csv" / "*_serial_capture.csv"))
    assert len(files) == 1
    assert res["path"] == files[0]
    # Filename carries a YYYYmmdd_HHMMSS timestamp prefix.
    assert re.match(r"\d{8}_\d{6}_serial_capture\.csv$", os.path.basename(files[0]))
