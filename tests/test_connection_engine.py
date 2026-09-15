"""Engine orchestration against the emulator — the spec's success criteria."""

import asyncio

import pytest
from freezegun import freeze_time

from custom_components.solarmax import connection as connection_module
from custom_components.solarmax.connection import (
    ARMED_ESCALATION_MIN_FAILURES,
    ARMED_ESCALATION_SECONDS,
    POLL_BUDGET_SECONDS,
    ConnectionEngine,
    EngineState,
    LinkClosed,
    LinkTimeout,
    SolarmaxLink,
)
from custom_components.solarmax.protocol import calculate_checksum


def _response(data: str) -> str:
    """Build a checksummed single-frame response for link doubles."""
    inner = f"01;FB;18|64:{data}|"
    return "{" + inner + calculate_checksum(inner) + "}"


class _FakeClock:
    """Deterministic stand-in for `time.monotonic` — advanced explicitly so
    escalation-window tests never need a real hour-long sleep."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _BlockingStaticLink:
    """Link double that lets close land after the static request starts."""

    def __init__(self) -> None:
        self.static_started = asyncio.Event()
        self.release_static = asyncio.Event()
        self.request_count = 0
        self.connected = False
        self.attempts = 0
        self.reconnects = 0
        self.timeouts = 0

    async def request(self, payload: str) -> str:
        self.request_count += 1
        if self.request_count == 1:
            self.static_started.set()
            await self.release_static.wait()
            data = "TYP=50AA"
        else:
            data = "PAC=BB8"
        return _response(data)

    async def disconnect(self) -> None:
        self.connected = False

    async def close(self) -> None:
        self.connected = False


class _PartialStaticLink:
    """Always omits most static fields and records the requested payloads."""

    def __init__(self) -> None:
        self.payloads: list[str] = []
        self.connected = False
        self.attempts = 0
        self.reconnects = 0
        self.timeouts = 0

    async def request(self, payload: str) -> str:
        self.payloads.append(payload)
        return _response("TYP=50AA" if "PIN" in payload else "PAC=BB8")

    async def disconnect(self) -> None:
        self.connected = False

    async def close(self) -> None:
        self.connected = False


class _ScriptedLink:
    """Return or raise a fixed sequence of request outcomes."""

    def __init__(
        self, responses: list[str | Exception], *, disconnect_delay: float = 0.0
    ) -> None:
        self.responses = responses
        self.disconnect_delay = disconnect_delay
        self.disconnect_calls = 0
        self.connected = False
        self.attempts = 0
        self.reconnects = 0
        self.timeouts = 0

    async def request(self, _payload: str) -> str:
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.disconnect_calls > 1:
            raise AssertionError("one poll classified the same failure twice")
        await asyncio.sleep(self.disconnect_delay)
        self.connected = False

    async def close(self) -> None:
        self.connected = False


class _RecordingLink:
    """Link double that records handoff and poll activity."""

    def __init__(self) -> None:
        self.disconnect_calls = 0
        self.request_count = 0
        self.connected = False
        self.attempts = 0
        self.reconnects = 0
        self.timeouts = 0

    async def request(self, _payload: str) -> str:
        self.request_count += 1
        return _response("PAC=03E8")

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    async def close(self) -> None:
        self.connected = False


def _engine(emulator, *, sun_below=lambda: False, grace=0.0, timeout=0.5, clock=None):
    # grace=0.0 disables the startup window so classification is tested directly;
    # the two grace tests pass an explicit grace to exercise the window itself.
    link = SolarmaxLink(*emulator.addr, response_timeout=timeout)
    kwargs = {} if clock is None else {"clock": clock}
    return ConnectionEngine(
        link, address=1, sun_below=sun_below, grace_seconds=grace, **kwargs
    )


async def test_online_poll_returns_values_and_statics(emulator):
    emulator.set_noise(False)  # PAC jitters +-2% otherwise
    engine = _engine(emulator)
    try:
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE
        assert snapshot.values["PAC"]["value"] == 1500.0
        assert snapshot.values["PIN"]["raw_value"] > 0  # statics fetched
        assert snapshot.values["TYP"]["raw_value"] == 20650
    finally:
        await engine.close()


async def test_dusk_sequence_classifies_expected(emulator):
    """Success criterion 1: announced dusk -> OFFLINE_EXPECTED, armed."""
    engine = _engine(emulator)
    try:
        await engine.poll()
        emulator.begin_dusk(announce_seconds=0.4)
        snapshot = await engine.poll()  # sees 20002 + PDC residual
        assert snapshot.shutdown_announced is True
        await asyncio.sleep(0.6)  # device goes dark
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.OFFLINE_EXPECTED
        assert snapshot.values["KDY"]["raw_value"] >= 0  # last values retained
    finally:
        await engine.close()


async def test_announcement_slip_covered_by_pdc(emulator):
    """The 20002 frame can slip between polls; low PDC still arms."""
    engine = _engine(emulator)
    try:
        await engine.poll()
        emulator.begin_dusk(announce_seconds=0.4)
        emulator.respond_only(["PAC", "PDC", "KDY"])  # SYS withheld: PDC path alone
        await engine.poll()  # PDC residual 1.5W < 25W must arm without SYS
        await asyncio.sleep(0.6)
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.OFFLINE_EXPECTED
    finally:
        await engine.close()


async def test_unannounced_drop_at_noon_is_fault(emulator):
    """Success criterion 2: healthy -> dark with sun up = FAULT, fault_since set."""
    engine = _engine(emulator)
    try:
        await engine.poll()
        emulator.dark = True  # sudden death, no announcement
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.OFFLINE_FAULT
        assert snapshot.fault_since is not None
    finally:
        await engine.close()


async def test_sun_fallback_for_non_announcing_inverter(emulator):
    """Success criterion 4: no announcement, sun below -> EXPECTED."""
    engine = _engine(emulator, sun_below=lambda: True)
    try:
        await engine.poll()
        emulator.dark = True
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.OFFLINE_EXPECTED
    finally:
        await engine.close()


async def test_online_drop_is_fault_immediately_with_reconnecting_flag(emulator):
    """Reconnect grace softens attributes without hiding a daytime fault."""
    engine = _engine(emulator, grace=60.0)
    try:
        await engine.poll()
        emulator.dark = True
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.OFFLINE_FAULT
        assert snapshot.reconnecting is True
        assert snapshot.fault_since is not None
    finally:
        await engine.close()


async def test_startup_grace_keeps_unknown(emulator):
    """From UNKNOWN, timeouts within the grace stay UNKNOWN, not FAULT."""
    emulator.dark = True
    engine = _engine(emulator, grace=60.0)
    try:
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.UNKNOWN
        assert snapshot.reconnecting is True
    finally:
        await engine.close()


async def test_partial_frame_is_online_and_can_arm(emulator):
    """A partial response proves reachability and may announce shutdown."""
    emulator.begin_dusk(announce_seconds=None)  # announce-only, no dark timer
    emulator.respond_only(["SYS"])
    engine = _engine(emulator)
    try:
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE
        assert snapshot.shutdown_announced is True
    finally:
        await engine.close()


async def test_partial_frame_after_midnight_drops_previous_daily_energy(emulator):
    """A partial new-day response must not restore yesterday's KDY value."""
    engine = _engine(emulator)
    try:
        with freeze_time("2026-09-04 12:00:00"):
            snapshot = await engine.poll()
        assert snapshot.values["KDY"]["value"] > 0

        emulator.respond_only(["PAC", "PDC", "SYS"])
        with freeze_time("2026-09-05 12:00:00"):
            snapshot = await engine.poll()

        assert "KDY" not in snapshot.values
    finally:
        await engine.close()


async def test_expected_outside_twilight_flag(emulator):
    """An announced daytime shutdown is expected but marked anomalous."""
    engine = _engine(emulator, sun_below=lambda: False)
    try:
        await engine.poll()
        emulator.begin_dusk(announce_seconds=0.4)
        await engine.poll()  # observes SYS 20002 / PDC residual -> arms
        await asyncio.sleep(0.6)
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.OFFLINE_EXPECTED
        assert snapshot.expected_outside_twilight is True
    finally:
        await engine.close()


async def test_corrupt_crc_is_retried_within_the_poll(emulator):
    """One in-poll retry on RetryableProtocolError: line noise never fails a cycle."""
    engine = _engine(emulator)
    try:
        await engine.poll()
        emulator.inject("corrupt_crc")  # poisons exactly one response
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE
    finally:
        await engine.close()


async def test_truncated_frame_retried(emulator):
    engine = _engine(emulator)
    try:
        await engine.poll()
        emulator.inject("truncate")
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE
    finally:
        await engine.close()


async def test_empty_data_frame_does_not_crash(emulator):
    """Empty payload parses to zero values: still an answering device."""
    engine = _engine(emulator)
    try:
        await engine.poll()
        emulator.inject("empty_data")
        snapshot = await engine.poll()
        assert snapshot.state in (EngineState.ONLINE,)
        assert snapshot.values["PAC"]["value"] is not None  # held from poll 1
    finally:
        await engine.close()


async def test_recovery_clears_everything(emulator):
    # wake may first drain a stale backlogged socket
    engine = _engine(emulator, timeout=1.0)
    try:
        await engine.poll()
        emulator.begin_dusk(announce_seconds=0.4)
        await engine.poll()  # observes SYS 20002 / PDC residual -> arms
        await asyncio.sleep(0.6)
        assert (await engine.poll()).state is EngineState.OFFLINE_EXPECTED
        emulator.wake()
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE
        assert snapshot.shutdown_announced is False
        assert snapshot.fault_since is None
    finally:
        await engine.close()


async def test_statics_retried_within_the_poll(emulator):
    """Static and hot requests share the same one-shot retry policy."""
    engine = _engine(emulator)
    try:
        emulator.inject("corrupt_crc")  # poisons the *statics* frame (first request)
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE
        assert "PIN" in snapshot.values
    finally:
        await engine.close()


@pytest.mark.parametrize(
    "failure_type",
    [None, LinkTimeout, LinkClosed],
    ids=["corrupt", "timeout", "closed"],
)
async def test_failed_statics_do_not_block_hot_telemetry(failure_type):
    """Exhausted static retries defer metadata without failing telemetry."""
    static_response = _response("TYP=50AA")
    if failure_type is None:
        failures = [f"{static_response[:-5]}0000}}"] * 2
    elif failure_type is LinkClosed:
        failures = [LinkClosed()]
    else:
        failures = [failure_type(), failure_type()]
    link = _ScriptedLink(
        [
            *failures,
            _response("PAC=BB8"),
            static_response,
            _response("PAC=BB8"),
        ]
    )
    engine = ConnectionEngine(
        link, address=1, sun_below=lambda: False, grace_seconds=0.0
    )
    try:
        first = await engine.poll()
        assert first.state is EngineState.ONLINE
        assert "PAC" in first.values
        assert "TYP" not in first.values

        second = await engine.poll()
        assert second.state is EngineState.ONLINE
        assert "TYP" in second.values
    finally:
        await engine.close()


async def test_poll_budget_expiry_does_not_classify_failure_twice(monkeypatch):
    """Failure cleanup runs once, outside the request budget."""
    monkeypatch.setattr(connection_module, "POLL_BUDGET_SECONDS", 0.01)
    link = _ScriptedLink(
        [
            _response("TYP=50AA"),
            _response("SYS=4E22;PDC=0"),
            _response("TYP=50AA"),
            LinkClosed(),
        ],
        disconnect_delay=0.05,
    )
    engine = ConnectionEngine(
        link, address=1, sun_below=lambda: False, grace_seconds=0.0
    )
    try:
        online = await engine.poll()
        assert online.state is EngineState.ONLINE
        assert online.shutdown_announced is True

        offline = await engine.poll()
        assert offline.state is EngineState.OFFLINE_EXPECTED
    finally:
        await engine.close()


async def test_sun_below_callback_exception_does_not_escape_poll(emulator):
    """A broken sun callback conservatively becomes a daytime fault."""

    def _raising_sun_below() -> bool:
        raise RuntimeError("boom")

    engine = _engine(emulator, sun_below=_raising_sun_below)
    try:
        await engine.poll()
        emulator.dark = True
        snapshot = await engine.poll()  # must not raise
        assert snapshot.state is EngineState.OFFLINE_FAULT
    finally:
        await engine.close()


async def test_dawn_recovery_after_on_failure_closed_link(emulator):
    """Expected outages disconnect without terminally closing the link."""
    engine = _engine(emulator, sun_below=lambda: True)
    try:
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE

        emulator.dark = True
        snapshot = await engine.poll()  # -> OFFLINE_EXPECTED, engine closes link
        assert snapshot.state is EngineState.OFFLINE_EXPECTED

        emulator.wake()
        snapshot = await engine.poll()  # dawn: link must be able to reconnect
        assert snapshot.state is EngineState.ONLINE
    finally:
        await engine.close()


async def test_empty_first_statics_frame_is_refetched(emulator):
    """An empty static response is retried on the next poll."""
    emulator.inject("empty_data")
    engine = _engine(emulator)
    try:
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE
        assert "TYP" not in snapshot.values
        assert "PIN" not in snapshot.values

        snapshot = await engine.poll()  # must re-fetch statics this time
        assert "TYP" in snapshot.values
        assert "PIN" in snapshot.values
    finally:
        await engine.close()


async def test_partial_first_statics_frame_gets_one_backfill(emulator):
    """One device key must not suppress a later fetch of missing statics."""
    emulator.respond_only(["TYP"])
    engine = _engine(emulator)
    try:
        first = await engine.poll()
        assert "TYP" in first.values
        assert "PIN" not in first.values

        emulator.respond_only(None)
        second = await engine.poll()
        assert "PIN" in second.values
    finally:
        await engine.close()


async def test_permanently_partial_statics_stop_after_one_backfill():
    """Unsupported static fields must not trigger a request on every poll."""
    link = _PartialStaticLink()
    engine = ConnectionEngine(
        link, address=1, sun_below=lambda: False, grace_seconds=0.0
    )

    for _ in range(3):
        await engine.poll()
    await engine.close()

    static_requests = [payload for payload in link.payloads if "PIN" in payload]
    assert len(static_requests) == 2


# --- Fault timing, escalation, shutdown, retries, and serialization ----------


async def test_fault_since_is_not_backdated_across_a_night(emulator):
    """A nighttime reclassification starts a fresh fault clock at dawn."""
    sun_below = False  # noon
    engine = _engine(emulator, sun_below=lambda: sun_below, grace=0.0)
    try:
        await engine.poll()
        emulator.dark = True  # sudden daytime fault at "14:00", sun still up
        fault_snapshot = await engine.poll()
        assert fault_snapshot.state is EngineState.OFFLINE_FAULT
        assert fault_snapshot.fault_since is not None

        sun_below = True  # dusk falls; the same unresolved outage now reads EXPECTED
        expected_snapshot = await engine.poll()
        assert expected_snapshot.state is EngineState.OFFLINE_EXPECTED
        assert expected_snapshot.fault_since is None

        sun_below = False  # dawn: sun is back up, device still not responding
        dawn_snapshot = await engine.poll()
        assert dawn_snapshot.state is EngineState.OFFLINE_FAULT
        assert dawn_snapshot.fault_since is not None
        assert dawn_snapshot.fault_since > fault_snapshot.fault_since  # fresh
    finally:
        await engine.close()


async def test_armed_escalates_to_fault_after_sustained_anomaly(emulator):
    """A sustained armed daytime outage eventually becomes a fault.

    Repeated failures call the policy directly to avoid slow network timeouts;
    separate tests cover every poll-to-failure path.
    """
    clock = _FakeClock()
    engine = _engine(emulator, sun_below=lambda: False, clock=clock)
    try:
        await engine.poll()
        emulator.begin_dusk(announce_seconds=0.4)
        armed_snapshot = await engine.poll()  # sees SYS 20002 + low PDC -> arms
        assert armed_snapshot.shutdown_announced is True

        for _ in range(9):  # below the 10-failure floor: no escalation yet
            snapshot = await engine._on_failure()
        assert snapshot.state is EngineState.OFFLINE_EXPECTED
        assert snapshot.expected_outside_twilight is True
        assert snapshot.shutdown_announced is True

        clock.advance(ARMED_ESCALATION_SECONDS + 1)
        snapshot = await engine._on_failure()  # 10th failed probe, window elapsed
        assert snapshot.state is EngineState.OFFLINE_FAULT
        assert snapshot.shutdown_announced is False  # armed cleared on escalation
        assert snapshot.expected_outside_twilight is False
        assert snapshot.fault_since is not None
    finally:
        await engine.close()


async def test_armed_anomaly_below_window_stays_expected(emulator):
    """Escalation requires both the failure count and elapsed-time limits."""
    clock = _FakeClock()
    engine = _engine(emulator, sun_below=lambda: False, clock=clock)
    try:
        await engine.poll()
        emulator.begin_dusk(announce_seconds=0.4)
        await engine.poll()  # arms

        clock.advance(100.0)  # well under ARMED_ESCALATION_SECONDS
        snapshot = None
        for _ in range(ARMED_ESCALATION_MIN_FAILURES + 5):
            snapshot = await engine._on_failure()
        assert snapshot.state is EngineState.OFFLINE_EXPECTED
        assert snapshot.expected_outside_twilight is True
    finally:
        await engine.close()


async def test_armed_with_sun_below_needs_no_escalation(emulator):
    """An outage explained by darkness never escalates."""
    clock = _FakeClock()
    engine = _engine(emulator, sun_below=lambda: True, clock=clock)
    try:
        await engine.poll()
        emulator.begin_dusk(announce_seconds=0.4)
        await engine.poll()  # arms, but sun is already below threshold

        clock.advance(ARMED_ESCALATION_SECONDS * 2)
        snapshot = None
        for _ in range(ARMED_ESCALATION_MIN_FAILURES + 5):
            snapshot = await engine._on_failure()
        assert snapshot.state is EngineState.OFFLINE_EXPECTED
        assert snapshot.expected_outside_twilight is False
    finally:
        await engine.close()


async def test_armed_daytime_outage_becomes_expected_at_dusk(emulator):
    """Twilight cancels escalation for an inverter that remains offline."""
    sun_below = False
    clock = _FakeClock()
    engine = _engine(emulator, sun_below=lambda: sun_below, clock=clock)
    try:
        await engine.poll()
        emulator.begin_dusk(announce_seconds=0.4)
        await engine.poll()

        for _ in range(9):
            snapshot = await engine._on_failure()
        assert snapshot.state is EngineState.OFFLINE_EXPECTED
        assert snapshot.expected_outside_twilight is True

        clock.advance(ARMED_ESCALATION_SECONDS + 1)
        sun_below = True
        snapshot = await engine._on_failure()

        assert snapshot.state is EngineState.OFFLINE_EXPECTED
        assert snapshot.shutdown_announced is True
        assert snapshot.expected_outside_twilight is False
        assert snapshot.fault_since is None
    finally:
        await engine.close()


async def test_armed_night_outage_escalates_if_still_offline_after_dawn(emulator):
    """A normal night becomes a fault if the inverter misses its dawn window."""
    sun_below = True
    clock = _FakeClock()
    engine = _engine(emulator, sun_below=lambda: sun_below, clock=clock)
    try:
        await engine.poll()
        emulator.begin_dusk(announce_seconds=0.4)
        await engine.poll()
        night_snapshot = await engine._on_failure()
        assert night_snapshot.state is EngineState.OFFLINE_EXPECTED
        assert night_snapshot.expected_outside_twilight is False

        sun_below = False
        for _ in range(9):
            dawn_snapshot = await engine._on_failure()
        assert dawn_snapshot.state is EngineState.OFFLINE_EXPECTED
        assert dawn_snapshot.expected_outside_twilight is True

        clock.advance(ARMED_ESCALATION_SECONDS + 1)
        fault_snapshot = await engine._on_failure()
        assert fault_snapshot.state is EngineState.OFFLINE_FAULT
        assert fault_snapshot.shutdown_announced is False
        assert fault_snapshot.fault_since is not None
    finally:
        await engine.close()


async def test_poll_after_close_never_touches_link(emulator):
    """A poll after terminal close returns cached state without network I/O."""
    link = SolarmaxLink(*emulator.addr, response_timeout=0.5)
    engine = ConnectionEngine(
        link, address=1, sun_below=lambda: False, grace_seconds=0.0
    )
    online_snapshot = await engine.poll()
    assert online_snapshot.state is EngineState.ONLINE
    attempts_before = link.attempts

    await engine.close()
    snapshot = await engine.poll()

    assert link.attempts == attempts_before
    assert snapshot.state is EngineState.ONLINE  # previous snapshot, not touched


async def test_timeout_is_retried_once_within_the_poll(emulator):
    """One dropped response is retried within the same poll."""
    engine = _engine(emulator, timeout=1.0)
    try:
        await engine.poll()  # baseline: statics loaded
        emulator.inject("drop")  # swallow exactly the next request, no reply
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE
    finally:
        await engine.close()


async def test_successful_responses_are_parsed_once(emulator, monkeypatch):
    """One normal poll parses each static and telemetry response once."""
    original = connection_module.parse_response
    parsed_responses: list[str] = []

    def track_parse(response: str, verify_checksum: bool):
        parsed_responses.append(response)
        return original(response, verify_checksum)

    monkeypatch.setattr(connection_module, "parse_response", track_parse)
    engine = _engine(emulator)
    try:
        snapshot = await engine.poll()
        assert snapshot.state is EngineState.ONLINE
        assert len(parsed_responses) == 2  # one static and one telemetry response
    finally:
        await engine.close()


async def test_close_during_poll_prevents_the_next_request():
    """Close drains the active poll and prevents its next network request."""
    link = _BlockingStaticLink()
    engine = ConnectionEngine(
        link, address=1, sun_below=lambda: False, grace_seconds=0.0
    )
    poll_task = asyncio.create_task(engine.poll())
    await asyncio.wait_for(link.static_started.wait(), timeout=1)

    close_task = asyncio.create_task(engine.close())
    await asyncio.sleep(0)
    close_returned_before_poll = close_task.done()
    link.release_static.set()
    await asyncio.gather(poll_task, close_task)

    assert close_returned_before_poll is False
    assert link.request_count == 1


async def test_concurrent_polls_are_serialized(emulator):
    """Scheduled and debounced refreshes serialize access to the link."""
    link = SolarmaxLink(*emulator.addr)
    engine = ConnectionEngine(
        link, address=1, sun_below=lambda: False, grace_seconds=0.0
    )
    try:
        results = await asyncio.gather(engine.poll(), engine.poll())
        assert all(s.state is EngineState.ONLINE for s in results)
        assert link.attempts == 1  # one connection served both polls, serially
    finally:
        await engine.close()


async def test_validation_handoff_disconnects_and_pauses_poll():
    """A handoff drops the link and excludes polls until its context exits."""
    link = _RecordingLink()
    engine = ConnectionEngine(link, address=1, sun_below=lambda: False)

    async with engine.validation_handoff():
        poll = asyncio.create_task(engine.poll())
        await asyncio.sleep(0)
        assert link.disconnect_calls == 1
        assert not poll.done()

    await poll
    assert link.request_count > 0


async def test_validation_handoff_releases_poll_after_cancellation():
    """Cancellation of a handoff must not leave the poll lock held."""
    engine = ConnectionEngine(_RecordingLink(), address=1, sun_below=lambda: False)

    async def cancelled_handoff() -> None:
        async with engine.validation_handoff():
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cancelled_handoff()
    assert (await engine.poll()).state is EngineState.ONLINE


def test_default_response_timeout_and_poll_budget():
    link = SolarmaxLink("127.0.0.1", 1)
    assert link.response_timeout == 3.5
    assert POLL_BUDGET_SECONDS == 15.0
