"""Lifecycle tests for the hardware-free inverter emulator."""

from __future__ import annotations

import socket
import threading
import time

from tests import emulator as emulator_support

SolarmaxEmulator = emulator_support.SolarmaxEmulator


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met before timeout")
        time.sleep(0.01)


def test_stop_waits_for_every_client_handler(socket_enabled) -> None:
    """Replacing the latest handler reference must not leak an older thread."""
    emulator = SolarmaxEmulator(host="127.0.0.1", port=0)
    server_thread = threading.Thread(target=emulator.start, daemon=True)
    handlers: list[threading.Thread] = []
    original_handle_client = emulator.handle_client

    def lingering_handle_client(client_socket, client_addr) -> None:
        original_handle_client(client_socket, client_addr)
        handlers.append(threading.current_thread())
        time.sleep(1.0 if len(handlers) == 1 else 0.05)

    emulator.handle_client = lingering_handle_client
    server_thread.start()

    try:
        _wait_until(lambda: emulator.bound_port is not None)
        address = ("127.0.0.1", emulator.bound_port)

        for expected_handlers in (1, 2):
            with socket.create_connection(address, timeout=1):
                pass
            _wait_until(lambda expected=expected_handlers: len(handlers) == expected)

        emulator.stop()

        assert all(not thread.is_alive() for thread in handlers)
    finally:
        emulator.stop()
        server_thread.join(timeout=2)
        for thread in handlers:
            thread.join(timeout=2)


def test_stop_cannot_race_client_thread_start(socket_enabled, monkeypatch) -> None:
    """Shutdown must wait while an accepted client's handler is starting."""
    emulator = SolarmaxEmulator(host="127.0.0.1", port=0)
    server_thread = threading.Thread(target=emulator.start, daemon=True)
    original_start = threading.Thread.start
    client_starting = threading.Event()
    allow_client_start = threading.Event()
    stop_finished = threading.Event()
    stop_thread: threading.Thread | None = None
    client: socket.socket | None = None

    server_thread.start()
    try:
        _wait_until(lambda: emulator.bound_port is not None)

        def controlled_start(thread: threading.Thread) -> None:
            if thread.name == "solarmax-emulator-client":
                client_starting.set()
                if not allow_client_start.wait(2):
                    raise AssertionError("client handler start remained blocked")
            original_start(thread)

        monkeypatch.setattr(threading.Thread, "start", controlled_start)
        client = socket.create_connection(("127.0.0.1", emulator.bound_port), timeout=1)
        assert client_starting.wait(2)

        def stop_emulator() -> None:
            emulator.stop()
            stop_finished.set()

        stop_thread = threading.Thread(target=stop_emulator)
        stop_thread.start()

        assert not stop_finished.wait(0.1)
        allow_client_start.set()
        stop_thread.join(timeout=2)

        assert stop_finished.is_set()
        assert all(not thread.is_alive() for thread in emulator._client_threads)
    finally:
        allow_client_start.set()
        if client is not None:
            client.close()
        if stop_thread is not None:
            stop_thread.join(timeout=2)
        emulator.stop()
        server_thread.join(timeout=2)


def test_client_handler_checks_shutdown_while_recv_is_blocked() -> None:
    """A blocked receive must wake often enough to observe shutdown."""

    class BlockingSocket:
        def __init__(self) -> None:
            self.timeout = 0.0
            self.recv_started = threading.Event()
            self.release = threading.Event()

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def recv(self, _size: int) -> bytes:
            self.recv_started.set()
            if self.release.wait(self.timeout):
                return b""
            raise TimeoutError

        def close(self) -> None:
            pass

    emulator = SolarmaxEmulator()
    client = BlockingSocket()
    emulator.running = True
    handler = threading.Thread(
        target=emulator.handle_client,
        args=(client, ("127.0.0.1", 12345)),
    )
    handler.start()
    try:
        assert client.recv_started.wait(1)
        emulator.running = False
        handler.join(timeout=0.5)
        assert not handler.is_alive()
    finally:
        client.release.set()
        handler.join(timeout=1)


def test_interactive_set_command_updates_state(capsys) -> None:
    """The interactive set command must update a known emulator field."""
    emulator = SolarmaxEmulator()

    keep_running = emulator_support._mod.execute_interactive_command(
        emulator, "set pac 42"
    )

    assert keep_running is True
    assert emulator.state.pac == 42
    assert "pac = 42" in capsys.readouterr().out


def test_interactive_scenario_command_replaces_state(capsys) -> None:
    """The scenario command must load the requested predefined state."""
    emulator = SolarmaxEmulator()

    keep_running = emulator_support._mod.execute_interactive_command(
        emulator, "scenario night"
    )

    assert keep_running is True
    assert emulator.state.sys == 20000
    assert "Loaded scenario: night" in capsys.readouterr().out


def test_interactive_quit_command_stops_the_loop() -> None:
    """Quit and exit commands must tell the interactive loop to stop."""
    emulator = SolarmaxEmulator()

    assert emulator_support._mod.execute_interactive_command(emulator, "quit") is False
    assert emulator_support._mod.execute_interactive_command(emulator, "exit") is False
