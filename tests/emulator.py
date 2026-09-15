"""Pytest-facing wrapper around the MaxComm inverter emulator.

Loads tools/inverter_emulator.py (plain directory, no package) and exposes:
- a tiny request/response codec for tests that speak raw MaxComm
- EmulatorHandle: lifecycle + scenario controls for one emulator instance
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import threading
import time

_TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"
_spec = importlib.util.spec_from_file_location(
    "inverter_emulator", _TOOLS / "inverter_emulator.py"
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["inverter_emulator"] = _mod  # dataclasses need the module registered
_spec.loader.exec_module(_mod)

SolarmaxEmulator = _mod.SolarmaxEmulator


def _checksum(payload: str) -> str:
    return format(sum(ord(c) for c in payload), "04X")


def build_request(address: int, fields: list[str]) -> bytes:
    data = ";".join(fields)
    req = "{FB;" + format(address, "02X") + ";!!|64:" + data + "|$$$$}"
    req = req.replace("!!", format(len(req), "02X"))
    req = req.replace("$$$$", _checksum(req[1 : req.index("$$$$")]))
    return req.encode()


def parse_values(response: str) -> dict[str, int]:
    values: dict[str, int] = {}
    if ":" not in response:
        return values
    body = response.split(":", 1)[1].rsplit("|", 1)[0]
    for item in body.split(";"):
        if "=" in item:
            key, _, raw = item.partition("=")
            try:
                values[key] = int(raw.split(",")[0], 16)
            except ValueError:
                pass
    return values


class EmulatorHandle:
    """One running emulator on an ephemeral port, with scenario controls."""

    def __init__(self) -> None:
        self._emulator = SolarmaxEmulator(host="127.0.0.1", port=0)
        self._thread = threading.Thread(target=self._emulator.start, daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 5
        while self._emulator.bound_port is None:
            if time.monotonic() > deadline:
                raise RuntimeError("emulator did not bind within 5s")
            time.sleep(0.01)

    def stop(self) -> None:
        self._emulator.stop()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("emulator server thread did not stop within 5s")
        if any(thread.is_alive() for thread in self._emulator._client_threads):
            raise RuntimeError("emulator client thread did not stop within 5s")

    @property
    def addr(self) -> tuple[str, int]:
        return ("127.0.0.1", self._emulator.bound_port)

    # --- passthrough scenario controls ---

    @property
    def idle_timeout(self) -> float:
        return self._emulator.idle_timeout

    @idle_timeout.setter
    def idle_timeout(self, value: float) -> None:
        self._emulator.idle_timeout = value

    def begin_dusk(self, announce_seconds: float | None) -> None:
        self._emulator.begin_dusk(announce_seconds)

    def wake(self) -> None:
        self._emulator.wake()

    def inject(self, failure: str) -> None:
        self._emulator.inject(failure)

    def respond_only(self, fields: list[str] | None) -> None:
        """Answer only these fields (simulates a partial/dying frame); None restores."""
        self._emulator._respond_only = fields

    @property
    def dark(self) -> bool:
        return self._emulator.dark

    @dark.setter
    def dark(self, value: bool) -> None:
        self._emulator.dark = value

    def set_noise(self, enabled: bool) -> None:
        """Toggle the emulator's +-2% jitter (on by default)."""
        self._emulator.state.add_noise = enabled
