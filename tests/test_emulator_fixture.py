"""Tests for the MaxComm emulator fixture.

These validate that the emulator reproduces the behaviour measured on the
live 7TP2 (2026-09-01 probe): persistent connections, ~100s idle FIN,
single-client lockout, and the dusk sequence 20008 -> 20002 -> dark.
"""

import socket
import threading
import time

import pytest

from tests.emulator import EmulatorHandle, build_request, parse_values


def _poll(sock, fields=("PAC", "SYS"), timeout=2.0):
    sock.settimeout(timeout)
    sock.sendall(build_request(1, list(fields)))
    buf = b""
    while not buf.endswith(b"}"):
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return parse_values(buf.decode(errors="ignore"))


def test_stop_joins_blocked_client_handler(socket_enabled):
    """Stopping the emulator must unblock and join an idle client handler."""
    emulator = EmulatorHandle()
    emulator.start()
    client = socket.create_connection(emulator.addr, timeout=2)
    try:
        deadline = time.monotonic() + 2
        handlers = []
        while not handlers:
            handlers = [
                thread
                for thread in threading.enumerate()
                if thread.name == "solarmax-emulator-client"
            ]
            if time.monotonic() >= deadline:
                raise AssertionError("emulator did not start a client handler")
            time.sleep(0.01)

        emulator.stop()
        assert all(not thread.is_alive() for thread in handlers)
    finally:
        client.close()
        emulator.stop()


def test_persistent_connection_multiple_polls(emulator):
    """One connection serves many polls — like the real inverter."""
    with socket.create_connection(emulator.addr, timeout=2) as sock:
        for _ in range(3):
            values = _poll(sock)
            assert values["SYS"] == 20008


def test_idle_timeout_closes_with_fin(emulator):
    """Past the idle window the emulator closes cleanly (recv -> 0 bytes)."""
    emulator.idle_timeout = 1.0
    with socket.create_connection(emulator.addr, timeout=2) as sock:
        _poll(sock)
        time.sleep(1.5)
        sock.settimeout(2)
        assert sock.recv(1024) == b""  # clean FIN, like the real device


def test_idle_timeout_change_applies_to_live_connection(emulator):
    """Scenario controls can shorten an existing connection's idle window."""
    emulator.idle_timeout = 10.0
    with socket.create_connection(emulator.addr, timeout=2) as sock:
        _poll(sock)
        emulator.idle_timeout = 0.1
        time.sleep(0.4)
        sock.settimeout(1)
        assert sock.recv(1024) == b""


def test_idle_timeout_must_be_positive(emulator):
    """Zero must not turn the client socket into a nonblocking socket."""
    with pytest.raises(ValueError, match="positive"):
        emulator.idle_timeout = 0


def test_second_client_hangs_not_refused(emulator):
    """A second connection is accepted by backlog but never answered."""
    with socket.create_connection(emulator.addr, timeout=2) as first:
        _poll(first)
        with socket.create_connection(emulator.addr, timeout=2) as second:
            second.settimeout(1.0)
            second.sendall(build_request(1, ["PAC"]))
            try:
                data = second.recv(1024)
                assert data == b""  # closed unanswered is acceptable too
            except TimeoutError:
                pass  # the measured real behaviour: silent hang
        # first connection keeps working throughout
        assert _poll(first)["SYS"] == 20008


def test_dusk_sequence(emulator):
    """Scripted dusk: announce 20002 with zero power, then go dark."""
    emulator.begin_dusk(announce_seconds=0.5)
    with socket.create_connection(emulator.addr, timeout=2) as sock:
        values = _poll(sock, fields=("PAC", "SYS", "PDC"))
        assert values["SYS"] == 20002
        assert values["PAC"] == 0
        # Real device reports a 1-2W residual during shutdown, never clean 0.
        # The engine's indicator is therefore a threshold (PDC < 50W), and the
        # emulator must not make the test easier than reality.
        assert 0 < values["PDC"] < 50 * 2  # raw is W*2 per protocol scaling

    time.sleep(0.8)  # announcement window elapses -> dark
    try:
        with socket.create_connection(emulator.addr, timeout=1) as sock:
            sock.settimeout(1.0)
            sock.sendall(build_request(1, ["PAC"]))
            data = sock.recv(1024)
            assert data == b""  # or silence; never a valid frame
    except (TimeoutError, ConnectionError, OSError):
        pass  # dark = timeout, the measured drop signature


def test_dawn_wake(emulator):
    """wake() restores normal operation after dark."""
    emulator.begin_dusk(announce_seconds=0)
    time.sleep(0.3)
    emulator.wake()
    with socket.create_connection(emulator.addr, timeout=2) as sock:
        assert _poll(sock)["SYS"] == 20008


def test_failure_injection_corrupt_crc(emulator):
    """inject('corrupt_crc') poisons exactly the next response."""
    emulator.inject("corrupt_crc")
    with socket.create_connection(emulator.addr, timeout=2) as sock:
        sock.settimeout(2)
        sock.sendall(build_request(1, ["PAC"]))
        buf = b""
        while not buf.endswith(b"}"):
            buf += sock.recv(4096)
        frame = buf.decode()
        payload, crc = frame[1:].rsplit("|", 1)
        expected = format(sum(ord(c) for c in payload + "|"), "04X")
        assert crc.rstrip("}") != expected
        # next poll is clean again
        assert _poll(sock)["PAC"] is not None
