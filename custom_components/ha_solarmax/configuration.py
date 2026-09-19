"""Config entry storage helpers for SolarMax."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Self
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from .connection import LinkClosed, LinkTimeout, SolarmaxLink
from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_HOST,
    CONF_IS_GROUP,
    CONF_NIGHT_KEEP_VALUES,
    CONF_PORT,
    CONF_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_VERIFY_CHECKSUM,
    DEFAULT_NIGHT_KEEP_VALUES,
    DEFAULT_PORT,
    DEFAULT_TWILIGHT_ELEVATION_THRESHOLD,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VERIFY_CHECKSUM,
    DOMAIN,
)
from .protocol import ProtocolError, build_request, parse_response
_CONFIGURATION_LOCK = "configuration_mutation_lock"
_ENDPOINT_LINKS = "endpoint_links"
_LOGGER = logging.getLogger(__name__)
TCP_PORT_SCHEMA = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))
CONNECTION_KEYS = (CONF_HOST, CONF_PORT, CONF_ADDRESS, CONF_DEVICE_NAME)
OPTION_KEYS = (
    CONF_UPDATE_INTERVAL,
    CONF_VERIFY_CHECKSUM,
    CONF_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_NIGHT_KEEP_VALUES,
)
OPTION_DEFAULTS = {
    CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
    CONF_VERIFY_CHECKSUM: DEFAULT_VERIFY_CHECKSUM,
    CONF_TWILIGHT_ELEVATION_THRESHOLD: DEFAULT_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_NIGHT_KEEP_VALUES: DEFAULT_NIGHT_KEEP_VALUES,
}

class CannotConnect(HomeAssistantError):
    """The selected endpoint did not return a valid PAC response."""


class EntryReloadError(HomeAssistantError):
    """The new entry could not load and rollback was required."""


@dataclass
class _SharedLink:
    """A SolarmaxLink plus how many callers currently hold it."""

    link: SolarmaxLink
    refcount: int = 0


@dataclass(frozen=True)
class EntrySnapshot:
    """Entry values needed to undo a configuration change."""

    data: dict[str, Any]
    options: dict[str, Any]
    title: str
    unique_id: str | None
    @classmethod
    def capture(cls, entry: ConfigEntry) -> Self:
        """Copy persisted values before mutation."""
        return cls(dict(entry.data), dict(entry.options), entry.title, entry.unique_id)


@asynccontextmanager
async def validation_handoff(entry: ConfigEntry) -> AsyncIterator[None]:
    """Wait for setup, then release the runtime connection if one exists."""
    async with entry.setup_lock:
        engine = getattr(getattr(entry, "runtime_data", None), "engine", None)
        if engine is None:
            yield
            return
        async with engine.validation_handoff():
            yield


@callback
def update_device_name(hass: HomeAssistant, entry_id: str, device_name: str) -> None:
    """Update the device display name without changing entity identity."""
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, entry_id)})
    if device is not None:
        registry.async_update_device(device.id, name=device_name)


async def _reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        return await hass.config_entries.async_reload(entry.entry_id)
    except Exception:
        _LOGGER.exception("Failed to reload SolarMax entry %s", entry.entry_id)
        return False


async def _apply_reload_or_rollback(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    title: str,
    unique_id: str | None,
) -> None:
    previous = EntrySnapshot.capture(entry)
    previous_runtime = getattr(entry, "runtime_data", None)
    hass.config_entries.async_update_entry(
        entry, data=data, options=options, title=title, unique_id=unique_id
    )
    if entry.disabled_by is not None:
        return
    if await _reload_entry(hass, entry):
        return
    hass.config_entries.async_update_entry(
        entry,
        data=previous.data,
        options=previous.options,
        title=previous.title,
        unique_id=previous.unique_id,
    )
    old_runtime_survived = (
        previous_runtime is not None
        and getattr(entry, "runtime_data", None) is previous_runtime
    )
    if not old_runtime_survived and not await _reload_entry(hass, entry):
        raise EntryReloadError("new and restored entry setup failed")
    raise EntryReloadError("entry reload failed")


async def _await_atomic(task: asyncio.Task[None]) -> None:
    """Defer repeated caller cancellation until the child reaches stability."""
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.wait((task,))
        except asyncio.CancelledError as err:
            cancellation = cancellation or err
    try:
        task.result()
    except Exception:
        if cancellation is not None:
            raise cancellation from None
        raise
    if cancellation is not None:
        raise cancellation


async def async_apply_and_reload(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    data: Mapping[str, Any],
    options: Mapping[str, Any],
    title: str,
    unique_id: str | None,
) -> None:
    """Apply or restore entry values; caller holds configuration_mutation_lock."""
    transaction = hass.async_create_task(
        _apply_reload_or_rollback(
            hass, entry, data=data, options=options, title=title, unique_id=unique_id
        ),
        f"update SolarMax entry {entry.entry_id}",
    )
    await _await_atomic(transaction)


def configuration_mutation_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the domain-scoped lock for configuration mutations."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    lock: asyncio.Lock = domain_data.setdefault(_CONFIGURATION_LOCK, asyncio.Lock())
    return lock


def acquire_endpoint_link(hass: HomeAssistant, host: str, port: int) -> SolarmaxLink:
    """Return the persistent link for one host:port, opening it on first use.

    A SolarMax gateway serves a single TCP client. When several inverters
    sit behind the same gateway (same host:port, different address), every
    config entry must reuse this one connection instead of opening its own,
    or the gateway would see more than one concurrent client -- which is
    exactly the failure this integration must not cause, independent of
    whether requests on it are also serialized. Keyed by (host, port) only:
    entries never share an address too, so this is exactly the set that
    shares physical wiring.

    Callers must release with `release_endpoint_link()` exactly once per
    acquire, once they no longer need the connection.
    """
    registry: dict[tuple[str, int], _SharedLink] = hass.data.setdefault(
        DOMAIN, {}
    ).setdefault(_ENDPOINT_LINKS, {})
    key = (host, port)
    shared = registry.get(key)
    if shared is None:
        shared = _SharedLink(link=SolarmaxLink(host, port))
        registry[key] = shared
    shared.refcount += 1
    return shared.link


async def release_endpoint_link(hass: HomeAssistant, host: str, port: int) -> None:
    """Undo one `acquire_endpoint_link()` call, closing the link when unused."""
    registry: dict[tuple[str, int], _SharedLink] = hass.data.get(DOMAIN, {}).get(
        _ENDPOINT_LINKS, {}
    )
    key = (host, port)
    shared = registry.get(key)
    if shared is None:
        return
    shared.refcount -= 1
    if shared.refcount <= 0:
        del registry[key]
        await shared.link.close()


def find_endpoint_conflict(
    hass: HomeAssistant,
    host: str,
    port: int,
    address: int,
    *,
    exclude_entry_id: str | None = None,
) -> ConfigEntry | None:
    """Return an entry that already owns the host, port and inverter address."""
    return next(
        (
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.entry_id != exclude_entry_id
            and entry.data.get(CONF_HOST) == host
            and entry.data.get(CONF_PORT, DEFAULT_PORT) == port
            and entry.data.get(CONF_ADDRESS, 1) == address
        ),
        None,
    )


def inverter_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return every configured inverter entry (any state), excluding the group."""
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if not entry.data.get(CONF_IS_GROUP, False)
    ]


async def validate_connection(
    hass: HomeAssistant, *, host: str, port: int, address: int, verify_checksum: bool
) -> None:
    """Validate an endpoint with a short PAC request.

    Uses `acquire_endpoint_link()`, so this probe reuses the same connection
    as any coordinator already on this host:port instead of opening a second
    one, and is naturally serialized against their polls by that link's own
    request lock.
    """
    link = acquire_endpoint_link(hass, host, port)
    try:
        raw = await link.request(build_request(address, ["PAC"]))
        parse_response(raw, verify_checksum)
    except (LinkTimeout, LinkClosed, ProtocolError, OSError, UnicodeError) as err:
        raise CannotConnect from err
    finally:
        await release_endpoint_link(hass, host, port)


def split_entry_input(
    values: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split config entry input into connection data and preference options."""
    return (
        {key: values[key] for key in CONNECTION_KEYS},
        {key: values[key] for key in OPTION_KEYS},
    )


def entry_option(entry: ConfigEntry, key: str, default: Any) -> Any:
    """Return an option, falling back to legacy config entry data."""
    return entry.options.get(key, entry.data.get(key, default))


def endpoint_unique_id(host: str, port: int, address: int) -> str:
    """Return the stable unique ID for an inverter endpoint."""
    return f"{host}:{port}:{address}"
