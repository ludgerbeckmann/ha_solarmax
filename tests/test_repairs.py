"""Test repairs for Solarmax integration."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir

from custom_components.solarmax.configuration import CannotConnect
from custom_components.solarmax.connection import EngineState
from custom_components.solarmax.const import DOMAIN, SAL_OPTIONS, SYS_OPTIONS
from custom_components.solarmax.coordinator import SolarmaxCoordinator
from custom_components.solarmax.repairs import (
    SolarmaxConnectionRepairFlow,
    async_create_fix_flow,
)
from tests.test_config_flow import _configured_endpoint_entry
from tests.test_coordinator import _snap

_INTEGRATION_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "solarmax"
)
_TRANSLATION_PATHS = [
    _INTEGRATION_DIR / "strings.json",
    _INTEGRATION_DIR / "translations" / "en.json",
    _INTEGRATION_DIR / "translations" / "de.json",
    _INTEGRATION_DIR / "translations" / "fr.json",
]


def _load_translation(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _leaf_key_paths(node: object, prefix: str = "") -> set[str]:
    """Return dotted paths to every leaf value in a nested dict.

    Comparing leaf paths (not just top-level keys) catches a missing
    `fix_flow` block, not just a missing `issues.<id>` entry.
    """
    if not isinstance(node, dict):
        return {prefix}
    paths: set[str] = set()
    for key, value in node.items():
        full = f"{prefix}.{key}" if prefix else key
        paths |= _leaf_key_paths(value, full)
    return paths


@pytest.mark.asyncio
async def test_connection_repair_flow(hass, configured_entry):
    """Repair offers only the current endpoint fields."""
    flow = await _repair_flow(hass, configured_entry)
    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert {str(key): key.default() for key in result["data_schema"].schema} == {
        "host": "192.0.2.10",
        "port": 12345,
    }


@pytest.mark.asyncio
async def test_async_create_fix_flow():
    """Test fix flow creation."""
    hass = Mock()

    # Test connection issue flow
    flow = await async_create_fix_flow(hass, "connection_issues_test", {"host": "test"})
    assert isinstance(flow, SolarmaxConnectionRepairFlow)

    # Legacy and unknown issue IDs use Home Assistant's generic fallback.
    flow = await async_create_fix_flow(
        hass, "configuration_issue_test", {"issue": "test"}
    )
    assert isinstance(flow, ConfirmRepairFlow)

    flow = await async_create_fix_flow(hass, "unknown_issue", {})
    assert isinstance(flow, ConfirmRepairFlow)


@pytest.mark.asyncio
async def test_connection_repair_flow_survives_null_data():
    """HA overwrites flow.data from issue.data after construction.

    homeassistant/components/repairs/issue_handler.py assigns `flow.data =
    issue.data` after async_create_fix_flow returns, so the `data or {}` guard
    there does not protect us — a None reaches _placeholders() and used to
    raise AttributeError, surfacing as a 500 when the user opened the repair.
    """
    flow = SolarmaxConnectionRepairFlow({})
    flow.data = None

    placeholders = flow._placeholders()

    assert placeholders["host"] == "unknown"
    assert placeholders["port"] == "unknown"
    assert placeholders["minutes"] == "?"


@pytest.mark.asyncio
async def test_connection_repair_fix_flow_preserves_placeholders(
    hass, configured_entry
):
    """The editable repair retains the issue's description context."""
    flow = await _repair_flow(hass, configured_entry)
    result = await flow.async_step_init()

    assert result["type"] == "form"
    assert result["step_id"] == "init"

    assert result["description_placeholders"] == {
        "host": "192.0.2.10",
        "port": "12345",
        "minutes": "5",
    }


def _connection_issue(hass, entry):
    return ir.async_get(hass).async_get_issue(
        DOMAIN, f"connection_issues_{entry.entry_id}"
    )


def _create_connection_issue(hass, entry):
    context = {
        "host": entry.data["host"],
        "port": str(entry.data["port"]),
        "minutes": "5",
    }
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"connection_issues_{entry.entry_id}",
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="connection_issues",
        translation_placeholders=context,
        data=context,
    )


@pytest.fixture
def configured_entry(hass):
    entry = _configured_endpoint_entry(host="192.0.2.10", port=12345, address=7)
    entry.add_to_hass(hass)
    _create_connection_issue(hass, entry)
    return entry


async def _repair_flow(hass, entry):
    issue_id = f"connection_issues_{entry.entry_id}"
    issue = _connection_issue(hass, entry)
    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass, flow.issue_id, flow.data = hass, issue_id, issue.data
    return flow


async def _submit_repair(hass, entry, *, host, port=None):
    flow = await _repair_flow(hass, entry)
    return await flow.async_step_init(
        {"host": host, "port": port or entry.data["port"]}
    )


@pytest.mark.parametrize(
    "error,key", [(CannotConnect(), "cannot_connect"), (RuntimeError(), "unknown")]
)
async def test_failed_repair_probe_leaves_entry_and_issue_unchanged(
    hass, configured_entry, error, key
):
    with (
        patch(
            "custom_components.solarmax.repairs.validate_connection", side_effect=error
        ),
        patch.object(hass.config_entries, "async_reload", return_value=True) as reload,
    ):
        result = await _submit_repair(hass, configured_entry, host="192.0.2.99")
    assert result["errors"] == {"base": key}
    assert configured_entry.data["host"] == "192.0.2.10"
    assert "verification_pending" not in _connection_issue(hass, configured_entry).data
    reload.assert_not_awaited()


@pytest.mark.parametrize("disabled", [False, True])
@pytest.mark.parametrize("changed", [False, True])
async def test_repair_without_runtime_preserves_disabled_state(
    hass, configured_entry, disabled, changed
):
    if disabled:
        object.__setattr__(configured_entry, "disabled_by", ConfigEntryDisabler.USER)
    host = "192.0.2.20" if changed else "192.0.2.10"
    original_options = dict(configured_entry.options)
    with (
        patch("custom_components.solarmax.repairs.validate_connection") as validate,
        patch.object(hass.config_entries, "async_reload", return_value=True) as reload,
    ):
        result = await _submit_repair(hass, configured_entry, host=host)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "repair_pending_verification"
    assert configured_entry.disabled_by is (
        ConfigEntryDisabler.USER if disabled else None
    )
    assert reload.await_count == (0 if disabled else 1)
    assert configured_entry.data == {
        "host": host,
        "port": 12345,
        "address": 7,
        "device_name": "Existing inverter",
    }
    assert dict(configured_entry.options) == original_options
    assert _connection_issue(hass, configured_entry).data["verification_pending"] == 1
    validate.assert_awaited_once_with(
        host=host, port=12345, address=7, verify_checksum=True
    )


async def test_repair_unchanged_runtime_handoff_and_refresh(hass, configured_entry):
    handed_off = False

    @asynccontextmanager
    async def handoff():
        nonlocal handed_off
        handed_off = True
        yield
        handed_off = False

    runtime = Mock(
        engine=Mock(validation_handoff=handoff), async_request_refresh=AsyncMock()
    )
    configured_entry.runtime_data = runtime
    hass.config_entries.async_update_entry(
        configured_entry, options={**configured_entry.options, "verify_checksum": False}
    )

    async def probe(**kwargs):
        assert handed_off
        assert kwargs["verify_checksum"] is False

    with (
        patch(
            "custom_components.solarmax.repairs.validate_connection", side_effect=probe
        ),
        patch.object(hass.config_entries, "async_reload") as reload,
    ):
        result = await _submit_repair(hass, configured_entry, host="192.0.2.10")
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    reload.assert_not_awaited()
    runtime.async_request_refresh.assert_awaited_once()
    assert not handed_off


@pytest.mark.parametrize("failure", [False, RuntimeError("reload failed")])
async def test_repair_reload_failure_restores_entry_and_open_issue(
    hass, configured_entry, failure
):
    with (
        patch("custom_components.solarmax.repairs.validate_connection"),
        patch.object(hass.config_entries, "async_reload", side_effect=[failure, True]),
    ):
        result = await _submit_repair(hass, configured_entry, host="192.0.2.20")
    assert result["errors"] == {"base": "reload_failed"}
    assert configured_entry.data["host"] == "192.0.2.10"
    assert _connection_issue(hass, configured_entry).data["verification_pending"] == 1


async def test_repair_failed_unload_accepts_recovered_original_runtime(
    hass, configured_entry
):
    """The runtime retained by a failed unload can verify the restored endpoint."""
    coordinator = SolarmaxCoordinator(hass, configured_entry)
    configured_entry.runtime_data = coordinator
    with (
        patch("custom_components.solarmax.repairs.validate_connection"),
        patch.object(hass.config_entries, "async_reload", return_value=False),
    ):
        result = await _submit_repair(hass, configured_entry, host="192.0.2.20")

    assert result["errors"] == {"base": "reload_failed"}
    assert configured_entry.data["host"] == "192.0.2.10"
    await coordinator._async_handle_snapshot(_snap(EngineState.ONLINE))
    assert _connection_issue(hass, configured_entry) is None


async def test_repair_missing_entry_aborts(hass, configured_entry):
    flow = await _repair_flow(hass, configured_entry)
    with patch.object(hass.config_entries, "async_get_entry", return_value=None):
        result = await flow.async_step_init()
    assert result["reason"] == "entry_missing"


async def test_repair_cancel_before_activation_removes_pending(hass, configured_entry):
    """Cancellation while leaving handoff cannot mark an unaccepted edit pending."""
    leaving, release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def handoff():
        yield
        leaving.set()
        await release.wait()

    configured_entry.runtime_data = Mock(engine=Mock(validation_handoff=handoff))
    with patch("custom_components.solarmax.repairs.validate_connection"):
        task = asyncio.create_task(
            _submit_repair(hass, configured_entry, host="192.0.2.20")
        )
        await leaving.wait()
        assert (
            _connection_issue(hass, configured_entry).data["verification_pending"] == 1
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert configured_entry.data["host"] == "192.0.2.10"
    assert "verification_pending" not in _connection_issue(hass, configured_entry).data


async def test_repair_aborts_when_poll_recovers_while_waiting_for_handoff(
    hass, configured_entry
):
    """A real engine poll finishes ahead of repair and clears its stale issue."""
    coordinator = SolarmaxCoordinator(hass, configured_entry)
    configured_entry.runtime_data = coordinator
    polling, release_poll = asyncio.Event(), asyncio.Event()

    async def poll_inner():
        polling.set()
        await release_poll.wait()
        return _snap(EngineState.ONLINE)

    with (
        patch.object(coordinator.engine, "_poll_inner", side_effect=poll_inner),
        patch("custom_components.solarmax.repairs.validate_connection") as validate,
        patch.object(coordinator, "async_request_refresh") as refresh,
        patch.object(hass.config_entries, "async_reload") as reload,
    ):
        poll_task = asyncio.create_task(coordinator._async_update_data())
        await asyncio.wait_for(polling.wait(), 1)
        repair_task = asyncio.create_task(
            _submit_repair(hass, configured_entry, host="192.0.2.10")
        )
        await asyncio.sleep(0)
        assert not repair_task.done()
        release_poll.set()
        assert (await poll_task).state is EngineState.ONLINE
        assert _connection_issue(hass, configured_entry) is None
        result = await asyncio.wait_for(repair_task, 1)
        await hass.async_block_till_done()

    assert result["reason"] == "issue_missing"
    assert _connection_issue(hass, configured_entry) is None
    assert configured_entry.data["host"] == "192.0.2.10"
    validate.assert_not_awaited()
    refresh.assert_not_awaited()
    reload.assert_not_awaited()


async def test_repair_aborts_when_completed_poll_clears_issue_during_probe(
    hass, configured_entry
):
    """An ONLINE snapshot delivered during probing must not be undone."""
    coordinator = SolarmaxCoordinator(hass, configured_entry)
    configured_entry.runtime_data = coordinator
    with patch.object(
        coordinator.engine, "_poll_inner", return_value=_snap(EngineState.ONLINE)
    ):
        recovered = await coordinator.engine.poll()

    async def probe(**kwargs):
        # The engine already returned this snapshot; handling it needs no lock.
        await coordinator._async_handle_snapshot(recovered)
        assert _connection_issue(hass, configured_entry) is None

    with (
        patch(
            "custom_components.solarmax.repairs.validate_connection", side_effect=probe
        ),
        patch.object(coordinator, "async_request_refresh") as refresh,
        patch.object(hass.config_entries, "async_reload") as reload,
    ):
        result = await _submit_repair(hass, configured_entry, host="192.0.2.20")

    assert result["reason"] == "issue_missing"
    assert _connection_issue(hass, configured_entry) is None
    assert configured_entry.data["host"] == "192.0.2.10"
    refresh.assert_not_awaited()
    reload.assert_not_awaited()


@pytest.mark.parametrize("during_probe", [False, True])
async def test_repair_rejects_endpoint_owned_by_other_address(
    hass, configured_entry, during_probe
):
    other = _configured_endpoint_entry(host="192.0.2.20", port=12345, address=8)
    if not during_probe:
        other.add_to_hass(hass)

    async def probe(**kwargs):
        other.add_to_hass(hass)

    with (
        patch(
            "custom_components.solarmax.repairs.validate_connection", side_effect=probe
        ) as validate,
        patch.object(hass.config_entries, "async_reload") as reload,
    ):
        result = await _submit_repair(hass, configured_entry, host="192.0.2.20")
    assert result["reason"] == "already_configured"
    assert validate.await_count == int(during_probe)
    reload.assert_not_awaited()
    assert configured_entry.data["host"] == "192.0.2.10"
    assert "verification_pending" not in _connection_issue(hass, configured_entry).data


async def test_overlapping_repairs_serialize_endpoint_ownership(hass, configured_entry):
    second = _configured_endpoint_entry(host="192.0.2.11", port=12345, address=8)
    second.add_to_hass(hass)
    _create_connection_issue(hass, second)
    started, release = asyncio.Event(), asyncio.Event()

    async def probe(**kwargs):
        started.set()
        await release.wait()

    with (
        patch(
            "custom_components.solarmax.repairs.validate_connection", side_effect=probe
        ) as validate,
        patch.object(hass.config_entries, "async_reload", return_value=True),
    ):
        first_task = asyncio.create_task(
            _submit_repair(hass, configured_entry, host="192.0.2.20")
        )
        await started.wait()
        second_task = asyncio.create_task(
            _submit_repair(hass, second, host="192.0.2.20")
        )
        await asyncio.sleep(0)
        release.set()
        first, last = await asyncio.gather(first_task, second_task)
    assert first["reason"] == "repair_pending_verification"
    assert last["reason"] == "already_configured"
    assert validate.await_count == 1


def test_translation_files_share_issues_and_sys_state_keys():
    """strings.json and every translations/*.json must declare identical
    keys under `issues` and `entity.sensor.sys.state`.

    A missing `issues` key breaks the repair dialog for that language; a
    missing `entity.sensor.sys.state` key means Home Assistant rejects the
    enum state entirely (`SYS_OPTIONS` in const.py must also stay in sync,
    checked separately below).
    """
    loaded = {path.name: _load_translation(path) for path in _TRANSLATION_PATHS}

    reference = next(iter(loaded.values()))
    reference_issue_keys = _leaf_key_paths(reference["issues"])
    reference_sys_state_keys = set(reference["entity"]["sensor"]["sys"]["state"])

    for name, content in loaded.items():
        assert _leaf_key_paths(content["issues"]) == reference_issue_keys, name
        assert (
            set(content["entity"]["sensor"]["sys"]["state"]) == reference_sys_state_keys
        ), name


def test_translation_files_cover_reconfiguration_and_repair_flows():
    """Every locale must expose each field and outcome used by the flows."""
    option_fields = {
        "update_interval",
        "verify_checksum",
        "night_keep_values",
        "twilight_elevation_threshold",
    }
    reconfigure_fields = {"host", "port", "address", "device_name"}
    repair_abort_keys = {
        "entry_missing",
        "already_configured",
        "repair_pending_verification",
        "issue_missing",
    }
    repair_error_keys = {"cannot_connect", "unknown", "reload_failed"}

    for path in _TRANSLATION_PATHS:
        content = _load_translation(path)
        reconfigure = content["config"]["step"]["reconfigure"]
        assert set(reconfigure["data"]) == reconfigure_fields, path.name
        assert set(reconfigure["data_description"]) == reconfigure_fields, path.name
        assert {
            "reconfigure_successful",
            "already_configured",
        } <= set(content["config"]["abort"]), path.name
        assert {"cannot_connect", "reload_failed"} <= set(content["config"]["error"]), (
            path.name
        )

        options = content["options"]
        assert set(options["step"]["init"]["data"]) == option_fields, path.name
        assert "{current_" not in options["step"]["init"]["description"], path.name
        assert set(options["error"]) == {"reload_failed"}, path.name

        repair = content["issues"]["connection_issues"]["fix_flow"]
        assert set(repair["step"]) == {"init"}, path.name
        assert set(repair["step"]["init"]["data"]) == {"host", "port"}, path.name
        assert set(repair["error"]) == repair_error_keys, path.name
        assert set(repair["abort"]) == repair_abort_keys, path.name


def test_sys_state_keys_match_sys_options():
    """Every translation file's `entity.sensor.sys.state` keys must equal
    `SYS_OPTIONS` -- Home Assistant rejects an enum state whose option
    isn't declared in every translation file."""
    for path in _TRANSLATION_PATHS:
        content = _load_translation(path)
        sys_state_keys = set(content["entity"]["sensor"]["sys"]["state"])
        assert sys_state_keys == set(SYS_OPTIONS), path.name


def test_sal_state_keys_match_sal_options():
    """Every alarm translation must declare exactly the supported options."""
    for path in _TRANSLATION_PATHS:
        content = _load_translation(path)
        sal_state_keys = set(content["entity"]["sensor"]["sal"]["state"])
        assert sal_state_keys == set(SAL_OPTIONS), path.name


def test_translation_files_share_data_description_keys():
    """Every translation file's `data_description` blocks (config.step.user
    and options.step.init) must declare the same fields as each other, and
    only fields that also exist in `data` -- HA renders `data_description`
    text next to the matching `data` field, so a stray or missing key
    silently does nothing rather than erroring."""
    loaded = {path.name: _load_translation(path) for path in _TRANSLATION_PATHS}

    for step_path in (("config", "step", "user"), ("options", "step", "init")):
        reference_description_keys = None
        for name, content in loaded.items():
            step = content
            for part in step_path:
                step = step[part]
            data_keys = set(step["data"])
            description_keys = set(step.get("data_description", {}))

            assert description_keys, (
                f"{name}: {'.'.join(step_path)} has no data_description"
            )
            assert description_keys <= data_keys, name

            if reference_description_keys is None:
                reference_description_keys = description_keys
            else:
                assert description_keys == reference_description_keys, name
