"""DataUpdateCoordinator for Solarmax."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.helpers.issue_registry import (
    async_get as async_get_issue_registry,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .configuration import endpoint_unique_id, entry_option
from .connection import ConnectionEngine, EngineSnapshot, EngineState, SolarmaxLink
from .const import (
    CONF_ADDRESS,
    CONF_HOST,
    CONF_PORT,
    CONF_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_VERIFY_CHECKSUM,
    DAWN_POLL_SECONDS,
    DEFAULT_ADDRESS,
    DEFAULT_TWILIGHT_ELEVATION_THRESHOLD,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VERIFY_CHECKSUM,
    DEVICE_KEY_BUILD,
    DEVICE_KEY_FIRMWARE,
    DEVICE_KEY_SERIAL,
    DEVICE_KEY_TYPE,
    DEVICE_TYPE_MAP,
    DOMAIN,
    FAULT_POLL_SECONDS,
    FAULT_REPAIR_SECONDS,
    NIGHT_POLL_SECONDS,
    REPAIR_PENDING,
    REPAIR_PENDING_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

_DAWN_ELEVATION_THRESHOLD = -6.0
_CLOCK_DAWN_HOUR = 5
_CLOCK_NIGHT_HOUR = 20


class SolarmaxCoordinator(DataUpdateCoordinator[EngineSnapshot]):
    """Poll a Solarmax inverter through a ConnectionEngine.

    Every poll cycle produces an EngineSnapshot, never an exception — this
    is what makes the coordinator a thin *always-succeed* adapter. HA's
    DataUpdateCoordinator suppresses async_update_listeners() while polls
    are failing (last_update_success is False); since _async_update_data
    never raises, that suppression path can never engage, which is exactly
    what closes the dusk/dawn listener-starvation bug this redesign fixes.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self._entry = entry
        self._endpoint_unique_id = endpoint_unique_id(
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            entry.data.get(CONF_ADDRESS, DEFAULT_ADDRESS),
        )

        link = SolarmaxLink(
            host=entry.data[CONF_HOST],
            port=entry.data[CONF_PORT],
        )
        self._engine = ConnectionEngine(
            link,
            address=entry.data.get(CONF_ADDRESS, DEFAULT_ADDRESS),
            sun_below=self.sun_below_threshold,
            verify_checksum=entry_option(
                entry, CONF_VERIFY_CHECKSUM, DEFAULT_VERIFY_CHECKSUM
            ),
            today=lambda: dt_util.now().date(),
        )

        self._configured_interval = timedelta(
            seconds=entry_option(entry, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        )
        self._sun_source = "unknown"
        self._sun_fallback_warned = False
        self.sensor_setup_complete = False

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self._configured_interval,
            always_update=False,  # Only notify listeners when data changes
        )

        self._repair_issue_id = f"connection_issues_{entry.entry_id}"

    @property
    def engine(self) -> ConnectionEngine:
        """Return the underlying connection engine."""
        return self._engine

    @property
    def sun_source(self) -> str:
        """Return the source used by the most recent sun check."""
        return self._sun_source

    @property
    def _twilight_elevation_threshold(self) -> float:
        """Return the configured twilight elevation threshold in degrees."""
        return float(
            entry_option(
                self._entry,
                CONF_TWILIGHT_ELEVATION_THRESHOLD,
                DEFAULT_TWILIGHT_ELEVATION_THRESHOLD,
            )
        )

    def sun_below_threshold(self) -> bool:
        """Return True when the sun is below the configured twilight threshold.

        Passed to ConnectionEngine as its `sun_below` callback. Falls back to
        a fixed 20:00-06:00 clock window when no `sun.sun` entity is available.
        """
        sun_component = self._sun_component()
        if sun_component is not None:
            try:
                if sun_component.state == "below_horizon":
                    return True
                elevation = sun_component.attributes.get("elevation")
                if (
                    elevation is not None
                    and elevation < self._twilight_elevation_threshold
                ):
                    return True
                return False
            except Exception as e:  # noqa: BLE001 - defensive, must not fail a poll
                _LOGGER.debug("Error checking sun position: %s", e)

        current_hour = self._clock_fallback_hour()
        return current_hour >= 20 or current_hour < 6

    def _sun_component(self) -> State | None:
        """Return the sun entity and remember when it is available."""
        try:
            sun_component = self.hass.states.get("sun.sun")
        except Exception as e:  # noqa: BLE001 - defensive, must not fail a poll
            _LOGGER.debug("Error reading sun.sun: %s", e)
            return None
        if sun_component is not None and sun_component.state not in (
            STATE_UNAVAILABLE,
            STATE_UNKNOWN,
        ):
            self._sun_source = "sun.sun"
            return sun_component
        return None

    def _clock_fallback_hour(self) -> int:
        """Return the current hour and record use of the clock fallback."""
        self._sun_source = "clock_fallback"
        if not self._sun_fallback_warned:
            _LOGGER.warning(
                "sun.sun is unavailable; using the 20:00-06:00 clock "
                "fallback with fast polling from 05:00"
            )
            self._sun_fallback_warned = True
        return dt_util.now().hour

    def _fast_expected_polling(self) -> bool:
        """Return whether an expected outage needs the recovery cadence."""
        sun_component = self._sun_component()
        if sun_component is not None:
            try:
                elevation = sun_component.attributes.get("elevation")
                if elevation is None:
                    return sun_component.state != "below_horizon"
                return elevation >= self._twilight_elevation_threshold or (
                    sun_component.attributes.get("rising") is True
                    and elevation >= _DAWN_ELEVATION_THRESHOLD
                )
            except Exception as e:  # noqa: BLE001 - defensive, must not fail a poll
                _LOGGER.debug("Error checking sun position: %s", e)

        current_hour = self._clock_fallback_hour()
        return _CLOCK_DAWN_HOUR <= current_hour < _CLOCK_NIGHT_HOUR

    @callback
    def async_handle_midnight(self, now: datetime) -> None:
        """Force a listener refresh at local midnight for Energy Day sensors.

        `coordinator.data` is reassigned every poll regardless — the engine
        never raises, so that assignment always runs. What's suppressed is
        `async_update_listeners()`, and the mechanism is `always_update=False`
        plus `EngineSnapshot` equality (its `diagnostics` field is
        `compare=False` for exactly this reason): HA only notifies listeners
        when the new snapshot differs from the last one it notified with.
        Not `last_update_success` — that stays True all night, since
        `_async_update_data` never raises. Two consecutive OFFLINE_EXPECTED
        snapshots overnight compare equal, so nothing re-reads native_value
        between dusk and dawn. Energy Day depends on noticing midnight, so
        we push one update ourselves, bypassing the equality check.

        That also makes this the *only* state write between dusk and dawn
        along the *armed* path — the inverter announced its own shutdown
        (SYS 20002 or low PDC) before going dark, so ArmingTracker.armed
        stays True all night and, per `armed or sun_below`, classification
        holds OFFLINE_EXPECTED straight through the dawn gap (sun already
        above the twilight threshold, inverter not yet answering) with no
        write at all — which is what keeps the night policy safe there. The
        *sun-fallback* path (no shutdown announcement was ever observed, so
        armed never latched) has no such protection: once the sun clears the
        threshold, `armed or sun_below` goes False and the snapshot moves off
        OFFLINE_EXPECTED (to UNKNOWN/OFFLINE_FAULT) — a real change, so this
        one *does* notify. But `_night_policy` requires state ==
        OFFLINE_EXPECTED, so that write only ever produces `unavailable`,
        never a numeric value — and a TOTAL_INCREASING sensor going
        unavailable is not read as a rise. If state were written with a
        *numeric* value in that window instead, a HOLD_UNTIL_MIDNIGHT sensor
        like KDY would jump from the midnight 0 back up to yesterday's
        total, and HA reads that rise on a TOTAL_INCREASING sensor as real
        growth — injecting a phantom day's energy into the Energy dashboard
        every morning. Any future change that adds a second state-write path
        in that window — a forced homeassistant.update_entity call, an
        always_update/availability-polling change, or RestoreSensor work —
        reopens this hole and needs the same care.
        """
        self.async_update_listeners()

    async def _async_update_data(self) -> EngineSnapshot:
        """Poll the engine and hand back a snapshot. Never raises."""
        try:
            snapshot = await self._engine.poll()
        except Exception:  # noqa: BLE001 - the coordinator contract never raises
            _LOGGER.exception(
                "Unexpected error polling the inverter; treating as a fault"
            )
            snapshot = self._restate_as_fault()

        await self._async_handle_snapshot(snapshot)
        self.update_interval = self._interval_for(snapshot)
        return snapshot

    def _restate_as_fault(self) -> EngineSnapshot:
        """Build a fault snapshot after an unexpected exception from poll().

        ConnectionEngine.poll() is documented to never raise; this only
        guards against an unforeseen bug so _async_update_data can still
        provably never raise.
        """
        previous = self.data
        if previous is None:
            return EngineSnapshot(
                state=EngineState.OFFLINE_FAULT,
                values={},
                shutdown_announced=False,
                reconnecting=False,
                expected_outside_twilight=False,
                fault_since=dt_util.utcnow(),
                diagnostics={},
            )
        return replace(
            previous,
            state=EngineState.OFFLINE_FAULT,
            fault_since=previous.fault_since or dt_util.utcnow(),
        )

    def _interval_for(self, snapshot: EngineSnapshot) -> timedelta:
        """Adapt cadence for expected outages and active daytime failures."""
        if snapshot.state is EngineState.OFFLINE_EXPECTED:
            interval = (
                DAWN_POLL_SECONDS
                if self._fast_expected_polling()
                else NIGHT_POLL_SECONDS
            )
            return timedelta(seconds=interval)
        if snapshot.state is EngineState.OFFLINE_FAULT or (
            snapshot.state is EngineState.UNKNOWN and snapshot.reconnecting
        ):
            return min(
                self._configured_interval,
                timedelta(seconds=FAULT_POLL_SECONDS),
            )
        return self._configured_interval

    async def _async_handle_snapshot(self, snapshot: EngineSnapshot) -> None:
        """Log transitions and synchronize the connection repair issue."""
        self._log_state_transition(snapshot)
        issue = async_get_issue_registry(self.hass).async_get_issue(
            DOMAIN, self._repair_issue_id
        )
        issue_data = issue.data or {} if issue is not None else {}
        if issue_data.get(REPAIR_PENDING) == 1:
            pending_endpoint = issue_data.get(REPAIR_PENDING_ENDPOINT)
            current_endpoint = endpoint_unique_id(
                self._entry.data[CONF_HOST],
                self._entry.data[CONF_PORT],
                self._entry.data.get(CONF_ADDRESS, DEFAULT_ADDRESS),
            )
            current_or_restored_runtime = self._endpoint_unique_id == current_endpoint
            if snapshot.state is EngineState.ONLINE and (
                pending_endpoint is None
                or pending_endpoint == self._endpoint_unique_id
                or current_or_restored_runtime
            ):
                self._clear_repair_issue()
            return
        fault_seconds = self._repairable_fault_seconds(snapshot)
        if fault_seconds is None:
            self._clear_repair_issue()
            return
        self._create_repair_issue(fault_seconds)

    def _log_state_transition(self, snapshot: EngineSnapshot) -> None:
        """Log when the incoming snapshot changes connection state."""
        # The base coordinator assigns self.data after _async_update_data returns.
        previous_state = self.data.state if self.data else None
        if snapshot.state is previous_state:
            return
        if snapshot.state is EngineState.OFFLINE_FAULT:
            _LOGGER.warning(
                "Inverter %s:%s unreachable (fault)",
                self._entry.data[CONF_HOST],
                self._entry.data[CONF_PORT],
            )
            return
        _LOGGER.info("Connection state %s -> %s", previous_state, snapshot.state)

    @staticmethod
    def _repairable_fault_seconds(snapshot: EngineSnapshot) -> float | None:
        """Return the age of a sustained fault that warrants a repair issue."""
        if (
            snapshot.state is not EngineState.OFFLINE_FAULT
            or snapshot.fault_since is None
        ):
            return None
        fault_seconds = (dt_util.utcnow() - snapshot.fault_since).total_seconds()
        return fault_seconds if fault_seconds >= FAULT_REPAIR_SECONDS else None

    def _create_repair_issue(self, fault_seconds: float) -> None:
        """Create or refresh the repair issue for the current fault."""
        issue_context: dict[str, str] = {
            "host": self._entry.data[CONF_HOST],
            "port": str(self._entry.data[CONF_PORT]),
            "minutes": str(int(fault_seconds // 60)),
        }
        async_create_issue(
            self.hass,
            DOMAIN,
            self._repair_issue_id,
            is_fixable=True,
            is_persistent=False,
            severity=IssueSeverity.ERROR,
            translation_key="connection_issues",
            translation_placeholders=issue_context,
            # The repair API's mutable data mapping has a broader value type.
            data=cast("dict[str, str | int | float | None]", issue_context),
        )

    def _clear_repair_issue(self) -> None:
        """End repair bookkeeping for a recovered or reclassified episode."""
        async_delete_issue(self.hass, DOMAIN, self._repair_issue_id)

    def _static_raw(self, key: str) -> Any:
        """Return the raw_value for a static device-info key, or None."""
        if self.data is None:
            return None
        return self.data.values.get(key, {}).get("raw_value")

    @property
    def device_model(self) -> str | None:
        """Return the detected inverter model, from the latest snapshot."""
        typ_value = self._static_raw(DEVICE_KEY_TYPE)
        if typ_value is None:
            return None
        return DEVICE_TYPE_MAP.get(typ_value, f"Unknown ({typ_value})")

    @property
    def sw_version(self) -> str | None:
        """Return the detected firmware version, from the latest snapshot."""
        swv_value = self._static_raw(DEVICE_KEY_FIRMWARE)
        if swv_value is None:
            return None
        bdn_value = self._static_raw(DEVICE_KEY_BUILD)
        if bdn_value is not None:
            return f"{swv_value} (build {bdn_value})"
        return str(swv_value)

    @property
    def serial_number(self) -> str | None:
        """Return the detected serial number, from the latest snapshot."""
        din_value = self._static_raw(DEVICE_KEY_SERIAL)
        if din_value is None:
            return None
        return str(din_value)

    @property
    def last_successful_update(self) -> datetime | None:
        """Return the local-time timestamp of the last successful poll.

        Converted to local time because sensor._is_new_day() compares
        .date() against dt_util.now().date() — a raw UTC value would shift
        the KDY midnight rollover by the timezone offset.
        """
        if self.data is None:
            return None
        last = self.data.diagnostics.get("last_successful_poll")
        if not isinstance(last, datetime):
            return None
        return dt_util.as_local(last)


# Typed config entry: gives `entry.runtime_data` a real type instead of Any,
# so mypy can actually check every coordinator access through it.
# Plain assignment rather than PEP 695 `type` — pyproject targets >=3.11.
SolarmaxConfigEntry = ConfigEntry[SolarmaxCoordinator]
