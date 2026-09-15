"""Diagnostics support for Solarmax integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CONF_HOST, DEVICE_KEY_SERIAL
from .coordinator import SolarmaxConfigEntry, SolarmaxCoordinator

# Redact the host and both names used for inverter serial data.
REDACT_KEYS = {CONF_HOST, DEVICE_KEY_SERIAL, "serial_number"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolarmaxConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: SolarmaxCoordinator = entry.runtime_data
    snapshot = coordinator.data

    integration = await async_get_integration(hass, entry.domain)

    diagnostics_data: dict[str, Any] = {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "minor_version": entry.minor_version,
            "domain": entry.domain,
            "title": entry.title,
            "data": async_redact_data(entry.data, REDACT_KEYS),
            "options": entry.options,
            "source": entry.source,
            "state": entry.state.value if entry.state else None,
        },
        "coordinator": {
            "update_interval": str(coordinator.update_interval),
            "state": snapshot.state if snapshot else None,
            "reconnecting": snapshot.reconnecting if snapshot else None,
            "sun_source": coordinator.sun_source,
            "fault_since": (
                snapshot.fault_since.isoformat()
                if snapshot and snapshot.fault_since
                else None
            ),
            "last_successful_update": (
                coordinator.last_successful_update.isoformat()
                if coordinator.last_successful_update
                else None
            ),
        },
        "connection": dict(snapshot.diagnostics) if snapshot else {},
        "sensor_data": {},
        "system_info": {
            "ha_version": hass.config.as_dict().get("version"),
            "integration_version": str(integration.version),
        },
    }

    if snapshot:
        diagnostics_data["sensor_data"] = async_redact_data(
            {
                sensor_key: {
                    "value": sensor_value.get("value"),
                    "raw_value": sensor_value.get("raw_value"),
                }
                for sensor_key, sensor_value in snapshot.values.items()
            },
            REDACT_KEYS,
        )

    diagnostics_data["device_info"] = async_redact_data(
        {
            "identifiers": [(entry.domain, entry.entry_id)],
            "name": entry.data.get("device_name", "Solarmax Inverter"),
            "manufacturer": "Solarmax",
            "model": coordinator.device_model or "Inverter",
        },
        REDACT_KEYS,
    )

    return diagnostics_data
