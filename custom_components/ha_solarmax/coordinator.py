"""DataUpdateCoordinator for SolarMax."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, cast

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
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

from .configuration import (
    acquire_endpoint_link,
    endpoint_unique_id,
    entry_option,
    group_entry,
    release_endpoint_link,
)
from .connection import ConnectionEngine, EngineSnapshot, EngineState
from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_GROUP_MEMBERS,
    CONF_HOST,
    CONF_IS_GROUP,
    CONF_NOTIFY_MODE,
    CONF_NOTIFY_RECOVERY,
    CONF_NOTIFY_TARGET,
    CONF_NOTIFY_TIMING,
    CONF_PORT,
    CONF_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_VERIFY_CHECKSUM,
    DAWN_POLL_SECONDS,
    DEFAULT_ADDRESS,
    DEFAULT_DEVICE_NAME,
    DEFAULT_NOTIFY_MODE_GROUP,
    DEFAULT_NOTIFY_MODE_INVERTER,
    DEFAULT_NOTIFY_RECOVERY,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_NOTIFY_TIMING,
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
    GROUP_SENSOR_TYPES,
    NIGHT_POLL_SECONDS,
    NOTIFY_MODE_INHERIT,
    NOTIFY_MODE_OFF,
    NOTIFY_MODE_PERSISTENT,
    NOTIFY_MODE_PUSH,
    NOTIFY_TIMING_IMMEDIATE,
    REPAIR_PENDING,
    REPAIR_PENDING_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)

_DAWN_ELEVATION_THRESHOLD = -6.0
_CLOCK_DAWN_HOUR = 5
_CLOCK_NIGHT_HOUR = 20

# Runtime notification text has no strings.json translation mechanism to draw
# on (it is generated at fault time, not shown in a config/options form), so
# it follows the same hass.config.language lookup already used in
# config_flow.py for suggested device names, with English as the fallback.
_NOTIFY_FAULT_TITLE_EN = "Inverter unreachable"
_NOTIFY_FAULT_MESSAGE_EN = (
    "{name} ({host}:{port}) has been unreachable for {minutes} minutes."
)
_NOTIFY_RECOVERY_TITLE_EN = "Inverter back online"
_NOTIFY_RECOVERY_MESSAGE_EN = "{name} ({host}:{port}) is back online."
_LOCALIZED_NOTIFY_FAULT_TITLE: dict[str, str] = {
    "de": "Wechselrichter nicht erreichbar",
    "fr": "Onduleur injoignable",
}
_LOCALIZED_NOTIFY_FAULT_MESSAGE: dict[str, str] = {
    "de": "{name} ({host}:{port}) ist seit {minutes} Minuten nicht erreichbar.",
    "fr": "{name} ({host}:{port}) est injoignable depuis {minutes} minutes.",
}
_LOCALIZED_NOTIFY_RECOVERY_TITLE: dict[str, str] = {
    "de": "Wechselrichter wieder erreichbar",
    "fr": "Onduleur de nouveau joignable",
}
_LOCALIZED_NOTIFY_RECOVERY_MESSAGE: dict[str, str] = {
    "de": "{name} ({host}:{port}) ist wieder online.",
    "fr": "{name} ({host}:{port}) est de nouveau en ligne.",
}


class SolarmaxCoordinator(DataUpdateCoordinator[EngineSnapshot]):
    """Poll a SolarMax inverter through a ConnectionEngine.

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
        self._host = entry.data[CONF_HOST]
        self._port = entry.data[CONF_PORT]
        self._endpoint_unique_id = endpoint_unique_id(
            self._host,
            self._port,
            entry.data.get(CONF_ADDRESS, DEFAULT_ADDRESS),
        )

        # Shared with any sibling entry already on this host:port -- see
        # acquire_endpoint_link() -- released in async_close().
        link = acquire_endpoint_link(hass, self._host, self._port)
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
        # Whether a fault notification was sent for the fault episode still
        # in progress (if any) -- guards against re-notifying on every poll
        # and gates a recovery notification to episodes that were announced.
        self._fault_notified = False

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

    async def async_close(self) -> None:
        """Tear down this entry's engine and release its (shared) link.

        Order matters: `engine.close()` first quiesces this entry -- no
        poll of ours still running -- before we release the link, since
        releasing may be the last reference and terminally close it.
        """
        await self._engine.close()
        await release_endpoint_link(self.hass, self._host, self._port)

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
        self._maybe_notify(snapshot)
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

    def _effective_notify_settings(self) -> tuple[str, str, str, bool]:
        """Return (mode, target, timing, recovery), resolving INHERIT via the group."""
        mode = entry_option(self._entry, CONF_NOTIFY_MODE, DEFAULT_NOTIFY_MODE_INVERTER)
        entry = self._entry
        if mode == NOTIFY_MODE_INHERIT:
            entry = group_entry(self.hass)
            if entry is None:
                return (NOTIFY_MODE_OFF, "", DEFAULT_NOTIFY_TIMING, False)
            mode = entry_option(entry, CONF_NOTIFY_MODE, DEFAULT_NOTIFY_MODE_GROUP)
        return (
            mode,
            entry_option(entry, CONF_NOTIFY_TARGET, DEFAULT_NOTIFY_TARGET),
            entry_option(entry, CONF_NOTIFY_TIMING, DEFAULT_NOTIFY_TIMING),
            entry_option(entry, CONF_NOTIFY_RECOVERY, DEFAULT_NOTIFY_RECOVERY),
        )

    @callback
    def _maybe_notify(self, snapshot: EngineSnapshot) -> None:
        """Notify once per fault episode, and once on recovery if enabled.

        `self.data` is still the previous snapshot here -- the base
        coordinator assigns the new one only after `_async_update_data`
        returns -- so a None-to-set transition on `fault_since` reliably
        marks the start of a new episode.
        """
        previous_fault_since = self.data.fault_since if self.data else None
        if snapshot.fault_since is not None and previous_fault_since is None:
            self._fault_notified = False
        mode, target, timing, recovery = self._effective_notify_settings()
        if mode == NOTIFY_MODE_OFF:
            return
        if (
            snapshot.state is EngineState.OFFLINE_FAULT
            and not self._fault_notified
            and self._fault_notification_due(snapshot, timing)
        ):
            title, message = self._fault_notification_text(snapshot)
            self._send_notification(mode, target, title, message)
            self._fault_notified = True
        elif (
            snapshot.state is EngineState.ONLINE and self._fault_notified and recovery
        ):
            title, message = self._recovery_notification_text()
            self._send_notification(mode, target, title, message)
            self._fault_notified = False

    @staticmethod
    def _fault_notification_due(snapshot: EngineSnapshot, timing: str) -> bool:
        """Return whether a fault notification is due under the given timing."""
        if timing == NOTIFY_TIMING_IMMEDIATE:
            return True
        if snapshot.fault_since is None:
            return False
        fault_seconds = (dt_util.utcnow() - snapshot.fault_since).total_seconds()
        return fault_seconds >= FAULT_REPAIR_SECONDS

    def _notify_language(self) -> str:
        return self.hass.config.language.split("-")[0].lower()

    def _fault_notification_text(self, snapshot: EngineSnapshot) -> tuple[str, str]:
        language = self._notify_language()
        minutes = 0
        if snapshot.fault_since is not None:
            minutes = int((dt_util.utcnow() - snapshot.fault_since).total_seconds() // 60)
        title = _LOCALIZED_NOTIFY_FAULT_TITLE.get(language, _NOTIFY_FAULT_TITLE_EN)
        message = _LOCALIZED_NOTIFY_FAULT_MESSAGE.get(
            language, _NOTIFY_FAULT_MESSAGE_EN
        ).format(
            name=self._entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
            host=self._host,
            port=self._port,
            minutes=minutes,
        )
        return title, message

    def _recovery_notification_text(self) -> tuple[str, str]:
        language = self._notify_language()
        title = _LOCALIZED_NOTIFY_RECOVERY_TITLE.get(
            language, _NOTIFY_RECOVERY_TITLE_EN
        )
        message = _LOCALIZED_NOTIFY_RECOVERY_MESSAGE.get(
            language, _NOTIFY_RECOVERY_MESSAGE_EN
        ).format(
            name=self._entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
            host=self._host,
            port=self._port,
        )
        return title, message

    def _send_notification(
        self, mode: str, target: str, title: str, message: str
    ) -> None:
        """Dispatch one notification without blocking or raising into the poll."""
        if mode == NOTIFY_MODE_PERSISTENT:
            persistent_notification.async_create(
                self.hass,
                message,
                title=title,
                notification_id=f"{DOMAIN}_{self._entry.entry_id}_connection",
            )
        elif mode == NOTIFY_MODE_PUSH and target:
            self.hass.async_create_task(
                self._async_push(target, title, message),
                f"send SolarMax notification {self._entry.entry_id}",
            )

    async def _async_push(self, target: str, title: str, message: str) -> None:
        """Send a push notification through a Companion App notify service.

        Runs as its own task and waits for the service to finish so a failure
        is logged, while an unreachable device cannot stall the next poll.
        """
        try:
            await self.hass.services.async_call(
                "notify", target, {"title": title, "message": message}, blocking=True
            )
        except Exception:
            _LOGGER.exception("Failed to send push notification to %s", target)

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

    @property
    def last_fault_started(self) -> datetime | None:
        """Return when the most recent unexpected daytime fault began.

        Persists across a later successful reconnect, unlike the transient
        `EngineSnapshot.fault_since` used for repair-issue timing, so it
        remains a durable monitoring/alerting signal after recovery. Never
        set by a night/shutdown-explained disconnect or by the startup
        grace period, so routine restarts, updates, and nightly shutdowns
        are not recorded here.
        """
        if self.data is None:
            return None
        value = self.data.diagnostics.get("last_fault_started")
        return value if isinstance(value, datetime) else None


class SolarmaxGroupCoordinator(DataUpdateCoordinator[dict[str, float]]):
    """Sums each summable register across every other loaded SolarMax entry.

    Not tied to any inverter connection: `data` is a plain {register: sum}
    dict, recomputed from the entity registry and current entity states
    rather than polled over the network, so it is never itself a source of
    connection failures. Polls on the same cadence as inverters do by
    default so an added/removed inverter is picked up within one cycle,
    without needing to track config entry lifecycle events.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the group coordinator."""
        self._entry = entry
        self.sensor_setup_complete = False
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_group",
            update_interval=timedelta(seconds=DEFAULT_UPDATE_INTERVAL),
        )

    async def _async_update_data(self) -> dict[str, float]:
        """Recompute every register's sum; pure computation, cannot fail."""
        return self._compute_sums()

    def _member_entries(self) -> list[ConfigEntry]:
        """Return every loaded, selected inverter entry other than this group.

        `CONF_GROUP_MEMBERS` unset means "every configured inverter" (the
        default), matching the group's original behavior of picking up
        added or removed inverters automatically; once a user saves an
        explicit selection in the group's options, only those entries
        count, same as any other multi-select.
        """
        selected = self._entry.options.get(CONF_GROUP_MEMBERS)
        return [
            other
            for other in self.hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != self._entry.entry_id
            and not other.data.get(CONF_IS_GROUP, False)
            and other.state is ConfigEntryState.LOADED
            and (selected is None or other.entry_id in selected)
        ]

    def _compute_sums(self) -> dict[str, float]:
        registry = er.async_get(self.hass)
        sums: dict[str, float] = {}
        for member in self._member_entries():
            for description in GROUP_SENSOR_TYPES:
                unique_id = f"{member.entry_id}-{description.key.lower()}"
                entity_id = registry.async_get_entity_id(
                    Platform.SENSOR, DOMAIN, unique_id
                )
                if entity_id is None:
                    continue
                state = self.hass.states.get(entity_id)
                if state is None or state.state in (
                    STATE_UNAVAILABLE,
                    STATE_UNKNOWN,
                ):
                    continue
                try:
                    value = float(state.state)
                except (TypeError, ValueError):
                    continue
                sums[description.key] = sums.get(description.key, 0.0) + value
        return sums


# Typed config entry: gives `entry.runtime_data` a real type instead of Any,
# so mypy can actually check every coordinator access through it.
# Plain assignment rather than PEP 695 `type` — pyproject targets >=3.11.
SolarmaxConfigEntry = ConfigEntry["SolarmaxCoordinator | SolarmaxGroupCoordinator"]
