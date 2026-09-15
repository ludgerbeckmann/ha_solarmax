"""The Solarmax Inverter integration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change

from .configuration import OPTION_DEFAULTS, endpoint_unique_id, entry_option
from .const import (
    CONF_ADDRESS,
    CONF_HOST,
    CONF_NIGHT_KEEP_VALUES,
    CONF_PORT,
    DEFAULT_ADDRESS,
    DEFAULT_NIGHT_KEEP_VALUES,
    DOMAIN,
)
from .coordinator import SolarmaxConfigEntry, SolarmaxCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# Mapping of old unique_id suffixes to new ones for entity migration
_UNIQUE_ID_MIGRATIONS = {
    "kdl": "kld",  # v1.2.1: Energy Yesterday key fix (KDL → KLD)
}


async def async_migrate_entry(hass: HomeAssistant, entry: SolarmaxConfigEntry) -> bool:
    """Migrate a legacy config entry to split data and options storage."""
    if entry.version > 2 or (entry.version == 2 and entry.minor_version > 1):
        return False
    if entry.version == 2:
        return True

    data = dict(entry.data)
    data.setdefault(CONF_ADDRESS, DEFAULT_ADDRESS)
    options = dict(entry.options)
    for key, default in OPTION_DEFAULTS.items():
        options.setdefault(key, data.get(key, default))
        data.pop(key, None)

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        unique_id=endpoint_unique_id(
            data[CONF_HOST], data[CONF_PORT], data[CONF_ADDRESS]
        ),
        version=2,
        minor_version=1,
    )
    return True


def _migrate_unique_ids(hass: HomeAssistant, entry: SolarmaxConfigEntry) -> None:
    """Migrate renamed sensor unique IDs to prevent orphaned entities."""
    registry = er.async_get(hass)

    for old_suffix, new_suffix in _UNIQUE_ID_MIGRATIONS.items():
        old_unique_id = f"{entry.entry_id}-{old_suffix}"
        new_unique_id = f"{entry.entry_id}-{new_suffix}"

        entity_id = registry.async_get_entity_id(Platform.SENSOR, DOMAIN, old_unique_id)
        if entity_id is not None:
            # If the new unique_id already exists, just remove the old entity
            if registry.async_get_entity_id(Platform.SENSOR, DOMAIN, new_unique_id):
                _LOGGER.info(
                    "Removing orphaned entity %s (new entity already exists)",
                    entity_id,
                )
                registry.async_remove(entity_id)
            else:
                _LOGGER.info(
                    "Migrating entity %s unique_id: %s → %s",
                    entity_id,
                    old_unique_id,
                    new_unique_id,
                )
                registry.async_update_entity(entity_id, new_unique_id=new_unique_id)


async def async_setup_entry(hass: HomeAssistant, entry: SolarmaxConfigEntry) -> bool:
    """Set up Solarmax Inverter from a config entry."""
    # Migrate renamed entity unique IDs (v1.2.0 → v1.2.1: KDL → KLD)
    _migrate_unique_ids(hass, entry)

    coordinator = SolarmaxCoordinator(hass, entry)

    try:
        # A dark inverter still produces a snapshot, so entities can be created
        # immediately without ConfigEntryNotReady.
        await coordinator.async_config_entry_first_refresh()

        # Use runtime_data instead of hass.data
        entry.runtime_data = coordinator

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        if not coordinator.sensor_setup_complete:
            raise RuntimeError("Solarmax sensor platform setup failed")

        # Register only after platform setup succeeds so failed setup cannot
        # leave callbacks targeting a closed coordinator.
        if entry_option(entry, CONF_NIGHT_KEEP_VALUES, DEFAULT_NIGHT_KEEP_VALUES):
            entry.async_on_unload(
                async_track_time_change(
                    hass, coordinator.async_handle_midnight, hour=0, minute=0, second=0
                )
            )

        _LOGGER.info(
            "Successfully set up Solarmax inverter at %s:%s",
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
        )
    except BaseException:
        # HA skips integration unload after failed setup. Release this setup's
        # client slot before rollback, including when setup was cancelled.
        try:
            try:
                await coordinator.async_shutdown()
            finally:
                await coordinator.engine.close()
        finally:
            if getattr(entry, "runtime_data", None) is coordinator:
                object.__delattr__(entry, "runtime_data")
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SolarmaxConfigEntry) -> bool:
    """Unload a config entry (runtime_data is cleaned up automatically).

    Keep the engine usable if platform teardown fails and Home Assistant
    leaves the config entry loaded. A successful teardown is followed by
    terminal engine close, which drains any poll still in flight.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.engine.close()
    return unload_ok
