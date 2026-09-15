"""Connection primitives and transport for the SolarMax MaxComm link.

The inverter announces its own shutdown (SYS status 20002, DC power dropping
to a 1-2W residual) before it leaves the network. Arming reflects the
evidence seen on the LAST successful poll; a disconnect while armed (or with
the sun below the twilight threshold) is expected rather than a fault.

The state-machine data types and arming tracker are pure. SolarmaxLink owns
the TCP connection, while ConnectionEngine coordinates polling and state.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

from .protocol import (
    DEVICE_FIELDS,
    HOT_FIELDS,
    STATIC_FIELDS,
    ProtocolError,
    RetryableProtocolError,
    build_request,
    parse_response,
)

_LOGGER = logging.getLogger(__name__)

LOW_PDC_WATTS = 25
SHUTDOWN_ANNOUNCE_SYS = 20002
STARTUP_GRACE_SECONDS = 150.0
POLL_BUDGET_SECONDS = 15.0
STATIC_FETCH_MAX_ATTEMPTS = 2
# Prevent stale shutdown evidence from masking a sustained daytime fault.
ARMED_ESCALATION_SECONDS = 3600.0
ARMED_ESCALATION_MIN_FAILURES = 10


class EngineState(StrEnum):
    """Observed connection state, derived from poll evidence only."""

    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE_EXPECTED = "offline_expected"
    OFFLINE_FAULT = "offline_fault"


class ArmingTracker:
    """Track shutdown evidence from the latest poll that contains it."""

    def __init__(self, low_pdc_watts: float = LOW_PDC_WATTS) -> None:
        self.low_pdc_watts = low_pdc_watts
        self.armed = False

    def observe(self, values: dict[str, dict[str, float | int]]) -> None:
        status = values.get("SYS")
        dc_power = values.get("PDC")
        if status is None and dc_power is None:
            return
        self.armed = bool(
            status is not None
            and status["raw_value"] == SHUTDOWN_ANNOUNCE_SYS
            or dc_power is not None
            and dc_power["value"] < self.low_pdc_watts
        )


@dataclass
class EngineDiagnostics:
    """Poll/connection counters and the most recent state transitions."""

    connection_attempts: int = 0
    reconnects: int = 0
    timeouts: int = 0
    polls_ok: int = 0
    last_successful_poll: datetime | None = None
    last_shutdown_announcement: datetime | None = None
    transitions: list[tuple[str, str, str]] = field(default_factory=list)

    def record_transition(self, from_state: str, to_state: str) -> None:
        """Append an (iso-ts, from, to) transition, capped at the last 20."""
        self.transitions.append((datetime.now(UTC).isoformat(), from_state, to_state))
        del self.transitions[:-20]


@dataclass(frozen=True)
class EngineSnapshot:
    """Immutable result of a single poll cycle."""

    state: EngineState
    values: dict[str, dict[str, float | int]]
    shutdown_announced: bool
    reconnecting: bool
    expected_outside_twilight: bool
    fault_since: datetime | None
    # HA notifies listeners on `previous_data != self.data`; per-poll counter
    # churn in diagnostics must not defeat `always_update=False`, so it is
    # excluded from equality/hash comparison.
    diagnostics: dict[str, object] = field(compare=False)


class LinkTimeout(Exception):
    """No answer within the connect/response timeout budget (lockout/dark)."""


class LinkClosed(Exception):
    """Peer closed the connection (FIN/reset/EPIPE) and no recovery was possible."""


class _PeerClosed(Exception):
    """Internal signal: peer closed the socket — triggers one reconnect+resend."""


class SolarmaxLink:
    """Persistent async TCP transport to a single SolarMax inverter.

    The device serves exactly one TCP client and FINs idle connections at
    ~100 s. `request()` reuses one connection across calls, transparently
    reconnecting-and-resending once if the peer has closed it; any timeout
    (connect or response) raises `LinkTimeout` with no internal retry. No
    raw `OSError` is ever allowed to cross this boundary — an escaped
    exception would flip HA's `last_update_success` incorrectly.
    """

    def __init__(
        self,
        host: str,
        port: int,
        connect_timeout: float = 3.0,
        response_timeout: float = 3.5,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout
        self.connected = False
        self.attempts = 0
        self.reconnects = 0
        self.timeouts = 0
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._closed = False
        self._request_lock = asyncio.Lock()

    async def request(self, payload: str) -> str:
        """Send `payload` and return the response, reusing the connection."""
        async with self._request_lock:
            if self._closed:
                raise LinkClosed(f"{self.host}:{self.port}: link is closed")
            return await self._request(payload)

    async def _request(self, payload: str) -> str:
        """Perform one serialized request on a link that is still open."""
        if not self.connected:
            await self._connect()
        try:
            return await self._send_receive(payload)
        except _PeerClosed as err:
            await self._close_transport()
            if self._closed:  # terminally closed underneath us — do NOT reopen
                raise LinkClosed(
                    f"{self.host}:{self.port}: closed during poll"
                ) from err
            self.reconnects += 1
            await self._connect()
            try:
                return await self._send_receive(payload)
            except _PeerClosed as err2:
                await self._close_transport()
                raise LinkClosed(
                    f"peer at {self.host}:{self.port} closed the connection"
                ) from err2

    async def disconnect(self) -> None:
        """Drop the current transport while allowing a later reconnect."""
        self._abort_transport()

    async def close(self) -> None:
        """Terminally close the link and drain any request already in flight."""
        self._closed = True
        self._abort_transport()
        async with self._request_lock:
            # A pending open_connection() may have completed while close()
            # waited for the request lock. Its unpublished writer is rejected
            # in _connect(); this second abort is the final state assertion.
            self._abort_transport()

    async def _connect(self) -> None:
        if self._closed:
            raise LinkClosed(f"{self.host}:{self.port}: link is closed")

        reader, writer = await self._open_connection()
        if self._closed:
            # close() can land while open_connection() is awaiting.
            writer.transport.abort()
            raise LinkClosed(f"{self.host}:{self.port}: closed during connect")

        # Publish the writer before socket setup so failures can always abort it.
        self._reader = reader
        self._writer = writer
        self.connected = True
        try:
            self._configure_socket(writer)
        except OSError as err:
            self._abort_transport()
            raise LinkClosed(
                f"connect to {self.host}:{self.port} failed: {err}"
            ) from err

    async def _open_connection(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open a TCP connection and translate transport failures."""
        self.attempts += 1
        try:
            async with asyncio.timeout(self.connect_timeout):
                return await asyncio.open_connection(self.host, self.port)
        except TimeoutError as err:
            self.timeouts += 1
            self._abort_transport()
            raise LinkTimeout(f"connect to {self.host}:{self.port} timed out") from err
        except OSError as err:
            self._abort_transport()
            raise LinkClosed(
                f"connect to {self.host}:{self.port} failed: {err}"
            ) from err

    @staticmethod
    def _configure_socket(writer: asyncio.StreamWriter) -> None:
        """Enable low-latency writes and dead-peer detection when available."""
        sock = writer.get_extra_info("socket")
        if sock is None:
            return
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    async def _send_receive(self, payload: str) -> str:
        try:
            return await self._exchange(payload)
        except TimeoutError as err:
            self.timeouts += 1
            self._abort_transport()
            raise LinkTimeout(
                f"no response from {self.host}:{self.port} "
                f"within {self.response_timeout}s"
            ) from err
        except (ConnectionResetError, BrokenPipeError) as err:
            raise _PeerClosed(str(err)) from err
        except OSError as err:
            await self._close_transport()
            raise LinkClosed(f"{self.host}:{self.port}: {err}") from err
        except asyncio.CancelledError:
            # External cancellation (e.g. an outer asyncio.timeout wrapping a
            # poll, or HA cancelling a coordinator refresh on unload) can land
            # here mid-read. Abort synchronously — no `await` — so an
            # in-flight response can never be left on the wire for the NEXT
            # request() to read as a stale frame.
            self._abort_transport()
            raise

    async def _exchange(self, payload: str) -> str:
        """Perform one timed exchange on the published transport."""
        reader, writer = self._transport()
        async with asyncio.timeout(self.response_timeout):
            writer.write(payload.encode())
            await writer.drain()
            return (await self._read_response(reader)).decode(errors="ignore")

    def _transport(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = self._reader, self._writer
        if reader is None or writer is None:
            raise LinkClosed(f"{self.host}:{self.port}: transport is not connected")
        return reader, writer

    @staticmethod
    async def _read_response(reader: asyncio.StreamReader) -> bytes:
        """Read through the final MaxComm frame terminator."""
        response = b""
        while not response.endswith(b"}"):
            chunk = await reader.read(4096)
            if not chunk:
                raise _PeerClosed("peer closed the connection (EOF)")
            response += chunk
        return response

    def _abort_transport(self) -> None:
        """Synchronous, non-blocking transport teardown.

        `writer.close()` + `await writer.wait_closed()` is a graceful
        shutdown that can block if the peer has stopped reading (the
        measured silent-hang lockout). `transport.abort()` tears the socket
        down immediately and has no `await`, so it is also safe to call
        while unwinding a `CancelledError`.
        """
        writer = self._writer
        self._writer = None
        self._reader = None
        self.connected = False
        if writer is not None:
            writer.transport.abort()

    async def _close_transport(self) -> None:
        writer = self._writer
        self._writer = None
        self._reader = None
        self.connected = False
        if writer is None:
            return
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass


class ConnectionEngine:
    """Composes the codec, the state machine, and the link into poll().

    Every `poll()` call returns an `EngineSnapshot` classified from what
    that poll (and, for arming, the LAST successful poll) actually
    observed — never from elapsed time or assumptions about why a request
    failed. `EngineState`'s own docstring is the invariant this class
    exists to uphold: state is derived from poll evidence only.
    """

    def __init__(
        self,
        link: SolarmaxLink,
        address: int,
        sun_below: Callable[[], bool],
        verify_checksum: bool = True,
        low_pdc_watts: float = LOW_PDC_WATTS,
        grace_seconds: float = STARTUP_GRACE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._link = link
        self._address = address
        self._sun_below = sun_below
        self._verify_checksum = verify_checksum
        self._grace_seconds = grace_seconds
        self._clock = clock
        self._today = today or (lambda: datetime.now(UTC).date())
        self._tracker = ArmingTracker(low_pdc_watts)
        self._diagnostics = EngineDiagnostics()
        self._values: dict[str, dict[str, float | int]] = {}
        self._values_date: date | None = None
        self._statics_loaded = False
        self._static_fetch_attempts = 0
        self._state = EngineState.UNKNOWN
        self._fault_since: datetime | None = None
        # One anchor covers both startup and post-online reconnect grace.
        self._disconnected_since: float | None = self._clock()
        # An armed, daytime disconnect becomes a fault only after both limits.
        self._escalation_since: float | None = None
        self._escalation_failures = 0
        self._closed = False
        # Scheduled and debounced refreshes may overlap; the link may not.
        self._poll_lock = asyncio.Lock()

    async def poll(self) -> EngineSnapshot:
        async with self._poll_lock:
            if self._closed:
                return self._snapshot(
                    reconnecting=False, expected_outside_twilight=False
                )
            try:
                async with asyncio.timeout(POLL_BUDGET_SECONDS):
                    return await self._poll_inner()
            except (TimeoutError, LinkTimeout, LinkClosed, ProtocolError):
                return await self._on_failure()

    @asynccontextmanager
    async def validation_handoff(self) -> AsyncIterator[None]:
        """Temporarily release the inverter connection for validation."""
        async with self._poll_lock:
            if self._closed:
                raise LinkClosed("connection engine is closed")
            await self._link.disconnect()
            yield

    async def close(self) -> None:
        """Idempotent; the last word — no poll() may touch the link again.

        Sets `_closed` before tearing down the link, then waits for the poll
        lock so no active poll can issue another request after close returns.
        """
        self._closed = True
        await self._link.close()
        async with self._poll_lock:
            pass

    async def _poll_inner(self) -> EngineSnapshot:
        if not self._statics_loaded:
            try:
                await self._load_static_values()
            except (LinkTimeout, LinkClosed, ProtocolError):
                # Live telemetry, not optional metadata, decides poll health.
                pass
            if self._closed:
                return self._snapshot(
                    reconnecting=False, expected_outside_twilight=False
                )
        values = await self._request_with_retry(
            build_request(self._address, HOT_FIELDS)
        )
        return self._on_success(values)

    async def _load_static_values(self) -> None:
        fields = STATIC_FIELDS + DEVICE_FIELDS
        values = await self._request_with_retry(build_request(self._address, fields))
        self._values.update(values)
        self._static_fetch_attempts += 1
        self._statics_loaded = all(field in values for field in fields) or (
            self._static_fetch_attempts >= STATIC_FETCH_MAX_ATTEMPTS
        )

    async def _request_with_retry(
        self, payload: str
    ) -> dict[str, dict[str, float | int]]:
        """Fetch a payload, retrying once for timeout or corrupt data."""
        try:
            raw = await self._link.request(payload)
        except LinkTimeout:
            raw = await self._link.request(payload)
        try:
            return parse_response(raw, self._verify_checksum)
        except RetryableProtocolError:
            raw = await self._link.request(payload)
            return parse_response(raw, self._verify_checksum)

    def _on_success(self, values: dict[str, dict[str, float | int]]) -> EngineSnapshot:
        today = self._today()
        if self._values_date is not None and today != self._values_date:
            self._values.pop("KDY", None)
        self._values_date = today
        # Partial frames update present readings without discarding cached ones.
        self._values.update(values)

        was_armed = self._tracker.armed
        self._tracker.observe(values)
        if self._tracker.armed and not was_armed:
            self._diagnostics.last_shutdown_announcement = datetime.now(UTC)

        previous_state = self._state
        self._set_state(EngineState.ONLINE, previous_state)
        self._diagnostics.polls_ok += 1
        self._diagnostics.last_successful_poll = datetime.now(UTC)

        self._fault_since = None
        self._disconnected_since = None
        self._reset_escalation()

        return self._snapshot(reconnecting=False, expected_outside_twilight=False)

    async def _on_failure(self) -> EngineSnapshot:
        previous_state = self._state
        armed = self._tracker.armed
        sun_below = self._sun_is_below()
        if armed or sun_below:
            new_state, expected_outside_twilight = await self._expected_failure(
                armed, sun_below
            )
            reconnecting = False
        else:
            new_state, reconnecting = self._daytime_failure(previous_state)
            expected_outside_twilight = False

        self._set_state(new_state, previous_state)
        return self._snapshot(
            reconnecting=reconnecting,
            expected_outside_twilight=expected_outside_twilight,
        )

    def _sun_is_below(self) -> bool:
        """Read the sun fallback without crossing poll's no-error boundary."""
        try:
            return self._sun_below()
        except Exception:
            _LOGGER.warning(
                "sun_below callback raised; assuming sun is up", exc_info=True
            )
            return False

    async def _expected_failure(
        self, armed: bool, sun_below: bool
    ) -> tuple[EngineState, bool]:
        """Handle a disconnect explained by shutdown evidence or darkness."""
        # An expected window starts a fresh repair clock if it later becomes a fault.
        self._fault_since = None
        await self._link.disconnect()
        self._statics_loaded = False
        self._static_fetch_attempts = 0

        anomalous = armed and not sun_below
        if not anomalous:
            self._reset_escalation()
            return EngineState.OFFLINE_EXPECTED, False
        if not self._should_escalate():
            return EngineState.OFFLINE_EXPECTED, True

        self._tracker.armed = False
        self._fault_since = datetime.now(UTC)
        self._reset_escalation()
        return EngineState.OFFLINE_FAULT, False

    def _should_escalate(self) -> bool:
        """Advance and evaluate the armed daytime-disconnect limits."""
        if self._escalation_since is None:
            self._escalation_since = self._clock()
        self._escalation_failures += 1
        return (
            self._clock() - self._escalation_since >= ARMED_ESCALATION_SECONDS
            and self._escalation_failures >= ARMED_ESCALATION_MIN_FAILURES
        )

    def _daytime_failure(self, previous_state: EngineState) -> tuple[EngineState, bool]:
        """Classify an unexplained daytime disconnect."""
        self._reset_escalation()
        if self._disconnected_since is None:
            self._disconnected_since = self._clock()
        reconnecting = (self._clock() - self._disconnected_since) < self._grace_seconds
        if previous_state is EngineState.UNKNOWN and reconnecting:
            return EngineState.UNKNOWN, True
        if self._fault_since is None:
            self._fault_since = datetime.now(UTC)
        return EngineState.OFFLINE_FAULT, reconnecting

    def _reset_escalation(self) -> None:
        self._escalation_since = None
        self._escalation_failures = 0

    def _set_state(self, state: EngineState, previous_state: EngineState) -> None:
        self._state = state
        if previous_state is not state:
            self._diagnostics.record_transition(previous_state, state)

    def _snapshot(
        self, *, reconnecting: bool, expected_outside_twilight: bool
    ) -> EngineSnapshot:
        # Link counters are live and must not lag behind the returned snapshot.
        self._diagnostics.connection_attempts = self._link.attempts
        self._diagnostics.reconnects = self._link.reconnects
        self._diagnostics.timeouts = self._link.timeouts
        return EngineSnapshot(
            state=self._state,
            values=dict(self._values),  # fresh copy every snapshot
            shutdown_announced=self._tracker.armed,
            reconnecting=reconnecting,
            expected_outside_twilight=expected_outside_twilight,
            fault_since=self._fault_since,
            diagnostics=asdict(self._diagnostics),
        )
