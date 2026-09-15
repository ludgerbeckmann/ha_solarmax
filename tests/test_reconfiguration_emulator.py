"""Reconfiguration handoff tests against the single-client emulator."""

import asyncio
import threading
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

from custom_components.solarmax import async_setup_entry, sensor
from custom_components.solarmax.configuration import validate_connection
from custom_components.solarmax.connection import (
    ConnectionEngine,
    EngineState,
    SolarmaxLink,
)
from custom_components.solarmax.coordinator import SolarmaxCoordinator
from custom_components.solarmax.protocol import build_request, parse_response
from tests.emulator import EmulatorHandle
from tests.test_config_flow import (
    _configured_endpoint_entry,
    _submit_options,
    _submit_reconfigure,
)
from tests.test_repairs import (
    _connection_issue,
    _create_connection_issue,
    _submit_repair,
)


@pytest.fixture
def proposed_emulator(socket_enabled):
    handle = EmulatorHandle()
    handle.start()
    yield handle
    handle.stop()


async def _loaded_entry(hass, emulator):
    host, port = emulator.addr
    entry = _configured_endpoint_entry(host=host, port=port, address=1)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert (await entry.runtime_data.engine.poll()).state is EngineState.ONLINE
    return entry


async def test_reconfigure_emulator_success(hass, emulator, proposed_emulator):
    """Real HA unload and setup transfer the connection to the proposed endpoint."""
    entry = await _loaded_entry(hass, emulator)
    old_engine = entry.runtime_data.engine
    host, port = proposed_emulator.addr
    try:
        result = await asyncio.wait_for(
            _submit_reconfigure(hass, entry, host=host, port=port), 5
        )
        assert result["type"] is FlowResultType.ABORT
        assert entry.data["port"] == port
        assert entry.runtime_data.engine is not old_engine
        assert (await entry.runtime_data.engine.poll()).state is EngineState.ONLINE
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_emulator_probe_failure(hass, emulator, proposed_emulator):
    """Rejected probe leaves the original engine able to reconnect."""
    entry = await _loaded_entry(hass, emulator)
    old_engine = entry.runtime_data.engine
    proposed_emulator.inject("corrupt_crc")
    host, port = proposed_emulator.addr
    try:
        result = await _submit_reconfigure(hass, entry, host=host, port=port)
        assert result["errors"] == {"base": "cannot_connect"}
        assert entry.data["port"] == emulator.addr[1]
        assert entry.runtime_data.engine is old_engine
        assert (await old_engine.poll()).state is EngineState.ONLINE
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_waits_for_startup_connection_owner(hass, emulator):
    """Validation must not overlap an entry's first refresh connection."""
    host, port = emulator.addr
    entry = _configured_endpoint_entry(host=host, port=port, address=1)
    entry.add_to_hass(hass)
    response_started = threading.Event()
    release_response = threading.Event()
    original_response = emulator._emulator.build_response
    original_refresh = SolarmaxCoordinator.async_config_entry_first_refresh
    starting_runtime = None
    overlaps: list[bool] = []

    def delayed_response(*args, **kwargs):
        response_started.set()
        assert release_response.wait(5)
        return original_response(*args, **kwargs)

    async def wait_for_response() -> None:
        while not response_started.is_set():
            await asyncio.sleep(0.001)

    async def first_refresh(coordinator):
        nonlocal starting_runtime
        starting_runtime = coordinator
        await original_refresh(coordinator)

    async def probe(**kwargs):
        overlaps.append(starting_runtime.engine._link.connected)
        return await validate_connection(**kwargs)

    try:
        with (
            patch.object(
                emulator._emulator, "build_response", side_effect=delayed_response
            ),
            patch.object(
                SolarmaxCoordinator,
                "async_config_entry_first_refresh",
                first_refresh,
            ),
            patch(
                "custom_components.solarmax.config_flow.validate_connection",
                side_effect=probe,
            ),
        ):
            setup = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
            await asyncio.wait_for(wait_for_response(), 5)
            reconfigure = asyncio.create_task(
                _submit_reconfigure(hass, entry, address=2)
            )
            await asyncio.sleep(0.05)
            release_response.set()
            assert await asyncio.wait_for(setup, 5)
            await asyncio.wait_for(reconfigure, 5)

        assert overlaps == [False]
    finally:
        release_response.set()
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)
        if starting_runtime is not None:
            await starting_runtime.async_shutdown()
            await starting_runtime.engine.close()


async def test_sensor_setup_failure_rolls_back_reconfiguration(
    hass, emulator, proposed_emulator
):
    """A platform exception swallowed by HA must still fail the reload."""
    entry = await _loaded_entry(hass, emulator)
    old_data = dict(entry.data)
    original_setup = sensor.async_setup_entry

    async def setup_sensor(hass, current, add_entities):
        if current.data["port"] == proposed_emulator.addr[1]:
            raise RuntimeError("sensor initialization failed")
        return await original_setup(hass, current, add_entities)

    try:
        with patch.object(sensor, "async_setup_entry", side_effect=setup_sensor):
            result = await _submit_reconfigure(
                hass,
                entry,
                host=proposed_emulator.addr[0],
                port=proposed_emulator.addr[1],
            )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "reload_failed"}
        assert dict(entry.data) == old_data
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("same_endpoint", [False, True])
async def test_failed_setup_closes_connection_before_restoration(
    hass, emulator, proposed_emulator, same_endpoint
):
    """A platform setup error releases the client slot before rollback reloads."""
    entry = await _loaded_entry(hass, emulator)
    old_runtime = entry.runtime_data
    old_data, old_options = dict(entry.data), dict(entry.options)
    failed_runtime = None
    forward = hass.config_entries.async_forward_entry_setups

    async def fail_first_forward(entry, platforms):
        nonlocal failed_runtime
        if failed_runtime is None:
            failed_runtime = entry.runtime_data
            assert failed_runtime is not old_runtime
            assert failed_runtime.data.state is EngineState.ONLINE
            assert failed_runtime.engine._link.connected
            raise RuntimeError("platform forwarding failed after first refresh")
        assert not failed_runtime.engine._link.connected
        await forward(entry, platforms)

    try:
        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=fail_first_forward,
        ):
            if same_endpoint:
                result = await _submit_options(hass, entry, update_interval=90)
            else:
                host, port = proposed_emulator.addr
                result = await _submit_reconfigure(hass, entry, host=host, port=port)
        assert result["errors"] == {"base": "reload_failed"}
        assert failed_runtime is not None
        assert not failed_runtime.engine._link.connected
        assert dict(entry.data) == old_data
        assert dict(entry.options) == old_options
        assert entry.runtime_data is not old_runtime
        assert entry.runtime_data is not failed_runtime
        assert entry.runtime_data.data.state is EngineState.ONLINE
        assert (await entry.runtime_data.engine.poll()).state is EngineState.ONLINE
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        # Also clean up on RED, so the assertion remains the failure signal.
        if failed_runtime is not None:
            await failed_runtime.engine.close()
    for handle in (emulator, proposed_emulator):
        for thread in tuple(handle._emulator._client_threads):
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive()


@pytest.mark.parametrize("during_refresh", [False, True])
async def test_cancelled_setup_closes_open_connection(hass, emulator, during_refresh):
    """Cancellation releases a connected setup with or without runtime_data."""
    host, port = emulator.addr
    entry = _configured_endpoint_entry(host=host, port=port, address=1)
    entry.add_to_hass(hass)
    connected = asyncio.Event()
    runtime = None
    first_refresh = SolarmaxCoordinator.async_config_entry_first_refresh

    async def refresh(coordinator):
        nonlocal runtime
        runtime = coordinator
        await first_refresh(coordinator)
        if during_refresh:
            connected.set()
            await asyncio.Event().wait()

    async def forward(entry, platforms):
        assert entry.runtime_data is runtime
        connected.set()
        await asyncio.Event().wait()

    try:
        with (
            patch.object(
                SolarmaxCoordinator, "async_config_entry_first_refresh", refresh
            ),
            patch.object(
                hass.config_entries, "async_forward_entry_setups", side_effect=forward
            ),
        ):
            setup = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
            await asyncio.wait_for(connected.wait(), 5)
            assert runtime.engine._link.connected
            setup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await setup
        assert not runtime.engine._link.connected
        # The inverter's only slot is available immediately after cancellation.
        await validate_connection(host=host, port=port, address=1, verify_checksum=True)
    finally:
        if runtime is not None:
            await runtime.async_shutdown()
            await runtime.engine.close()
    for thread in tuple(emulator._emulator._client_threads):
        await asyncio.to_thread(thread.join, 5)
        assert not thread.is_alive()


async def test_failed_initial_setup_detaches_closed_runtime(
    hass, emulator, proposed_emulator
):
    """A setup error must not leave its closed coordinator attached."""
    host, port = emulator.addr
    entry = _configured_endpoint_entry(host=host, port=port, address=1)
    entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("platform setup failed"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert not hasattr(entry, "runtime_data")

    new_host, new_port = proposed_emulator.addr
    try:
        result = await _submit_reconfigure(hass, entry, host=new_host, port=new_port)
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.runtime_data.data.state is EngineState.ONLINE
    finally:
        if entry.state is ConfigEntryState.LOADED:
            await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_emulator_cancel_validation(
    hass, emulator, proposed_emulator
):
    """Cancelling while the old engine is handed off releases its polling lock."""
    entry = await _loaded_entry(hass, emulator)
    old_engine = entry.runtime_data.engine
    validated = asyncio.Event()

    async def blocked_probe(**kwargs):
        await validate_connection(**kwargs)
        validated.set()
        await asyncio.Event().wait()

    host, port = proposed_emulator.addr
    try:
        with patch(
            "custom_components.solarmax.config_flow.validate_connection",
            side_effect=blocked_probe,
        ):
            submit = asyncio.create_task(
                _submit_reconfigure(hass, entry, host=host, port=port)
            )
            await asyncio.wait_for(validated.wait(), 5)
            submit.cancel()
            with pytest.raises(asyncio.CancelledError):
                await submit
        assert entry.data["port"] == emulator.addr[1]
        assert (await old_engine.poll()).state is EngineState.ONLINE
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_emulator_cancel_failed_activation_restores(
    hass, emulator, proposed_emulator
):
    """After actual unload, cancelled setup failure restores a usable old endpoint."""
    entry = await _loaded_entry(hass, emulator)
    old_runtime = entry.runtime_data
    activation_started, release_activation = asyncio.Event(), asyncio.Event()
    restoration_started, release_restoration = asyncio.Event(), asyncio.Event()
    host, port = proposed_emulator.addr

    async def setup(hass, entry):
        if entry.data["port"] == port:
            assert getattr(entry, "runtime_data", None) is not old_runtime
            activation_started.set()
            await release_activation.wait()
            return False
        restoration_started.set()
        await release_restoration.wait()
        return await async_setup_entry(hass, entry)

    try:
        with patch("custom_components.solarmax.async_setup_entry", side_effect=setup):
            submit = asyncio.create_task(
                _submit_reconfigure(hass, entry, host=host, port=port)
            )
            await asyncio.wait_for(activation_started.wait(), 5)
            submit.cancel()
            await asyncio.sleep(0)
            submit.cancel()
            release_activation.set()
            await asyncio.wait_for(restoration_started.wait(), 5)
            submit.cancel()
            await asyncio.sleep(0)
            submit.cancel()
            release_restoration.set()
            with pytest.raises(asyncio.CancelledError):
                await submit
        assert entry.data["port"] == emulator.addr[1]
        assert entry.runtime_data is not old_runtime
        assert (await entry.runtime_data.engine.poll()).state is EngineState.ONLINE
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_validation_handoff_releases_single_client_slot(emulator):
    """A validation client owns the sole socket slot only during handoff."""
    host, port = emulator.addr
    link = SolarmaxLink(host, port)
    engine = ConnectionEngine(link, address=1, sun_below=lambda: False)
    assert (await engine.poll()).state is EngineState.ONLINE

    async with engine.validation_handoff():
        probe = SolarmaxLink(host, port)
        try:
            raw = await probe.request(build_request(1, ["PAC"]))
            assert parse_response(raw, verify_checksum=True)["PAC"]["value"] >= 0
        finally:
            await probe.close()

    assert (await engine.poll()).state is EngineState.ONLINE
    await engine.close()


@pytest.mark.parametrize("changed", [False, True])
async def test_repair_emulator_waits_for_full_poll(
    hass, emulator, proposed_emulator, changed
):
    """Changed and unchanged repairs retain the issue until a real full poll."""
    entry = await _loaded_entry(hass, emulator)
    _create_connection_issue(hass, entry)
    before_poll, release_poll = asyncio.Event(), asyncio.Event()
    original_update = SolarmaxCoordinator._async_update_data

    async def gated_update(coordinator):
        before_poll.set()
        await release_poll.wait()
        return await original_update(coordinator)

    host, port = (proposed_emulator if changed else emulator).addr
    try:
        with patch.object(SolarmaxCoordinator, "_async_update_data", gated_update):
            task = asyncio.create_task(
                _submit_repair(hass, entry, host=host, port=port)
            )
            await asyncio.wait_for(before_poll.wait(), 5)
            assert _connection_issue(hass, entry).data["verification_pending"] == 1
            release_poll.set()
            result = await asyncio.wait_for(task, 5)
            await hass.async_block_till_done()
        assert result["type"] is FlowResultType.ABORT
        assert entry.data["port"] == port
        assert entry.runtime_data.data.state is EngineState.ONLINE
        assert _connection_issue(hass, entry) is None
    finally:
        release_poll.set()
        await hass.config_entries.async_unload(entry.entry_id)


async def test_old_runtime_poll_cannot_verify_new_repair_endpoint(
    hass, emulator, proposed_emulator
):
    """Only an ONLINE poll from the proposed endpoint verifies its repair."""
    entry = await _loaded_entry(hass, emulator)
    old_runtime = entry.runtime_data
    _create_connection_issue(hass, entry)
    poll_task = None
    issue_seen_during_unload = []
    unload_platforms = hass.config_entries.async_unload_platforms

    async def probe(**kwargs):
        nonlocal poll_task
        await validate_connection(**kwargs)
        poll_task = asyncio.create_task(old_runtime._async_update_data())
        await asyncio.sleep(0)
        assert not poll_task.done()

    async def unload(current, platforms):
        issue = _connection_issue(hass, entry)
        assert issue.data["verification_pending"] == 1
        snapshot = await asyncio.wait_for(poll_task, 5)
        assert snapshot.state is EngineState.ONLINE
        assert entry.runtime_data is old_runtime
        issue_seen_during_unload.append(_connection_issue(hass, entry) is not None)
        return await unload_platforms(current, platforms)

    try:
        with (
            patch(
                "custom_components.solarmax.repairs.validate_connection",
                side_effect=probe,
            ),
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                side_effect=unload,
            ),
        ):
            host, port = proposed_emulator.addr
            result = await _submit_repair(hass, entry, host=host, port=port)

        assert result["type"] is FlowResultType.ABORT
        assert issue_seen_during_unload == [True]
        assert entry.runtime_data is not old_runtime
        assert entry.runtime_data.data.state is EngineState.ONLINE
        assert _connection_issue(hass, entry) is None
    finally:
        if poll_task is not None:
            await asyncio.gather(poll_task, return_exceptions=True)
        await hass.config_entries.async_unload(entry.entry_id)


async def test_repair_emulator_unreachable_endpoint_stays_open(
    hass, emulator, unused_tcp_port
):
    """A refused probe preserves the entry; only its recovered full poll clears."""
    entry = await _loaded_entry(hass, emulator)
    _create_connection_issue(hass, entry)
    old_runtime = entry.runtime_data
    try:
        result = await _submit_repair(
            hass, entry, host="127.0.0.1", port=unused_tcp_port
        )
        assert result["errors"] == {"base": "cannot_connect"}
        assert entry.data["port"] == emulator.addr[1]
        assert entry.runtime_data is old_runtime
        assert _connection_issue(hass, entry) is not None
        await entry.runtime_data.async_refresh()
        assert entry.runtime_data.data.state is EngineState.ONLINE
        assert _connection_issue(hass, entry) is None
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_repair_emulator_activation_rollback_full_poll_clears(
    hass, emulator, proposed_emulator
):
    """Rollback's successful full poll verifies recovery and must stay cleared."""
    entry = await _loaded_entry(hass, emulator)
    _create_connection_issue(hass, entry)
    before_restore, release_restore = asyncio.Event(), asyncio.Event()
    host, port = proposed_emulator.addr

    async def setup(hass, entry):
        if entry.data["port"] == port:
            return False
        before_restore.set()
        await release_restore.wait()
        return await async_setup_entry(hass, entry)

    try:
        with patch("custom_components.solarmax.async_setup_entry", side_effect=setup):
            task = asyncio.create_task(
                _submit_repair(hass, entry, host=host, port=port)
            )
            await asyncio.wait_for(before_restore.wait(), 5)
            assert _connection_issue(hass, entry).data["verification_pending"] == 1
            release_restore.set()
            result = await asyncio.wait_for(task, 5)
        assert result["errors"] == {"base": "reload_failed"}
        assert entry.data["port"] == emulator.addr[1]
        assert entry.runtime_data.data.state is EngineState.ONLINE
        assert _connection_issue(hass, entry) is None
    finally:
        release_restore.set()
        await hass.config_entries.async_unload(entry.entry_id)


async def test_disabled_repair_emulator_waits_until_enabled(hass, emulator):
    """A disabled repaired entry stays pending until enabled and polled."""
    host, port = emulator.addr
    entry = _configured_endpoint_entry(host="192.0.2.10", port=12345, address=1)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "disabled_by", ConfigEntryDisabler.USER)
    _create_connection_issue(hass, entry)
    result = await _submit_repair(hass, entry, host=host, port=port)
    assert result["reason"] == "repair_pending_verification"
    assert entry.disabled_by is ConfigEntryDisabler.USER
    assert getattr(entry, "runtime_data", None) is None
    assert _connection_issue(hass, entry).data["verification_pending"] == 1
    object.__setattr__(entry, "disabled_by", None)
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert entry.runtime_data.data.state is EngineState.ONLINE
        assert _connection_issue(hass, entry) is None
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
