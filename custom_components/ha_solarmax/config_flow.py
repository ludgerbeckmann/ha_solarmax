"""Config flow for SolarMax integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from .configuration import (
    OPTION_DEFAULTS,
    TCP_PORT_SCHEMA,
    CannotConnect,
    EntryReloadError,
    async_apply_and_reload,
    configuration_mutation_lock,
    endpoint_unique_id,
    entry_option,
    find_endpoint_conflict,
    inverter_entries,
    split_entry_input,
    update_device_name,
    validate_connection,
    validation_handoff,
)
from .const import (
    CONF_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_GROUP_MEMBERS,
    CONF_HOST,
    CONF_IS_GROUP,
    CONF_NIGHT_KEEP_VALUES,
    CONF_NOTIFY_MODE,
    CONF_NOTIFY_RECOVERY,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_TIMING,
    CONF_PORT,
    CONF_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_VERIFY_CHECKSUM,
    DEFAULT_ADDRESS,
    DEFAULT_DEVICE_NAME,
    DEFAULT_GROUP_DEVICE_NAME,
    DEFAULT_NIGHT_KEEP_VALUES,
    DEFAULT_NOTIFY_MODE_GROUP,
    DEFAULT_NOTIFY_MODE_INVERTER,
    DEFAULT_NOTIFY_RECOVERY,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_NOTIFY_TIMING,
    DEFAULT_PORT,
    DEFAULT_TWILIGHT_ELEVATION_THRESHOLD,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VERIFY_CHECKSUM,
    DOMAIN,
    GROUP_UNIQUE_ID,
    NOTIFY_MODE_INHERIT,
    NOTIFY_MODE_OFF,
    NOTIFY_MODE_PERSISTENT,
    NOTIFY_MODE_PUSH,
    NOTIFY_TIMING_DELAYED,
    NOTIFY_TIMING_IMMEDIATE,
)
_LOGGER = logging.getLogger(__name__)
# Plain min/max int fields render as a slider in the HA frontend, which is
# fiddly for a 1-249 range where an exact value is needed; a box-mode number
# selector gives a typeable field with +/- steppers instead.
_ADDRESS_SELECTOR = vol.All(
    selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1, max=249, mode=selector.NumberSelectorMode.BOX
        )
    ),
    vol.Coerce(int),
)
# Localized suggested device names, keyed by the first segment of
# hass.config.language (e.g. "de" for "de" or "de-DE"). DEFAULT_DEVICE_NAME /
# DEFAULT_GROUP_DEVICE_NAME (English) is the fallback for any other language.
_LOCALIZED_DEVICE_NAMES: dict[str, str] = {"de": "Wechselrichter", "fr": "Onduleur"}
_LOCALIZED_GROUP_DEVICE_NAMES: dict[str, str] = {
    "de": "Wechselrichtergruppe",
    "fr": "Groupe d'onduleurs",
}
# Selectable notification modes. The group has no INHERIT option -- it is the
# top of the inheritance chain and never has a connection fault of its own.
_NOTIFY_MODES_INVERTER = [
    NOTIFY_MODE_INHERIT,
    NOTIFY_MODE_OFF,
    NOTIFY_MODE_PERSISTENT,
    NOTIFY_MODE_PUSH,
]
_NOTIFY_MODES_GROUP = [NOTIFY_MODE_OFF, NOTIFY_MODE_PERSISTENT, NOTIFY_MODE_PUSH]
_NOTIFY_TIMINGS = [NOTIFY_TIMING_IMMEDIATE, NOTIFY_TIMING_DELAYED]


def _notify_service_selector(hass: HomeAssistant, current: str) -> selector.SelectSelector:
    """Build a dropdown of registered notify.* services, plus the saved value."""
    services = sorted(hass.services.async_services().get("notify", {}))
    if current and current not in services:
        services = [current, *services]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=services,
            custom_value=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _notify_mode_selector(modes: list[str]) -> selector.SelectSelector:
    """Build a translated dropdown for one of the two notification-mode sets."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=modes,
            translation_key="notify_mode",
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


_NOTIFY_TIMING_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=_NOTIFY_TIMINGS,
        translation_key="notify_timing",
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)


def _localized_device_name(hass: HomeAssistant) -> str:
    """Suggest a device name in the user's configured language, if known."""
    language = hass.config.language.split("-")[0].lower()
    return _LOCALIZED_DEVICE_NAMES.get(language, DEFAULT_DEVICE_NAME)


def _localized_group_device_name(hass: HomeAssistant) -> str:
    """Suggest the group's device name in the user's configured language."""
    language = hass.config.language.split("-")[0].lower()
    return _LOCALIZED_GROUP_DEVICE_NAMES.get(language, DEFAULT_GROUP_DEVICE_NAME)


# Default field values for a fresh config entry. The options flow overlays the
# entry's current values on top of these before building its schema.
_DEFAULT_VALUES: dict[str, Any] = {
    CONF_HOST: "",
    CONF_PORT: DEFAULT_PORT,
    CONF_ADDRESS: DEFAULT_ADDRESS,
    CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
    CONF_DEVICE_NAME: DEFAULT_DEVICE_NAME,
    CONF_VERIFY_CHECKSUM: DEFAULT_VERIFY_CHECKSUM,
    CONF_TWILIGHT_ELEVATION_THRESHOLD: DEFAULT_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_NIGHT_KEEP_VALUES: DEFAULT_NIGHT_KEEP_VALUES,
    CONF_NOTIFY_MODE: DEFAULT_NOTIFY_MODE_INVERTER,
    CONF_NOTIFY_SERVICE: DEFAULT_NOTIFY_SERVICE,
    CONF_NOTIFY_TIMING: DEFAULT_NOTIFY_TIMING,
    CONF_NOTIFY_RECOVERY: DEFAULT_NOTIFY_RECOVERY,
}


def _notify_fields(hass: HomeAssistant, values: Mapping[str, Any]) -> dict[Any, Any]:
    """Return the four notification fields shared by the inverter schemas."""
    return {
        vol.Optional(
            CONF_NOTIFY_MODE, default=values[CONF_NOTIFY_MODE]
        ): _notify_mode_selector(_NOTIFY_MODES_INVERTER),
        vol.Optional(
            CONF_NOTIFY_SERVICE, default=values[CONF_NOTIFY_SERVICE]
        ): _notify_service_selector(hass, values[CONF_NOTIFY_SERVICE]),
        vol.Optional(
            CONF_NOTIFY_TIMING, default=values[CONF_NOTIFY_TIMING]
        ): _NOTIFY_TIMING_SELECTOR,
        vol.Optional(
            CONF_NOTIFY_RECOVERY, default=values[CONF_NOTIFY_RECOVERY]
        ): bool,
    }


def _build_schema(hass: HomeAssistant, values: dict[str, Any]) -> vol.Schema:
    """Build the shared config/options schema, pre-filled with the given values."""
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, description={"suggested_value": values[CONF_HOST]}
            ): str,
            vol.Required(CONF_PORT, default=values[CONF_PORT]): TCP_PORT_SCHEMA,
            vol.Optional(
                CONF_ADDRESS, default=values[CONF_ADDRESS]
            ): _ADDRESS_SELECTOR,
            vol.Optional(
                CONF_UPDATE_INTERVAL, default=values[CONF_UPDATE_INTERVAL]
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600)),
            vol.Optional(CONF_DEVICE_NAME, default=values[CONF_DEVICE_NAME]): str,
            vol.Optional(
                CONF_VERIFY_CHECKSUM, default=values[CONF_VERIFY_CHECKSUM]
            ): bool,
            vol.Optional(
                CONF_NIGHT_KEEP_VALUES, default=values[CONF_NIGHT_KEEP_VALUES]
            ): bool,
            vol.Optional(
                CONF_TWILIGHT_ELEVATION_THRESHOLD,
                default=values[CONF_TWILIGHT_ELEVATION_THRESHOLD],
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=90)),
            **_notify_fields(hass, values),
        }
    )


def _build_options_schema(hass: HomeAssistant, values: Mapping[str, Any]) -> vol.Schema:
    """Build the preference-only options schema."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_UPDATE_INTERVAL, default=values[CONF_UPDATE_INTERVAL]
            ): vol.All(vol.Coerce(int), vol.Range(min=5, max=3600)),
            vol.Optional(
                CONF_VERIFY_CHECKSUM, default=values[CONF_VERIFY_CHECKSUM]
            ): bool,
            vol.Optional(
                CONF_TWILIGHT_ELEVATION_THRESHOLD,
                default=values[CONF_TWILIGHT_ELEVATION_THRESHOLD],
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=90)),
            vol.Optional(
                CONF_NIGHT_KEEP_VALUES, default=values[CONF_NIGHT_KEEP_VALUES]
            ): bool,
            **_notify_fields(hass, values),
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SolarMax."""

    VERSION = 2
    MINOR_VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return OptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer a choice between a single inverter and the summary group."""
        return self.async_show_menu(step_id="user", menu_options=["device", "group"])

    async def async_step_group(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the single virtual entry summing every configured inverter."""
        await self.async_set_unique_id(GROUP_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            device_name = user_input[CONF_DEVICE_NAME]
            return self.async_create_entry(
                title=device_name,
                data={CONF_IS_GROUP: True, CONF_DEVICE_NAME: device_name},
                options={},
            )
        return self.async_show_form(
            step_id="group",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICE_NAME,
                        default=_localized_group_device_name(self.hass),
                    ): str,
                }
            ),
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle setting up a single inverter."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data, options = split_entry_input(user_input)
            host = data[CONF_HOST]
            port = data[CONF_PORT]
            address = data[CONF_ADDRESS]
            async with configuration_mutation_lock(self.hass):
                if find_endpoint_conflict(self.hass, host, port, address) is not None:
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(endpoint_unique_id(host, port, address))
                self._abort_if_unique_id_configured()
                try:
                    await validate_connection(
                        self.hass,
                        host=host,
                        port=port,
                        address=address,
                        verify_checksum=options[CONF_VERIFY_CHECKSUM],
                    )
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected exception")
                    errors["base"] = "unknown"
                else:
                    if find_endpoint_conflict(self.hass, host, port, address) is not None:
                        return self.async_abort(reason="already_configured")
                    return self.async_create_entry(
                        title=data[CONF_DEVICE_NAME], data=data, options=options
                    )
        return self.async_show_form(
            step_id="device",
            data_schema=_build_schema(
                self.hass,
                _DEFAULT_VALUES
                | {CONF_DEVICE_NAME: _localized_device_name(self.hass)},
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and atomically replace connection settings."""
        entry = self._get_reconfigure_entry()
        if entry.data.get(CONF_IS_GROUP, False):
            return self.async_abort(reason="group_not_reconfigurable")
        errors: dict[str, str] = {}
        if user_input is not None:
            async with configuration_mutation_lock(self.hass):
                entry = self._get_reconfigure_entry()
                try:
                    return await self._async_reconfigure(entry, user_input)
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except EntryReloadError:
                    errors["base"] = "reload_failed"
        values = (
            _DEFAULT_VALUES | dict(entry.data) if user_input is None else user_input
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=values[CONF_HOST]): str,
                    vol.Required(CONF_PORT, default=values[CONF_PORT]): TCP_PORT_SCHEMA,
                    vol.Required(
                        CONF_ADDRESS, default=values[CONF_ADDRESS]
                    ): _ADDRESS_SELECTOR,
                    vol.Required(
                        CONF_DEVICE_NAME, default=values[CONF_DEVICE_NAME]
                    ): str,
                }
            ),
            errors=errors,
        )

    async def _async_reconfigure(
        self, entry: config_entries.ConfigEntry, values: dict[str, Any]
    ) -> ConfigFlowResult:
        """Apply a submitted reconfiguration while the mutation lock is held."""
        host, port, address = (
            values[key] for key in (CONF_HOST, CONF_PORT, CONF_ADDRESS)
        )
        name = values[CONF_DEVICE_NAME]
        name_changed = name != entry.data[CONF_DEVICE_NAME]
        data = dict(entry.data) | values
        if (host, port, address) == tuple(
            entry.data.get(key, _DEFAULT_VALUES[key])
            for key in (CONF_HOST, CONF_PORT, CONF_ADDRESS)
        ):
            if name_changed:
                self.hass.config_entries.async_update_entry(
                    entry, data=data, title=name
                )
                update_device_name(self.hass, entry.entry_id, name)
            return self.async_abort(reason="reconfigure_successful")
        if find_endpoint_conflict(
            self.hass, host, port, address, exclude_entry_id=entry.entry_id
        ):
            return self.async_abort(reason="already_configured")
        async with validation_handoff(entry):
            await validate_connection(
                self.hass,
                host=host,
                port=port,
                address=address,
                verify_checksum=entry_option(
                    entry, CONF_VERIFY_CHECKSUM, DEFAULT_VERIFY_CHECKSUM
                ),
            )
        # Release the engine poll lock before unload closes that engine.
        if find_endpoint_conflict(
            self.hass, host, port, address, exclude_entry_id=entry.entry_id
        ):
            return self.async_abort(reason="already_configured")
        await async_apply_and_reload(
            self.hass,
            entry,
            data=data,
            options=entry.options,
            title=name,
            unique_id=endpoint_unique_id(host, port, address),
        )
        if name_changed:
            update_device_name(self.hass, entry.entry_id, name)
        return self.async_abort(reason="reconfigure_successful")


class OptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for SolarMax."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update settings without opening a second inverter connection."""
        if self.config_entry.data.get(CONF_IS_GROUP, False):
            return await self.async_step_group_members(user_input)
        errors: dict[str, str] = {}
        if user_input is not None:
            async with configuration_mutation_lock(self.hass):
                entry = self.config_entry
                data = dict(entry.data)
                options = dict(entry.options)
                if user_input == options:
                    return self.async_create_entry(title="", data=None)  # type: ignore[arg-type]
                try:
                    await async_apply_and_reload(
                        self.hass,
                        entry,
                        data=data,
                        options=user_input,
                        title=entry.title,
                        unique_id=entry.unique_id,
                    )
                except EntryReloadError:
                    errors["base"] = "reload_failed"
                else:
                    return self.async_create_entry(title="", data=user_input)
        values = {
            key: entry_option(self.config_entry, key, default)
            for key, default in OPTION_DEFAULTS.items()
        }
        if user_input is not None:
            values = user_input

        return self.async_show_form(
            step_id="init",
            data_schema=_build_options_schema(self.hass, values),
            errors=errors,
        )

    async def async_step_group_members(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which configured inverters this group sums together."""
        entry = self.config_entry
        all_ids = [member.entry_id for member in inverter_entries(self.hass)]
        errors: dict[str, str] = {}
        if user_input is not None:
            options = dict(entry.options)
            if user_input == options:
                return self.async_create_entry(title="", data=None)  # type: ignore[arg-type]
            async with configuration_mutation_lock(self.hass):
                try:
                    await async_apply_and_reload(
                        self.hass,
                        entry,
                        data=dict(entry.data),
                        options=user_input,
                        title=entry.title,
                        unique_id=entry.unique_id,
                    )
                except EntryReloadError:
                    errors["base"] = "reload_failed"
                else:
                    return self.async_create_entry(title="", data=user_input)
        selected = entry_option(entry, CONF_GROUP_MEMBERS, all_ids)
        selected = [entry_id for entry_id in selected if entry_id in all_ids]
        notify_values = {
            CONF_NOTIFY_MODE: entry_option(
                entry, CONF_NOTIFY_MODE, DEFAULT_NOTIFY_MODE_GROUP
            ),
            CONF_NOTIFY_SERVICE: entry_option(
                entry, CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE
            ),
            CONF_NOTIFY_TIMING: entry_option(
                entry, CONF_NOTIFY_TIMING, DEFAULT_NOTIFY_TIMING
            ),
            CONF_NOTIFY_RECOVERY: entry_option(
                entry, CONF_NOTIFY_RECOVERY, DEFAULT_NOTIFY_RECOVERY
            ),
        }
        return self.async_show_form(
            step_id="group_members",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_GROUP_MEMBERS, default=selected
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=member.entry_id,
                                    label=member.data.get(
                                        CONF_DEVICE_NAME, member.title
                                    ),
                                )
                                for member in inverter_entries(self.hass)
                            ],
                            multiple=True,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(
                        CONF_NOTIFY_MODE,
                        default=notify_values[CONF_NOTIFY_MODE],
                    ): _notify_mode_selector(_NOTIFY_MODES_GROUP),
                    vol.Optional(
                        CONF_NOTIFY_SERVICE,
                        default=notify_values[CONF_NOTIFY_SERVICE],
                    ): _notify_service_selector(
                        self.hass, notify_values[CONF_NOTIFY_SERVICE]
                    ),
                    vol.Optional(
                        CONF_NOTIFY_TIMING,
                        default=notify_values[CONF_NOTIFY_TIMING],
                    ): _NOTIFY_TIMING_SELECTOR,
                    vol.Optional(
                        CONF_NOTIFY_RECOVERY,
                        default=notify_values[CONF_NOTIFY_RECOVERY],
                    ): bool,
                }
            ),
            errors=errors,
        )
