"""Sensor platform for Solarmax integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo, generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .configuration import entry_option
from .connection import EngineState
from .const import (
    CONF_DEVICE_NAME,
    CONF_NIGHT_KEEP_VALUES,
    DEFAULT_NIGHT_KEEP_VALUES,
    DOMAIN,
    NIGHT_POLICY,
    SAL_ALARM_MAP,
    SAL_STATE_MULTIPLE,
    SAL_STATE_UNKNOWN,
    SENSOR_TYPE_ALARM,
    SENSOR_TYPE_STATUS,
    SENSOR_TYPES,
    SYS_STATE_OFFLINE_EXPECTED,
    SYS_STATE_OFFLINE_FAULT,
    SYS_STATE_UNKNOWN,
    SYS_STATUS_MAP,
    NightPolicy,
)
from .coordinator import SolarmaxConfigEntry, SolarmaxCoordinator

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to prevent overwhelming the inverter
PARALLEL_UPDATES = 1

# Non-ONLINE engine states mapped to the status sensor's enum option keys.
_SYS_OFFLINE_STATE_MAP: dict[EngineState, str] = {
    EngineState.OFFLINE_EXPECTED: SYS_STATE_OFFLINE_EXPECTED,
    EngineState.OFFLINE_FAULT: SYS_STATE_OFFLINE_FAULT,
    EngineState.UNKNOWN: SYS_STATE_UNKNOWN,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarmaxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Solarmax sensor platform."""
    coordinator: SolarmaxCoordinator = entry.runtime_data
    device_name = entry.data.get(CONF_DEVICE_NAME, "Solarmax Inverter")

    async_add_entities(
        SolarmaxSensor(coordinator, entry, description, device_name)
        for description in SENSOR_TYPES
    )

    entry.async_on_unload(
        coordinator.async_add_listener(
            _make_device_registry_updater(hass, entry, coordinator)
        )
    )
    coordinator.sensor_setup_complete = True


def _make_device_registry_updater(
    hass: HomeAssistant, entry: SolarmaxConfigEntry, coordinator: SolarmaxCoordinator
) -> Callable[[], None]:
    """Refresh device metadata when static inverter data becomes available."""

    @callback
    def _update_device_registry() -> None:
        model = coordinator.device_model
        if model is None:
            return
        device_registry = dr.async_get(hass)
        identifier = (DOMAIN, entry.entry_id)
        get_by_identifier = getattr(
            device_registry, "async_get_device_by_identifier", None
        )
        if get_by_identifier is not None:
            device = get_by_identifier(identifier, entry.entry_id)
        else:
            device = device_registry.async_get_device(identifiers={identifier})
        if device is None:
            return
        metadata: dict[str, Any] = {"model": model}
        if coordinator.sw_version is not None:
            metadata["sw_version"] = coordinator.sw_version
        if coordinator.serial_number is not None:
            metadata["serial_number"] = coordinator.serial_number
        device_registry.async_update_device(device.id, **metadata)

    return _update_device_registry


class SolarmaxSensor(CoordinatorEntity[SolarmaxCoordinator], SensorEntity):
    """Representation of a Solarmax sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarmaxCoordinator,
        entry: SolarmaxConfigEntry,
        description: SensorEntityDescription,
        device_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self.sensor_key = description.key

        # Snapshot the option: an options-flow change reloads the entry, so a
        # value read at construction time can never go stale.
        self._night_keep_values: bool = entry_option(
            entry, CONF_NIGHT_KEEP_VALUES, DEFAULT_NIGHT_KEEP_VALUES
        )

        # No hardware identifier is available, so the unique_id falls back to the
        # config entry id per HA guidance: {entry_id}-{key}.
        sensor_type = description.key.lower()
        self._attr_unique_id = f"{entry.entry_id}-{sensor_type}"

        # Force a stable, readable entity_id derived from the device name.
        device_name_normalized = device_name.lower().replace(" ", "_").replace("-", "_")
        self.entity_id = generate_entity_id(
            "sensor.{}",
            f"{device_name_normalized}_{sensor_type}",
            hass=coordinator.hass,
        )

        # Model/firmware/serial are resolved by the coordinator's first refresh,
        # which runs before the sensor platform is set up.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer="Solarmax",
            model=coordinator.device_model or "Inverter",
            sw_version=coordinator.sw_version,
            serial_number=coordinator.serial_number,
        )

    @staticmethod
    def _decode_sal_alarms(value: int) -> list[str]:
        """Return the active alarm option keys encoded in a SAL bitmask."""
        return [
            SAL_ALARM_MAP[bit]
            for bit in sorted(SAL_ALARM_MAP)
            if bit > 0 and value & bit
        ]

    @property
    def _night_policy(self) -> NightPolicy | None:
        """Return the active night policy, if this sensor has one."""
        if not self._night_keep_values:
            return None
        snapshot = self.coordinator.data
        if snapshot is None or snapshot.state is not EngineState.OFFLINE_EXPECTED:
            return None
        policy = NIGHT_POLICY.get(self.sensor_key, NightPolicy.UNAVAILABLE)
        return None if policy is NightPolicy.UNAVAILABLE else policy

    def _night_value_source(self, policy: NightPolicy) -> str:
        """Resolve a policy in force to the value it produces right now."""
        if policy is NightPolicy.ZERO:
            if self._sensor_data() is None:
                return "unavailable"
            # A shutdown inferred while the sun is up is not an honest zero.
            if self._anomalous_expected():
                return "unavailable"
            return "zero"
        if policy is NightPolicy.HOLD_UNTIL_MIDNIGHT and self._is_new_day():
            return "zero" if self._sensor_data() is not None else "unavailable"
        return "hold"

    def _anomalous_expected(self) -> bool:
        """True when the current OFFLINE_EXPECTED was armed outside twilight."""
        snapshot = self.coordinator.data
        return snapshot is not None and snapshot.expected_outside_twilight

    def _is_new_day(self) -> bool:
        """True when the last successful poll fell on an earlier local day.

        Stateless and restart-safe: derived from the timestamp rather than a
        latch, so it cannot drift out of sync with the wall clock.
        """
        last = self.coordinator.last_successful_update
        if last is None:
            return False
        return last.date() != dt_util.now().date()

    @property
    def _is_status(self) -> bool:
        return self.sensor_key == SENSOR_TYPE_STATUS

    def _sensor_data(self) -> dict[str, float | int] | None:
        """Return the cached reading for this entity."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return None
        return snapshot.values.get(self.sensor_key)

    def _sensor_value(self) -> float | int | None:
        sensor_data = self._sensor_data()
        return None if sensor_data is None else sensor_data.get("value")

    def _resolved_night_source(self) -> str | None:
        policy = self._night_policy
        return None if policy is None else self._night_value_source(policy)

    def _offline_status_value(self) -> str | None:
        """Return the connection state exposed by the status sensor."""
        if not self._is_status:
            return None
        snapshot = self.coordinator.data
        state = snapshot.state if snapshot is not None else EngineState.UNKNOWN
        if state is EngineState.ONLINE:
            return None
        return _SYS_OFFLINE_STATE_MAP.get(state, SYS_STATE_UNKNOWN)

    def _decoded_value(self, value: float | int | None) -> str | int | float | None:
        """Translate status and alarm registers into entity option keys."""
        if self._is_status and isinstance(value, int):
            return SYS_STATUS_MAP.get(value, SYS_STATE_UNKNOWN)
        if self.sensor_key == SENSOR_TYPE_ALARM and isinstance(value, int):
            return self._alarm_value(value)
        return value

    def _alarm_value(self, value: int) -> str:
        if value in SAL_ALARM_MAP:
            return SAL_ALARM_MAP[value]
        if self._decode_sal_alarms(value):
            return SAL_STATE_MULTIPLE
        return SAL_STATE_UNKNOWN

    @property
    def native_value(self) -> str | int | float | None:
        """Return the state of the sensor."""
        if offline_value := self._offline_status_value():
            return offline_value
        night_source = self._resolved_night_source()
        if night_source == "zero":
            return 0
        if night_source == "unavailable":
            return None
        return self._decoded_value(self._sensor_value())

    def _offline_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes shown on the status sensor while offline."""
        attributes: dict[str, Any] = {
            "raw_value": "offline",
            "code": "offline",
        }
        snapshot = self.coordinator.data
        if snapshot is not None:
            attributes.update(self._snapshot_diagnostic_attributes())
        last_update = self.coordinator.last_successful_update
        if last_update:
            attributes["last_successful_update"] = last_update.isoformat()
        return attributes

    def _snapshot_diagnostic_attributes(self) -> dict[str, Any]:
        """Return diagnostic flags from the current snapshot."""
        snapshot = self.coordinator.data
        if snapshot is None:
            return {}
        attributes: dict[str, Any] = {}
        if snapshot.reconnecting:
            attributes["reconnecting"] = True
        if snapshot.expected_outside_twilight:
            attributes["expected_outside_twilight"] = True
        if snapshot.fault_since is not None:
            attributes["fault_since"] = snapshot.fault_since.isoformat()
        return attributes

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        if self._offline_status_value() is not None:
            return self._offline_attributes()
        night_source = self._resolved_night_source()
        if synthetic := self._synthetic_night_attributes(night_source):
            return synthetic
        sensor_data = self._sensor_data()
        return (
            None
            if sensor_data is None
            else self._reading_attributes(sensor_data, night_source)
        )

    @staticmethod
    def _synthetic_night_attributes(night_source: str | None) -> dict[str, Any] | None:
        """Build attributes for night values that do not come from a poll."""
        if night_source == "zero":
            return {"raw_value": 0, "night_value_source": "zero"}
        if night_source == "unavailable":
            return {"night_value_source": "unavailable"}
        return None

    def _reading_attributes(
        self, sensor_data: dict[str, float | int], night_source: str | None
    ) -> dict[str, Any]:
        """Build attributes for a cached inverter reading."""
        attributes: dict[str, Any] = {}
        if "raw_value" in sensor_data:
            attributes["raw_value"] = sensor_data["raw_value"]
        value = sensor_data.get("value")
        attributes.update(self._register_attributes(value))
        last_update = self.coordinator.last_successful_update
        if self._is_status and last_update:
            attributes["last_successful_update"] = last_update.isoformat()
        if night_source is not None:
            attributes["night_value_source"] = night_source
        return attributes

    def _register_attributes(self, value: Any) -> dict[str, Any]:
        """Return raw-code details for status and alarm registers."""
        if not isinstance(value, int):
            return {}
        attributes: dict[str, Any] = {}
        if self.sensor_key in (SENSOR_TYPE_STATUS, SENSOR_TYPE_ALARM):
            attributes["code"] = value
        if self.sensor_key == SENSOR_TYPE_ALARM and value:
            if active_alarms := self._decode_sal_alarms(value):
                attributes["active_alarms"] = active_alarms
        return attributes

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        snapshot = self.coordinator.data
        if snapshot is not None and snapshot.state is EngineState.ONLINE:
            return True
        if self._is_status:
            return True
        return self._night_source_available(self._resolved_night_source())

    def _night_source_available(self, night_source: str | None) -> bool:
        if night_source == "zero":
            return True
        if night_source != "hold":
            return False
        return self._sensor_value() is not None
