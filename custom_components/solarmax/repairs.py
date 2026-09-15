"""Verified connection repair for Solarmax integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import data_entry_flow
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .configuration import (
    TCP_PORT_SCHEMA,
    CannotConnect,
    EntryReloadError,
    async_apply_and_reload,
    configuration_mutation_lock,
    endpoint_unique_id,
    entry_option,
    find_endpoint_conflict,
    validate_connection,
    validation_handoff,
)
from .const import (
    CONF_ADDRESS,
    CONF_HOST,
    CONF_PORT,
    CONF_VERIFY_CHECKSUM,
    DEFAULT_VERIFY_CHECKSUM,
    DOMAIN,
    REPAIR_PENDING,
    REPAIR_PENDING_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)


class SolarmaxConnectionRepairFlow(RepairsFlow):
    """Edit and probe an endpoint, leaving full verification to the coordinator."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__()
        self.data = data

    def _placeholders(self) -> dict[str, str]:
        data = self.data or {}
        return {
            "host": str(data.get("host", "unknown")),
            "port": str(data.get("port", "unknown")),
            "minutes": str(data.get("minutes", "?")),
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Offer host and port and validate submitted changes."""
        entry_id = self.issue_id.removeprefix("connection_issues_")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return self.async_abort(reason="entry_missing")
        errors: dict[str, str] = {}
        if user_input is not None:
            async with configuration_mutation_lock(self.hass):
                entry = self.hass.config_entries.async_get_entry(entry_id)
                if entry is None:
                    return self.async_abort(reason="entry_missing")
                try:
                    return await self._async_repair(entry, user_input)
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except EntryReloadError:
                    errors["base"] = "reload_failed"
                except Exception:
                    _LOGGER.exception("Unexpected exception repairing connection")
                    errors["base"] = "unknown"

        values = entry.data if user_input is None else user_input
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=values[CONF_HOST]): str,
                    vol.Required(CONF_PORT, default=values[CONF_PORT]): TCP_PORT_SCHEMA,
                }
            ),
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    def _set_pending(
        self, issue: ir.IssueEntry, pending: bool, endpoint: str | None = None
    ) -> None:
        """Update the same issue record, preserving native Ignore and metadata."""
        data = dict(issue.data or {})
        if pending:
            data[REPAIR_PENDING] = 1
            if endpoint is not None:
                data[REPAIR_PENDING_ENDPOINT] = endpoint
        else:
            data.pop(REPAIR_PENDING, None)
            data.pop(REPAIR_PENDING_ENDPOINT, None)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.issue_id,
            breaks_in_ha_version=issue.breaks_in_ha_version,
            data=data,
            is_fixable=True,
            is_persistent=issue.is_persistent,
            issue_domain=issue.issue_domain,
            learn_more_url=issue.learn_more_url,
            severity=issue.severity or ir.IssueSeverity.ERROR,
            translation_key=issue.translation_key or "connection_issues",
            translation_placeholders=issue.translation_placeholders,
        )

    async def _async_repair(
        self, entry: ConfigEntry, values: dict[str, Any]
    ) -> data_entry_flow.FlowResult:
        """Serialize probing and activation with all other configuration flows."""
        host, port = values[CONF_HOST], values[CONF_PORT]
        target_endpoint = endpoint_unique_id(host, port, entry.data[CONF_ADDRESS])
        if find_endpoint_conflict(
            self.hass, host, port, exclude_entry_id=entry.entry_id
        ):
            return self.async_abort(reason="already_configured")
        registry = ir.async_get(self.hass)
        issue = registry.async_get_issue(DOMAIN, self.issue_id)
        if issue is None:
            return self.async_abort(reason="issue_missing")
        accepted = False
        marked_pending = False
        try:
            async with validation_handoff(entry):
                if registry.async_get_issue(DOMAIN, self.issue_id) is None:
                    return self.async_abort(reason="issue_missing")
                await validate_connection(
                    host=host,
                    port=port,
                    address=entry.data[CONF_ADDRESS],
                    verify_checksum=entry_option(
                        entry, CONF_VERIFY_CHECKSUM, DEFAULT_VERIFY_CHECKSUM
                    ),
                )
                current_issue = registry.async_get_issue(DOMAIN, self.issue_id)
                if current_issue is None:
                    return self.async_abort(reason="issue_missing")
                issue = current_issue
                if find_endpoint_conflict(
                    self.hass, host, port, exclude_entry_id=entry.entry_id
                ):
                    return self.async_abort(reason="already_configured")
                self._set_pending(issue, True, target_endpoint)
                marked_pending = True
            # Unload closes the engine, so release its poll lock first.
            unchanged = (host, port) == (entry.data[CONF_HOST], entry.data[CONF_PORT])
            runtime = getattr(entry, "runtime_data", None)
            accepted = True
            if unchanged and runtime is not None:
                self.hass.async_create_task(
                    runtime.async_request_refresh(),
                    f"verify Solarmax repair {entry.entry_id}",
                )
            elif not unchanged or entry.disabled_by is None:
                await async_apply_and_reload(
                    self.hass,
                    entry,
                    data=dict(entry.data) | {CONF_HOST: host, CONF_PORT: port},
                    options=entry.options,
                    title=entry.title,
                    unique_id=target_endpoint,
                )
        except asyncio.CancelledError:
            if marked_pending and not accepted:
                self._set_pending(issue, False)
            raise
        return self.async_abort(reason="repair_pending_verification")


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Create the endpoint repair or Home Assistant's generic fallback."""
    if issue_id.startswith("connection_issues_"):
        return SolarmaxConnectionRepairFlow(data or {})
    return ConfirmRepairFlow()
