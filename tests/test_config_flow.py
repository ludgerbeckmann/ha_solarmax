"""Test the Solarmax config flow."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries, data_entry_flow
from homeassistant.config_entries import (
    ConfigEntryDisabler,
    ConfigEntryState,
    ConfigFlowResult,
    OperationNotAllowed,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmax.configuration import (
    OPTION_DEFAULTS,
    OPTION_KEYS,
    CannotConnect,
    EntryReloadError,
    async_apply_and_reload,
    configuration_mutation_lock,
    endpoint_unique_id,
    split_entry_input,
    validate_connection,
)
from custom_components.solarmax.connection import LinkClosed, LinkTimeout, SolarmaxLink
from custom_components.solarmax.const import (
    CONF_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_HOST,
    CONF_NIGHT_KEEP_VALUES,
    CONF_PORT,
    CONF_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_VERIFY_CHECKSUM,
    DOMAIN,
)
from custom_components.solarmax.protocol import build_request, calculate_checksum

_LINK_REQUEST = "custom_components.solarmax.configuration.SolarmaxLink.request"
_LINK_CLOSE = "custom_components.solarmax.configuration.SolarmaxLink.close"


@asynccontextmanager
async def _open_handoff() -> AsyncIterator[None]:
    yield


@pytest.fixture
def configured_entry(hass):
    entry = _configured_endpoint_entry(host="192.0.2.10", port=12345, address=1)
    engine = MagicMock()
    engine.validation_handoff.side_effect = _open_handoff
    engine.close = AsyncMock()
    entry.runtime_data = SimpleNamespace(engine=engine)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def reconfigure_io(hass):
    with (
        patch("custom_components.solarmax.config_flow.validate_connection") as probe,
        patch.object(hass.config_entries, "async_reload", return_value=True) as reload,
    ):
        yield probe, reload


async def _submit_reconfigure(hass, entry, **changes):
    form = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    return await hass.config_entries.flow.async_configure(
        form["flow_id"], dict(entry.data) | changes
    )


async def _submit_options(
    hass: HomeAssistant,
    entry: config_entries.ConfigEntry,
    *,
    update_interval: int | None = None,
    verify_checksum: bool | None = None,
    twilight_elevation_threshold: float | None = None,
    night_keep_values: bool | None = None,
) -> ConfigFlowResult:
    """Submit the current preferences with selected overrides."""
    values = {
        CONF_UPDATE_INTERVAL: entry.options[CONF_UPDATE_INTERVAL],
        CONF_VERIFY_CHECKSUM: entry.options[CONF_VERIFY_CHECKSUM],
        CONF_TWILIGHT_ELEVATION_THRESHOLD: entry.options[
            CONF_TWILIGHT_ELEVATION_THRESHOLD
        ],
        CONF_NIGHT_KEEP_VALUES: entry.options[CONF_NIGHT_KEEP_VALUES],
    }
    overrides = {
        CONF_UPDATE_INTERVAL: update_interval,
        CONF_VERIFY_CHECKSUM: verify_checksum,
        CONF_TWILIGHT_ELEVATION_THRESHOLD: twilight_elevation_threshold,
        CONF_NIGHT_KEEP_VALUES: night_keep_values,
    }
    values.update({key: value for key, value in overrides.items() if value is not None})
    form = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        form["flow_id"], user_input=values
    )


async def test_reconfigure_name_only_preserves_entities(
    hass, configured_entry, reconfigure_io
):
    probe, reload = reconfigure_io
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=configured_entry.entry_id,
        identifiers={(DOMAIN, configured_entry.entry_id)},
        name="Existing inverter",
    )
    entities = er.async_get(hass)
    entity = entities.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{configured_entry.entry_id}-pac",
        config_entry=configured_entry,
        device_id=device.id,
    )
    result = await _submit_reconfigure(hass, configured_entry, device_name="Garage")
    assert result["type"] is FlowResultType.ABORT
    assert configured_entry.title == configured_entry.data[CONF_DEVICE_NAME] == "Garage"
    assert devices.async_get(device.id).name == "Garage"
    assert entities.async_get(entity.entity_id) == entity
    probe.assert_not_awaited()
    reload.assert_not_awaited()


async def test_reconfigure_noop(hass, configured_entry, reconfigure_io):
    with patch.object(hass.config_entries, "async_update_entry") as update:
        result = await _submit_reconfigure(hass, configured_entry)
    assert result["type"] is FlowResultType.ABORT
    update.assert_not_called()
    for operation in reconfigure_io:
        operation.assert_not_awaited()


async def test_reconfigure_endpoint_success(hass, configured_entry, reconfigure_io):
    probe, reload = reconfigure_io
    result = await _submit_reconfigure(
        hass, configured_entry, host="192.0.2.99", address=7
    )
    assert result["type"] is FlowResultType.ABORT
    assert configured_entry.unique_id == "192.0.2.99:12345:7"
    assert configured_entry.options == OPTION_DEFAULTS
    probe.assert_awaited_once_with(
        host="192.0.2.99", port=12345, address=7, verify_checksum=True
    )
    configured_entry.runtime_data.engine.validation_handoff.assert_called_once()
    reload.assert_awaited_once_with(configured_entry.entry_id)


async def test_reconfigure_failed_probe(hass, configured_entry, reconfigure_io):
    probe, reload = reconfigure_io
    probe.side_effect = CannotConnect
    result = await _submit_reconfigure(hass, configured_entry, host="192.0.2.99")
    assert result["errors"] == {"base": "cannot_connect"}
    assert configured_entry.data[CONF_HOST] == "192.0.2.10"
    reload.assert_not_awaited()
    configured_entry.runtime_data.engine.close.assert_not_awaited()


@pytest.mark.parametrize("port", [0, 65536])
async def test_reconfigure_rejects_out_of_range_port_before_probe(
    hass, configured_entry, reconfigure_io, port
):
    probe, reload = reconfigure_io
    with pytest.raises(data_entry_flow.InvalidData):
        await _submit_reconfigure(hass, configured_entry, port=port)
    probe.assert_not_awaited()
    reload.assert_not_awaited()


@pytest.mark.parametrize("after_probe", [False, True])
async def test_reconfigure_endpoint_conflict(
    hass, configured_entry, reconfigure_io, after_probe
):
    probe, reload = reconfigure_io
    conflict = _configured_endpoint_entry(host="192.0.2.99", port=12345, address=2)
    if after_probe:
        probe.side_effect = lambda **kwargs: conflict.add_to_hass(hass)
    else:
        conflict.add_to_hass(hass)
    result = await _submit_reconfigure(hass, configured_entry, host="192.0.2.99")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert configured_entry.data[CONF_HOST] == "192.0.2.10"
    reload.assert_not_awaited()
    assert probe.await_count == int(after_probe)


@pytest.mark.parametrize(
    ("state", "disabled"),
    [
        (ConfigEntryState.NOT_LOADED, None),
        (ConfigEntryState.SETUP_ERROR, None),
        (ConfigEntryState.NOT_LOADED, ConfigEntryDisabler.USER),
    ],
)
async def test_reconfigure_without_runtime(
    hass, configured_entry, reconfigure_io, state, disabled
):
    probe, reload = reconfigure_io
    del configured_entry.runtime_data
    object.__setattr__(configured_entry, "state", state)
    object.__setattr__(configured_entry, "disabled_by", disabled)
    result = await _submit_reconfigure(hass, configured_entry, host="192.0.2.99")
    assert result["type"] is FlowResultType.ABORT
    assert configured_entry.data[CONF_HOST] == "192.0.2.99"
    assert configured_entry.disabled_by is disabled
    probe.assert_awaited_once()
    assert reload.await_count == (disabled is None)


async def test_disabled_reconfigure_updates_device_name(
    hass, configured_entry, reconfigure_io
):
    """A disabled endpoint edit still updates the existing device display name."""
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=configured_entry.entry_id,
        identifiers={(DOMAIN, configured_entry.entry_id)},
        name="Existing inverter",
    )
    object.__setattr__(configured_entry, "disabled_by", ConfigEntryDisabler.USER)

    result = await _submit_reconfigure(
        hass,
        configured_entry,
        host="192.0.2.99",
        device_name="Garage",
    )

    assert result["type"] is FlowResultType.ABORT
    assert devices.async_get(device.id).name == "Garage"


@pytest.mark.parametrize(
    "failure", [False, RuntimeError("activation"), OperationNotAllowed("unload")]
)
@pytest.mark.parametrize("runtime_survives", [False, True])
async def test_reconfigure_reload_failure_rolls_back(
    hass, configured_entry, reconfigure_io, failure, runtime_survives
):
    _, reload = reconfigure_io
    previous_runtime = configured_entry.runtime_data
    previous_data = dict(configured_entry.data)
    previous_options = dict(configured_entry.options)
    previous_title = configured_entry.title
    previous_id = configured_entry.unique_id

    async def activate(entry_id):
        if reload.await_count > 1:
            assert dict(configured_entry.data) == previous_data
            return True
        if not runtime_survives:
            del configured_entry.runtime_data
        else:
            object.__setattr__(
                configured_entry, "state", ConfigEntryState.FAILED_UNLOAD
            )
        if isinstance(failure, Exception):
            raise failure
        return failure

    reload.side_effect = activate
    result = await _submit_reconfigure(
        hass, configured_entry, host="192.0.2.99", device_name="New"
    )
    assert result["errors"] == {"base": "reload_failed"}
    assert dict(configured_entry.data) == previous_data
    assert dict(configured_entry.options) == previous_options
    assert configured_entry.title == previous_title
    assert configured_entry.unique_id == previous_id
    assert reload.await_count == (1 if runtime_survives else 2)
    previous_runtime.engine.close.assert_not_awaited()


async def test_reconfigure_retry_after_failed_unload(
    hass, configured_entry, reconfigure_io
):
    _, reload = reconfigure_io
    object.__setattr__(configured_entry, "state", ConfigEntryState.FAILED_UNLOAD)
    reload.side_effect = [False, OperationNotAllowed("previous unload failed")]
    for host in ("192.0.2.98", "192.0.2.99"):
        result = await _submit_reconfigure(hass, configured_entry, host=host)
        assert result["errors"] == {"base": "reload_failed"}
        assert configured_entry.data[CONF_HOST] == "192.0.2.10"
    assert reload.await_count == 2


@pytest.mark.parametrize("restoration", [False, RuntimeError("restoration failed")])
async def test_reconfigure_failed_restoration_attempted_once(
    hass, configured_entry, reconfigure_io, restoration
):
    """Even a failed restoration leaves old persisted values and stops retrying."""
    _, reload = reconfigure_io
    del configured_entry.runtime_data
    reload.side_effect = [False, restoration]
    async with configuration_mutation_lock(hass):
        with pytest.raises(EntryReloadError):
            await async_apply_and_reload(
                hass,
                configured_entry,
                data=dict(configured_entry.data) | {CONF_HOST: "192.0.2.99"},
                options=dict(configured_entry.options) | {CONF_UPDATE_INTERVAL: 90},
                title="New",
                unique_id="192.0.2.99:12345:1",
            )
    assert configured_entry.data[CONF_HOST] == "192.0.2.10"
    assert configured_entry.options[CONF_UPDATE_INTERVAL] == 30
    assert configured_entry.title == "Existing inverter"
    assert configured_entry.unique_id == "192.0.2.10:12345:1"
    assert reload.await_count == 2


@pytest.mark.parametrize("restore", [False, True])
async def test_reconfigure_repeated_cancellation_finishes_transaction(
    hass, configured_entry, reconfigure_io, restore
):
    _, reload = reconfigure_io
    activation_started, release_activation = asyncio.Event(), asyncio.Event()
    restore_started, release_restore = asyncio.Event(), asyncio.Event()

    async def activate(entry_id):
        if reload.await_count == 1:
            activation_started.set()
            await release_activation.wait()
            if restore:
                del configured_entry.runtime_data
            return not restore
        restore_started.set()
        await release_restore.wait()
        return True

    reload.side_effect = activate
    submit = asyncio.create_task(
        _submit_reconfigure(hass, configured_entry, host="192.0.2.99")
    )
    await asyncio.wait_for(activation_started.wait(), 2)
    submit.cancel()
    await asyncio.sleep(0)
    submit.cancel()
    assert configuration_mutation_lock(hass).locked()
    release_activation.set()
    if restore:
        await asyncio.wait_for(restore_started.wait(), 2)
        submit.cancel()
        await asyncio.sleep(0)
        submit.cancel()
        assert configuration_mutation_lock(hass).locked()
        release_restore.set()
    with pytest.raises(asyncio.CancelledError):
        await submit
    assert configured_entry.data[CONF_HOST] == (
        "192.0.2.10" if restore else "192.0.2.99"
    )
    assert reload.await_count == (2 if restore else 1)
    assert not configuration_mutation_lock(hass).locked()


async def test_reconfigure_rollback_cannot_overwrite_overlapping_rename(
    hass, configured_entry, reconfigure_io
):
    _, reload = reconfigure_io
    started, release = asyncio.Event(), asyncio.Event()
    rename_form = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": configured_entry.entry_id,
        },
    )
    rename_data = dict(configured_entry.data) | {CONF_DEVICE_NAME: "Garage"}

    async def fail(entry_id):
        started.set()
        await release.wait()
        return False

    reload.side_effect = fail
    connection = asyncio.create_task(
        _submit_reconfigure(hass, configured_entry, host="192.0.2.99")
    )
    await asyncio.wait_for(started.wait(), 2)
    rename = asyncio.create_task(
        hass.config_entries.flow.async_configure(rename_form["flow_id"], rename_data)
    )
    await asyncio.sleep(0)
    assert not rename.done()
    release.set()
    await connection
    await rename
    assert configured_entry.data[CONF_HOST] == "192.0.2.10"
    assert configured_entry.data[CONF_DEVICE_NAME] == configured_entry.title == "Garage"


def test_split_entry_input_separates_connection_data_from_options() -> None:
    """Config entry values are partitioned by their canonical ownership."""
    data, options = split_entry_input(
        {
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 12345,
            CONF_ADDRESS: 7,
            CONF_DEVICE_NAME: "Roof",
            CONF_UPDATE_INTERVAL: 45,
            CONF_VERIFY_CHECKSUM: False,
            CONF_TWILIGHT_ELEVATION_THRESHOLD: 4.5,
            CONF_NIGHT_KEEP_VALUES: True,
        }
    )

    assert data == {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 12345,
        CONF_ADDRESS: 7,
        CONF_DEVICE_NAME: "Roof",
    }
    assert options == {
        CONF_UPDATE_INTERVAL: 45,
        CONF_VERIFY_CHECKSUM: False,
        CONF_TWILIGHT_ELEVATION_THRESHOLD: 4.5,
        CONF_NIGHT_KEEP_VALUES: True,
    }


def _response(data: str, *, checksum: str | None = None) -> str:
    """Build one structurally valid MaxComm response frame."""
    response = "{01;FB;!!|64:" + data + "|$$$$}"
    response = response.replace("!!", format(len(response), "02X"))
    checksum_data = response[1:-5]
    return response.replace("$$$$", checksum or calculate_checksum(checksum_data))


def _configured_endpoint_entry(
    *, host: str, port: int, address: int
) -> MockConfigEntry:
    """Create a current-version entry for one inverter endpoint."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Existing inverter",
        data={
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_ADDRESS: address,
            CONF_DEVICE_NAME: "Existing inverter",
        },
        options=dict(OPTION_DEFAULTS),
        unique_id=endpoint_unique_id(host, port, address),
        version=2,
        minor_version=1,
    )


async def _submit_user_flow(
    hass: HomeAssistant, *, host: str, port: int, address: int
) -> ConfigFlowResult:
    """Submit one complete initial setup flow."""
    form = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_ADDRESS: address,
            CONF_DEVICE_NAME: "New inverter",
            **OPTION_DEFAULTS,
        },
    )


@patch.object(SolarmaxLink, "request", new_callable=AsyncMock)
async def test_setup_rejects_same_endpoint_with_different_address(
    mock_request: AsyncMock, hass: HomeAssistant
) -> None:
    """Only one config entry may claim an inverter TCP endpoint."""
    mock_request.return_value = _response("PAC=03E8")
    existing = _configured_endpoint_entry(host="192.0.2.5", port=12345, address=1)
    existing.add_to_hass(hass)

    result = await _submit_user_flow(hass, host="192.0.2.5", port=12345, address=2)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@patch.object(SolarmaxLink, "request", new_callable=AsyncMock)
async def test_setup_uses_address_aware_unique_id(
    mock_request: AsyncMock, hass: HomeAssistant
) -> None:
    """The inverter address participates in config entry identity."""
    mock_request.return_value = _response("PAC=03E8")

    result = await _submit_user_flow(hass, host="192.0.2.6", port=12345, address=7)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "192.0.2.6:12345:7"


@patch.object(SolarmaxLink, "request", new_callable=AsyncMock)
async def test_setup_creates_split_data_and_options(
    mock_request: AsyncMock, hass: HomeAssistant
) -> None:
    """Connection identity and preferences use their canonical stores."""
    mock_request.return_value = _response("PAC=03E8")

    result = await _submit_user_flow(hass, host="192.0.2.7", port=12345, address=3)

    assert result["data"] == {
        CONF_HOST: "192.0.2.7",
        CONF_PORT: 12345,
        CONF_ADDRESS: 3,
        CONF_DEVICE_NAME: "New inverter",
    }
    assert result["options"] == OPTION_DEFAULTS


async def test_setup_serializes_concurrent_claims_for_endpoint(
    hass: HomeAssistant,
) -> None:
    """Concurrent flows cannot both claim the same TCP endpoint."""
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    async def blocked_probe(**kwargs: Any) -> None:
        probe_started.set()
        await release_probe.wait()

    with (
        patch(
            "custom_components.solarmax.config_flow.validate_connection",
            side_effect=blocked_probe,
        ),
        patch(
            "custom_components.solarmax.async_setup_entry",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        first_submission = asyncio.create_task(
            _submit_user_flow(hass, host="192.0.2.8", port=12345, address=1)
        )
        await probe_started.wait()
        second_submission = asyncio.create_task(
            _submit_user_flow(hass, host="192.0.2.8", port=12345, address=2)
        )
        await asyncio.sleep(0)
        assert not second_submission.done()
        release_probe.set()
        first, second = await asyncio.gather(first_submission, second_submission)

    assert sorted(result["type"] for result in (first, second)) == [
        FlowResultType.ABORT,
        FlowResultType.CREATE_ENTRY,
    ]
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_form(hass: HomeAssistant) -> None:
    """Test we get the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_form_successful_connection(mock_request, hass: HomeAssistant) -> None:
    """Test successful config flow."""
    mock_request.return_value = _response("PAC=03E8")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
            CONF_ADDRESS: 1,
            CONF_DEVICE_NAME: "Test Inverter",
            CONF_UPDATE_INTERVAL: 30,
        },
    )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Test Inverter"
    assert result2["data"] == {
        CONF_HOST: "192.168.1.100",
        CONF_PORT: 12345,
        CONF_ADDRESS: 1,
        CONF_DEVICE_NAME: "Test Inverter",
    }
    assert result2["options"] == OPTION_DEFAULTS


@patch(_LINK_CLOSE, new_callable=AsyncMock)
@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_validate_connection_closes_probe_link_on_success(
    mock_request, mock_close
) -> None:
    """The `finally: await link.close()` is a hard invariant (spec: a leaked
    probe socket locks the single-client inverter out for ~128s and would
    fail the very next setup attempt) — must run even when the probe
    succeeds. Also pins that the configured address reaches the PAC probe.
    """
    mock_request.return_value = _response("PAC=03E8")

    await validate_connection(
        host="192.168.1.100",
        port=12345,
        address=2,
        verify_checksum=True,
    )

    mock_close.assert_awaited_once()
    mock_request.assert_awaited_once_with(build_request(2, ["PAC"]))


@patch(_LINK_CLOSE, new_callable=AsyncMock)
@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_validate_connection_closes_probe_link_on_failure(
    mock_request, mock_close
) -> None:
    """Same invariant on the failure path — and covers the LinkClosed arm of
    the except tuple, which no other test exercises.
    """
    mock_request.side_effect = LinkClosed("peer closed the connection")

    with pytest.raises(CannotConnect):
        await validate_connection(
            host="192.168.1.100",
            port=12345,
            address=1,
            verify_checksum=True,
        )

    mock_close.assert_awaited_once()


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_validate_connection_rejects_non_maxcomm_response(mock_request) -> None:
    """A TCP service that merely terminates with `}` is not an inverter."""
    mock_request.return_value = "not-a-maxcomm-frame}"

    with pytest.raises(CannotConnect):
        await validate_connection(
            host="192.168.1.100",
            port=12345,
            address=1,
            verify_checksum=True,
        )


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_validate_connection_rejects_invalid_checksum(
    mock_request,
) -> None:
    """Checksum-enabled setup validation rejects corrupted MaxComm frames."""
    mock_request.return_value = _response("PAC=03E8", checksum="0000")

    with pytest.raises(CannotConnect):
        await validate_connection(
            host="192.168.1.100",
            port=12345,
            address=1,
            verify_checksum=True,
        )


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_validate_connection_honors_disabled_checksum(
    mock_request,
) -> None:
    """The user's ignore-checksum choice also applies to the setup probe."""
    mock_request.return_value = _response("PAC=03E8", checksum="0000")

    await validate_connection(
        host="192.168.1.100",
        port=12345,
        address=1,
        verify_checksum=False,
    )


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_validate_connection_accepts_not_applicable_pac(
    mock_request,
) -> None:
    """A valid inverter may report PAC as unavailable while not producing."""
    mock_request.return_value = _response("PAC")

    await validate_connection(
        host="192.168.1.100",
        port=12345,
        address=1,
        verify_checksum=True,
    )


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_invalid_hostname_returns_reconfigure_form(mock_request, hass):
    """IDNA encoding failures are reported as connection validation errors."""
    mock_request.side_effect = UnicodeEncodeError(
        "idna", "a" * 64, 0, 64, "label too long"
    )
    entry = _configured_endpoint_entry(host="127.0.0.1", port=12345, address=1)
    entry.add_to_hass(hass)

    result = await _submit_reconfigure(hass, entry, host="a" * 64 + ".invalid")

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_HOST] == "127.0.0.1"


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_update_interval_out_of_range(mock_request, hass: HomeAssistant) -> None:
    """Test that an out-of-range update interval is rejected by the schema."""
    mock_request.return_value = _response("PAC=03E8")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with pytest.raises(data_entry_flow.InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.1.100",
                CONF_PORT: 12345,
                CONF_UPDATE_INTERVAL: 0,
            },
        )


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_form_cannot_connect(mock_request, hass: HomeAssistant) -> None:
    """Test we handle cannot connect error (LinkTimeout -> cannot_connect)."""
    mock_request.side_effect = LinkTimeout("no response")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
        },
    )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_form_unexpected_exception(mock_request, hass: HomeAssistant) -> None:
    """Test we handle unexpected exceptions."""
    mock_request.side_effect = Exception("Test exception")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
        },
    )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "unknown"}


@patch(_LINK_REQUEST, new_callable=AsyncMock)
async def test_duplicate_entry_prevention(mock_request, hass: HomeAssistant) -> None:
    """Test that duplicate entries are prevented."""
    mock_request.return_value = _response("PAC=03E8")

    # Create first entry
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
            CONF_ADDRESS: 1,
            CONF_DEVICE_NAME: "Test Inverter",
            CONF_UPDATE_INTERVAL: 30,
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY

    # Try to create duplicate entry
    result3 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result4 = await hass.config_entries.flow.async_configure(
        result3["flow_id"],
        {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
            CONF_ADDRESS: 1,
            CONF_DEVICE_NAME: "Test Inverter 2",
            CONF_UPDATE_INTERVAL: 60,
        },
    )

    assert result4["type"] == FlowResultType.ABORT
    assert result4["reason"] == "already_configured"


async def test_options_form_contains_only_preferences(hass, configured_entry):
    """Connection identity must remain exclusive to native reconfiguration."""
    result = await hass.config_entries.options.async_init(configured_entry.entry_id)

    assert {str(key) for key in result["data_schema"].schema} == set(OPTION_KEYS)


async def test_disabled_v1_options_form_preserves_legacy_values(hass):
    """Options remain editable before Home Assistant migrates a disabled entry."""
    legacy_options = {
        CONF_UPDATE_INTERVAL: 90,
        CONF_VERIFY_CHECKSUM: False,
        CONF_TWILIGHT_ELEVATION_THRESHOLD: 3,
        CONF_NIGHT_KEEP_VALUES: True,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=1,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 12345,
            CONF_ADDRESS: 1,
            CONF_DEVICE_NAME: "Existing inverter",
            **legacy_options,
        },
        options={},
    )
    entry.add_to_hass(hass)
    object.__setattr__(entry, "disabled_by", ConfigEntryDisabler.USER)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert {
        str(key): key.default() for key in result["data_schema"].schema
    } == legacy_options


async def test_disabled_v1_reconfigure_defaults_missing_address(hass):
    """Reconfiguration remains usable before a disabled entry can migrate."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 12345,
            CONF_DEVICE_NAME: "Existing inverter",
        },
        options={},
    )
    entry.add_to_hass(hass)
    object.__setattr__(entry, "disabled_by", ConfigEntryDisabler.USER)

    form = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    defaults = {str(key): key.default() for key in form["data_schema"].schema}

    assert defaults[CONF_ADDRESS] == 1
    result = await hass.config_entries.flow.async_configure(form["flow_id"], defaults)
    assert result["type"] is FlowResultType.ABORT


async def test_options_save_reloads_without_connection_probe(
    hass, configured_entry, reconfigure_io
):
    """Preference changes reload through the existing transaction without probing."""
    validate, reload_entry = reconfigure_io

    result = await _submit_options(
        hass,
        configured_entry,
        update_interval=90,
        verify_checksum=False,
        twilight_elevation_threshold=4,
        night_keep_values=True,
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert configured_entry.options == {
        CONF_UPDATE_INTERVAL: 90,
        CONF_VERIFY_CHECKSUM: False,
        CONF_TWILIGHT_ELEVATION_THRESHOLD: 4,
        CONF_NIGHT_KEEP_VALUES: True,
    }
    validate.assert_not_awaited()
    configured_entry.runtime_data.engine.validation_handoff.assert_not_called()
    reload_entry.assert_awaited_once_with(configured_entry.entry_id)


async def test_options_reload_failure_restores_old_options(
    hass, configured_entry, reconfigure_io
):
    """A rejected activation cannot leave the submitted preferences persisted."""
    _, reload_entry = reconfigure_io
    reload_entry.side_effect = [False, True]

    result = await _submit_options(hass, configured_entry, update_interval=90)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "reload_failed"}
    assert configured_entry.options[CONF_UPDATE_INTERVAL] == 30


async def test_options_wait_for_connection_transaction(
    hass, configured_entry, reconfigure_io
):
    """An option save cannot race a failing endpoint change and its rollback."""
    _, reload_entry = reconfigure_io
    reload_started = asyncio.Event()
    release_reload = asyncio.Event()

    async def reload_in_order(entry_id: str) -> bool:
        if reload_entry.await_count == 1:
            reload_started.set()
            await release_reload.wait()
            return False
        return True

    reload_entry.side_effect = reload_in_order
    connection_change = asyncio.create_task(
        _submit_reconfigure(hass, configured_entry, host="192.0.2.99")
    )
    await reload_started.wait()
    option_change = asyncio.create_task(
        _submit_options(hass, configured_entry, update_interval=90)
    )
    await asyncio.sleep(0)
    assert not option_change.done()
    release_reload.set()
    await connection_change
    await option_change

    assert configured_entry.data[CONF_HOST] == "192.0.2.10"
    assert configured_entry.options[CONF_UPDATE_INTERVAL] == 90


async def test_options_noop_does_not_update_or_reload(
    hass, configured_entry, reconfigure_io
):
    """Resubmitting identical preferences has no persistence side effects."""
    validate, reload_entry = reconfigure_io
    with patch.object(hass.config_entries, "async_update_entry") as update:
        result = await _submit_options(hass, configured_entry)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    update.assert_not_called()
    reload_entry.assert_not_awaited()
    validate.assert_not_awaited()
    configured_entry.runtime_data.engine.validation_handoff.assert_not_called()


async def test_options_disabled_entry_saves_without_reload(
    hass, configured_entry, reconfigure_io
):
    """Preference changes persist without enabling a disabled entry."""
    validate, reload_entry = reconfigure_io
    object.__setattr__(configured_entry, "disabled_by", ConfigEntryDisabler.USER)

    result = await _submit_options(hass, configured_entry, update_interval=90)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert configured_entry.options[CONF_UPDATE_INTERVAL] == 90
    assert configured_entry.disabled_by is ConfigEntryDisabler.USER
    reload_entry.assert_not_awaited()
    validate.assert_not_awaited()


async def test_night_keep_values_defaults_to_disabled(hass):
    """The new option must not change behaviour for anyone who ignores it."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Read the default off the vol.Optional marker rather than validating
    # an empty dict — the schema has vol.Required("host"), so schema({})
    # raises MultipleInvalid instead of applying defaults.
    schema_keys = {str(key): key for key in result["data_schema"].schema}
    assert "night_keep_values" in schema_keys
    assert schema_keys["night_keep_values"].default() is False
