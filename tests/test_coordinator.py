"""Test the Solarmax coordinator."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmax.connection import (
    ConnectionEngine,
    EngineSnapshot,
    EngineState,
)
from custom_components.solarmax.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    DAWN_POLL_SECONDS,
    DOMAIN,
    FAULT_REPAIR_SECONDS,
    NIGHT_POLL_SECONDS,
)
from custom_components.solarmax.coordinator import SolarmaxCoordinator


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Inverter",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
            CONF_UPDATE_INTERVAL: 30,
        },
        entry_id="test_entry",
        unique_id="192.168.1.100:12345",
    )


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_config_entry):
    """Create a coordinator instance."""
    return SolarmaxCoordinator(hass, mock_config_entry)


def _snap(
    state: EngineState,
    *,
    fault_since: datetime | None = None,
    values: dict[str, dict[str, float | int]] | None = None,
    diagnostics: dict[str, object] | None = None,
    reconnecting: bool = False,
) -> EngineSnapshot:
    """Build a minimal EngineSnapshot for coordinator-level tests."""
    return EngineSnapshot(
        state=state,
        values=values or {},
        shutdown_announced=False,
        reconnecting=reconnecting,
        expected_outside_twilight=False,
        fault_since=fault_since,
        diagnostics=diagnostics or {},
    )


class _StubEngine:
    """Stand-in for ConnectionEngine: returns a prepared snapshot, or raises.

    The real ConnectionEngine has its own emulator-driven coverage
    (test_connection_engine.py); the coordinator is tested against this
    stub so it only needs to trust the engine's documented contract.
    """

    def __init__(
        self,
        state: EngineState = EngineState.ONLINE,
        *,
        fault_since: datetime | None = None,
        values: dict[str, dict[str, float | int]] | None = None,
        diagnostics: dict[str, object] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._snapshot = _snap(
            state, fault_since=fault_since, values=values, diagnostics=diagnostics
        )
        self._exc = exc

    async def poll(self) -> EngineSnapshot:
        if self._exc is not None:
            raise self._exc
        return self._snapshot

    async def close(self) -> None:
        """No-op close, matching ConnectionEngine.close()'s signature."""


# --- Coordinator snapshot contract ------------------------------------------


async def test_update_returns_snapshot_and_never_raises(hass, mock_config_entry):
    """A poll that returns OFFLINE_FAULT must not raise UpdateFailed."""
    coordinator = SolarmaxCoordinator(hass, mock_config_entry)
    coordinator._engine = _StubEngine(state=EngineState.OFFLINE_FAULT)
    snapshot = await coordinator._async_update_data()  # no UpdateFailed
    assert snapshot.state is EngineState.OFFLINE_FAULT


async def test_interval_follows_state(hass, mock_config_entry):
    """Online polling uses the configured cadence; faults retain their cap."""
    coordinator = SolarmaxCoordinator(hass, mock_config_entry)
    assert coordinator._interval_for(_snap(EngineState.ONLINE)) == timedelta(seconds=30)
    # A configured cadence faster than the fault cap remains unchanged.
    assert coordinator._interval_for(_snap(EngineState.OFFLINE_FAULT)) == timedelta(
        seconds=30
    )


def test_runtime_option_overrides_legacy_update_interval(hass: HomeAssistant):
    """A migrated option must take precedence over legacy entry data."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
            CONF_UPDATE_INTERVAL: 30,
        },
        options={CONF_UPDATE_INTERVAL: 90},
    )

    coordinator = SolarmaxCoordinator(hass, entry)

    assert coordinator._interval_for(_snap(EngineState.ONLINE)) == timedelta(seconds=90)


@pytest.mark.parametrize(
    ("state", "elevation", "rising", "expected_seconds"),
    [
        ("below_horizon", -6.1, True, NIGHT_POLL_SECONDS),
        ("below_horizon", -6.0, True, DAWN_POLL_SECONDS),
        ("above_horizon", 4.0, True, DAWN_POLL_SECONDS),
        ("above_horizon", None, True, DAWN_POLL_SECONDS),
        ("below_horizon", -5.0, False, NIGHT_POLL_SECONDS),
        ("above_horizon", 6.0, False, DAWN_POLL_SECONDS),
    ],
)
def test_expected_offline_interval_tracks_dawn_and_daytime(
    coordinator, state, elevation, rising, expected_seconds
):
    """Fast polling starts at civil dawn and remains active in daytime."""
    coordinator.hass.states.async_set(
        "sun.sun",
        state,
        {"elevation": elevation, "rising": rising},
    )

    assert coordinator._interval_for(_snap(EngineState.OFFLINE_EXPECTED)) == timedelta(
        seconds=expected_seconds
    )


@pytest.mark.parametrize(
    ("hour", "expected_seconds"),
    [
        (4, NIGHT_POLL_SECONDS),
        (5, DAWN_POLL_SECONDS),
        (19, DAWN_POLL_SECONDS),
        (20, NIGHT_POLL_SECONDS),
    ],
)
def test_expected_offline_interval_uses_clock_dawn_fallback(
    coordinator, hour, expected_seconds
):
    """The clock fallback starts recovery polling an hour before daytime."""
    with patch("custom_components.solarmax.coordinator.dt_util.now") as mock_now:
        mock_now.return_value.hour = hour

        assert coordinator._interval_for(
            _snap(EngineState.OFFLINE_EXPECTED)
        ) == timedelta(seconds=expected_seconds)


def test_fault_interval_is_capped_at_one_minute(coordinator):
    """A long configured interval must not delay fault recovery detection."""
    coordinator._configured_interval = timedelta(hours=1)

    assert coordinator._interval_for(_snap(EngineState.OFFLINE_FAULT)) == timedelta(
        seconds=60
    )


def test_reconnecting_unknown_interval_is_capped_at_one_minute(coordinator):
    """Daytime startup failures need the same recovery cadence during grace."""
    coordinator._configured_interval = timedelta(hours=1)

    assert coordinator._interval_for(
        _snap(EngineState.UNKNOWN, reconnecting=True)
    ) == timedelta(seconds=60)


async def test_repair_raised_after_sustained_fault_and_cleared(hass, mock_config_entry):
    """A fault older than FAULT_REPAIR_SECONDS raises the repair issue;
    recovering to ONLINE clears it."""
    coordinator = SolarmaxCoordinator(hass, mock_config_entry)
    old = dt_util.utcnow() - timedelta(seconds=FAULT_REPAIR_SECONDS + 1)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=old)
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id)
    assert issue is not None
    assert issue.data["host"] == "192.168.1.100"  # dialog payload guaranteed

    await coordinator._async_handle_snapshot(_snap(EngineState.ONLINE))
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id) is None
    )


# --- Repair issue: additional coverage --------------------------------------


async def test_repair_not_raised_before_sustained_threshold(coordinator, hass):
    """A fault younger than FAULT_REPAIR_SECONDS does not raise the issue."""
    recent = dt_util.utcnow() - timedelta(seconds=FAULT_REPAIR_SECONDS - 1)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=recent)
    )
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id) is None
    )


async def test_repair_issue_payload_has_host_port_minutes(coordinator, hass):
    """data and translation_placeholders carry exactly host/port/minutes —
    the old {failures} placeholder no longer exists."""
    old = dt_util.utcnow() - timedelta(seconds=FAULT_REPAIR_SECONDS + 60)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=old)
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id)
    assert issue is not None
    assert set(issue.data) == {"host", "port", "minutes"}
    assert issue.translation_placeholders == issue.data
    assert issue.data["port"] == "12345"
    assert issue.data["minutes"] == "6"  # (300 + 60) // 60


async def test_repair_minutes_refresh_for_fault_episode(coordinator, hass):
    """An existing issue reflects the ongoing fault duration."""
    fault_since = dt_util.utcnow() - timedelta(seconds=310)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=fault_since)
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id)
    assert issue is not None
    assert issue.data["minutes"] == "5"  # 310 // 60

    fault_since = dt_util.utcnow() - timedelta(seconds=7300)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=fault_since)
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id)
    assert issue is not None
    assert issue.data["minutes"] == "121"


# --- state-transition logging: the replacement for the old ERROR spew -----


async def test_state_transition_logging(coordinator, caplog):
    """Entering FAULT logs exactly one WARNING; any other transition logs
    INFO; a repeated same-state snapshot logs nothing. This is now the only
    trail of connection state changes, since _async_handle_snapshot never
    raises."""
    logger_name = "custom_components.solarmax.coordinator"
    caplog.set_level(logging.INFO, logger=logger_name)

    def records() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.name == logger_name]

    # A startup fault log identifies the unreachable inverter directly.
    assert coordinator.data is None
    await coordinator._async_handle_snapshot(_snap(EngineState.OFFLINE_FAULT))
    assert len(records()) == 1
    assert records()[0].levelname == "WARNING"
    assert "192.168.1.100:12345" in records()[0].getMessage()
    coordinator.data = _snap(EngineState.OFFLINE_FAULT)
    caplog.clear()

    # OFFLINE_FAULT -> OFFLINE_EXPECTED: one INFO.
    await coordinator._async_handle_snapshot(_snap(EngineState.OFFLINE_EXPECTED))
    assert len(records()) == 1
    assert records()[0].levelname == "INFO"
    coordinator.data = _snap(EngineState.OFFLINE_EXPECTED)
    caplog.clear()

    # OFFLINE_EXPECTED -> OFFLINE_EXPECTED (no change): nothing logged.
    await coordinator._async_handle_snapshot(_snap(EngineState.OFFLINE_EXPECTED))
    assert records() == []


# --- last_successful_update: local-time semantics (KDY midnight reset) -----


async def test_last_successful_update_returns_local_time(coordinator, hass):
    """Convert engine timestamps to local time for midnight policies."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    utc_now = dt_util.utcnow()
    coordinator.data = _snap(
        EngineState.ONLINE, diagnostics={"last_successful_poll": utc_now}
    )
    result = coordinator.last_successful_update
    assert result == dt_util.as_local(utc_now)
    assert result.utcoffset() != timedelta(0)


async def test_last_successful_update_none_when_unavailable(coordinator):
    """No data yet, or a snapshot without the diagnostics key, both read as
    None rather than raising."""
    assert coordinator.last_successful_update is None

    coordinator.data = _snap(EngineState.ONLINE, diagnostics={})
    assert coordinator.last_successful_update is None


# --- Unexpected engine errors -----------------------------------------------


async def test_engine_exception_restates_previous_snapshot_as_fault(coordinator):
    """An unexpected exception from the engine must not escape
    _async_update_data; the previous snapshot's values are preserved."""
    coordinator.data = _snap(
        EngineState.ONLINE, values={"PAC": {"value": 1500.0, "raw_value": 3000}}
    )
    coordinator._engine = _StubEngine(exc=RuntimeError("boom"))

    snapshot = await coordinator._async_update_data()

    assert snapshot.state is EngineState.OFFLINE_FAULT
    assert snapshot.values == {"PAC": {"value": 1500.0, "raw_value": 3000}}
    assert snapshot.fault_since is not None


async def test_engine_exception_with_no_previous_data_builds_empty_fault(
    coordinator,
):
    """If the very first poll raises, there is no previous snapshot to
    restate — fall back to an empty OFFLINE_FAULT snapshot."""
    assert coordinator.data is None
    coordinator._engine = _StubEngine(exc=RuntimeError("boom"))

    snapshot = await coordinator._async_update_data()

    assert snapshot.state is EngineState.OFFLINE_FAULT
    assert snapshot.values == {}
    assert snapshot.fault_since is not None


async def test_engine_exception_preserves_existing_fault_since(coordinator):
    """A second unrelated exception while already faulted must not push
    fault_since forward."""
    original_fault_since = dt_util.utcnow() - timedelta(seconds=120)
    coordinator.data = _snap(
        EngineState.OFFLINE_FAULT, fault_since=original_fault_since
    )
    coordinator._engine = _StubEngine(exc=RuntimeError("boom again"))

    snapshot = await coordinator._async_update_data()

    assert snapshot.fault_since == original_fault_since


# --- sun_below_threshold: ported from the removed _is_night_time -----------


async def test_sun_below_threshold_with_sun_component(coordinator):
    """Test threshold detection with sun component."""
    coordinator.hass.states.async_set("sun.sun", "below_horizon")
    assert coordinator.sun_below_threshold() is True

    coordinator.hass.states.async_set("sun.sun", "above_horizon")
    assert coordinator.sun_below_threshold() is False


async def test_sun_below_threshold_dusk_twilight(coordinator):
    """Test that low sun elevation above the horizon is treated as below-threshold."""
    coordinator.hass.states.async_set("sun.sun", "above_horizon", {"elevation": 2.0})
    assert coordinator.sun_below_threshold() is True

    coordinator.hass.states.async_set("sun.sun", "above_horizon", {"elevation": 30.0})
    assert coordinator.sun_below_threshold() is False

    coordinator.hass.states.async_set("sun.sun", "above_horizon", {})
    assert coordinator.sun_below_threshold() is False


async def test_sun_below_threshold_configurable_twilight_threshold(
    hass: HomeAssistant,
):
    """Test that the twilight elevation threshold is configurable."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Inverter",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
            CONF_UPDATE_INTERVAL: 30,
            CONF_TWILIGHT_ELEVATION_THRESHOLD: 10,
        },
        entry_id="test_entry_custom_threshold",
        unique_id="192.168.1.100:12345:custom",
    )
    custom_coordinator = SolarmaxCoordinator(hass, entry)

    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 7.0})
    assert custom_coordinator.sun_below_threshold() is True

    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 15.0})
    assert custom_coordinator.sun_below_threshold() is False


def test_sun_below_threshold_fallback(coordinator):
    """Test the clock-based fallback used when no sun component exists."""
    with patch("custom_components.solarmax.coordinator.dt_util.now") as mock_now:
        mock_time = MagicMock()
        mock_time.hour = 22
        mock_now.return_value = mock_time
        assert coordinator.sun_below_threshold() is True

        mock_time.hour = 14
        assert coordinator.sun_below_threshold() is False

        mock_time.hour = 5
        assert coordinator.sun_below_threshold() is True


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
def test_unavailable_sun_state_uses_clock_fallback(coordinator, state):
    """An unusable sun entity must not suppress the clock fallback."""
    coordinator.hass.states.async_set("sun.sun", state, {})

    with patch("custom_components.solarmax.coordinator.dt_util.now") as mock_now:
        mock_now.return_value.hour = 22
        assert coordinator.sun_below_threshold() is True
        assert coordinator._fast_expected_polling() is False

    assert coordinator.sun_source == "clock_fallback"


def test_invalid_sun_elevation_uses_clock_fallback(coordinator):
    """Malformed sun attributes must not break classification or scheduling."""
    coordinator.hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {"elevation": "invalid", "rising": True},
    )

    with patch("custom_components.solarmax.coordinator.dt_util.now") as mock_now:
        mock_now.return_value.hour = 5
        assert coordinator.sun_below_threshold() is True
        assert coordinator._interval_for(
            _snap(EngineState.OFFLINE_EXPECTED)
        ) == timedelta(seconds=DAWN_POLL_SECONDS)


def test_sun_read_error_uses_clock_fallback(coordinator):
    """A failed state lookup must use the safe clock fallback."""
    with (
        patch.object(
            type(coordinator.hass.states),
            "get",
            side_effect=RuntimeError("state machine unavailable"),
        ),
        patch("custom_components.solarmax.coordinator.dt_util.now") as mock_now,
    ):
        mock_now.return_value.hour = 22
        assert coordinator.sun_below_threshold() is True

    assert coordinator.sun_source == "clock_fallback"


def test_sun_source_tracks_active_input(coordinator):
    """Support diagnostics report the source used by the last sun check."""
    assert coordinator.sun_source == "unknown"

    with patch("custom_components.solarmax.coordinator.dt_util.now") as mock_now:
        mock_now.return_value.hour = 22
        coordinator.sun_below_threshold()
    assert coordinator.sun_source == "clock_fallback"

    coordinator.hass.states.async_set(
        "sun.sun",
        "above_horizon",
        {"elevation": 20.0, "rising": True},
    )
    coordinator.sun_below_threshold()
    assert coordinator.sun_source == "sun.sun"


def test_clock_fallback_logs_one_warning(coordinator, caplog):
    """Repeated sun checks must not repeat the missing-entity warning."""
    with (
        patch("custom_components.solarmax.coordinator.dt_util.now") as mock_now,
        caplog.at_level(
            logging.WARNING,
            logger="custom_components.solarmax.coordinator",
        ),
    ):
        mock_now.return_value.hour = 5
        coordinator.sun_below_threshold()
        coordinator._interval_for(_snap(EngineState.OFFLINE_EXPECTED))

    warnings = [
        record
        for record in caplog.records
        if record.name == "custom_components.solarmax.coordinator"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1


# --- device-info properties, read from snapshot.values ----------------------


async def test_device_info_props_from_snapshot_values(coordinator):
    """device_model/sw_version/serial_number read from the latest
    snapshot's values via DEVICE_TYPE_MAP, with no separate fetch step."""
    coordinator.data = _snap(
        EngineState.ONLINE,
        values={
            "TYP": {"value": 20650, "raw_value": 20650},
            "SWV": {"value": 314, "raw_value": 314},
            "BDN": {"value": 5, "raw_value": 5},
            "DIN": {"value": 123456, "raw_value": 123456},
        },
    )
    assert coordinator.device_model == "SolarMax 7TP2"
    assert coordinator.sw_version == "314 (build 5)"
    assert coordinator.serial_number == "123456"


async def test_device_info_props_none_without_data(coordinator):
    """Before the first poll, device-info properties read as None."""
    assert coordinator.data is None
    assert coordinator.device_model is None
    assert coordinator.sw_version is None
    assert coordinator.serial_number is None


# --- engine property ---------------------------------------------------------


def test_engine_property_returns_the_connection_engine(coordinator):
    """engine exposes the ConnectionEngine the coordinator polls."""
    assert isinstance(coordinator.engine, ConnectionEngine)
    assert coordinator.engine is coordinator._engine


# --- midnight handler: kept verbatim from the old suite ---------------------


async def test_async_handle_midnight_notifies_listeners(hass, mock_config_entry):
    """The midnight callback must force listeners to re-read native_value."""
    mock_config_entry.add_to_hass(hass)
    coordinator = SolarmaxCoordinator(hass, mock_config_entry)

    with patch.object(coordinator, "async_update_listeners") as notify:
        coordinator.async_handle_midnight(dt_util.now())

    notify.assert_called_once()


# --- Repair-issue episodes and the dismissal window -------------------------


async def test_ignored_fault_issue_stays_ignored_when_refreshed(coordinator, hass):
    """Native Ignore persists when the same stable issue is refreshed."""
    old = dt_util.utcnow() - timedelta(seconds=FAULT_REPAIR_SECONDS + 60)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=old)
    )
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id)
        is not None
    )

    ir.async_get(hass).async_ignore(DOMAIN, coordinator._repair_issue_id, True)

    # Same fault, further aged, on the next poll.
    older = dt_util.utcnow() - timedelta(seconds=FAULT_REPAIR_SECONDS + 120)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=older)
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id)
    assert issue is not None
    assert issue.dismissed_version is not None


async def test_deleted_issue_is_recreated_without_custom_suppression(coordinator, hass):
    """Deletion is not a substitute for native Ignore."""
    old = dt_util.utcnow() - timedelta(seconds=FAULT_REPAIR_SECONDS + 60)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=old)
    )
    ir.async_delete_issue(hass, DOMAIN, coordinator._repair_issue_id)

    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=old)
    )
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id)
        is not None
    )


async def test_repair_new_episode_after_recovery_recreates_immediately(
    coordinator, hass
):
    """Recovery ends the ignored episode and the next fault is visible."""
    old = dt_util.utcnow() - timedelta(seconds=FAULT_REPAIR_SECONDS + 60)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=old)
    )
    ir.async_get(hass).async_ignore(DOMAIN, coordinator._repair_issue_id, True)

    # Recovery ends the episode and clears the dismissal anchor.
    await coordinator._async_handle_snapshot(_snap(EngineState.ONLINE))

    # A brand-new fault raises immediately, with no 24h suppression left over.
    new_old = dt_util.utcnow() - timedelta(seconds=FAULT_REPAIR_SECONDS + 10)
    await coordinator._async_handle_snapshot(
        _snap(EngineState.OFFLINE_FAULT, fault_since=new_old)
    )
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id)
    assert issue is not None
    assert issue.dismissed_version is None


@pytest.mark.parametrize("state", list(EngineState))
async def test_pending_repair_clears_only_on_online_snapshot(coordinator, hass, state):
    """A PAC probe cannot clear the issue before a full ONLINE snapshot."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        coordinator._repair_issue_id,
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="connection_issues",
        data={"verification_pending": 1},
    )
    await coordinator._async_handle_snapshot(_snap(state))
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator._repair_issue_id)
    if state is EngineState.ONLINE:
        assert issue is None
    else:
        assert issue is not None
        assert issue.data["verification_pending"] == 1
