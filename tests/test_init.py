"""Test the Solarmax integration initialization."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmax import (
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.solarmax.const import (
    CONF_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_HOST,
    CONF_NIGHT_KEEP_VALUES,
    CONF_PORT,
    CONF_TWILIGHT_ELEVATION_THRESHOLD,
    CONF_UPDATE_INTERVAL,
    CONF_VERIFY_CHECKSUM,
    DEFAULT_ADDRESS,
    DEFAULT_TWILIGHT_ELEVATION_THRESHOLD,
    DOMAIN,
)
from custom_components.solarmax.coordinator import SolarmaxCoordinator
from custom_components.solarmax.sensor import _make_device_registry_updater


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Inverter",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
            CONF_DEVICE_NAME: "Test Inverter",
            CONF_UPDATE_INTERVAL: 30,
        },
        entry_id="test_entry",
        unique_id="192.168.1.100:12345",
    )


def _legacy_entry(
    *,
    version: int = 1,
    minor_version: int = 1,
    data_update: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    data = {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 12345,
        CONF_DEVICE_NAME: "Roof",
        CONF_UPDATE_INTERVAL: 30,
    }
    data.update(data_update or {})
    return MockConfigEntry(
        domain=DOMAIN,
        version=version,
        minor_version=minor_version,
        unique_id="192.0.2.10:12345",
        data=data,
        options=options or {},
    )


async def test_migrate_v1_splits_connection_data_and_options(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=1,
        unique_id="192.0.2.10:12345",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 12345,
            CONF_DEVICE_NAME: "Roof",
            CONF_UPDATE_INTERVAL: 45,
            CONF_VERIFY_CHECKSUM: False,
            CONF_NIGHT_KEEP_VALUES: True,
        },
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert dict(entry.data) == {
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 12345,
        CONF_ADDRESS: DEFAULT_ADDRESS,
        CONF_DEVICE_NAME: "Roof",
    }
    assert dict(entry.options) == {
        CONF_UPDATE_INTERVAL: 45,
        CONF_VERIFY_CHECKSUM: False,
        CONF_TWILIGHT_ELEVATION_THRESHOLD: DEFAULT_TWILIGHT_ELEVATION_THRESHOLD,
        CONF_NIGHT_KEEP_VALUES: True,
    }
    assert entry.unique_id == "192.0.2.10:12345:1"
    assert (entry.version, entry.minor_version) == (2, 1)


async def test_migrate_v1_keeps_existing_option_value(hass):
    entry = _legacy_entry(
        data_update={CONF_UPDATE_INTERVAL: 30},
        options={CONF_UPDATE_INTERVAL: 90},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.options[CONF_UPDATE_INTERVAL] == 90


async def test_migrate_future_major_version_is_rejected(hass):
    entry = _legacy_entry(version=3)
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is False
    assert entry.version == 3


async def test_migrate_future_minor_version_is_rejected(hass):
    entry = _legacy_entry(version=2, minor_version=2)
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is False
    assert (entry.version, entry.minor_version) == (2, 2)


@patch("custom_components.solarmax.SolarmaxCoordinator")
async def test_setup_entry_success(
    mock_coordinator_class, hass: HomeAssistant, mock_config_entry
):
    """Test successful setup of config entry."""
    mock_coordinator = MagicMock()
    mock_coordinator.async_config_entry_first_refresh = AsyncMock()
    mock_coordinator_class.return_value = mock_coordinator

    with patch.object(
        hass.config_entries, "async_forward_entry_setups"
    ) as mock_forward:
        result = await async_setup_entry(hass, mock_config_entry)

        assert result is True
        assert mock_config_entry.runtime_data == mock_coordinator
        mock_coordinator.async_config_entry_first_refresh.assert_called_once()
        mock_forward.assert_called_once_with(mock_config_entry, [Platform.SENSOR])


@patch("custom_components.solarmax.SolarmaxCoordinator")
async def test_entry_update_does_not_reload_implicitly(
    mock_coordinator_class, hass, mock_config_entry
):
    """Submitting flows own reloads, so a title update cannot trigger a second one."""
    mock_config_entry.add_to_hass(hass)
    mock_coordinator_class.return_value.async_config_entry_first_refresh = AsyncMock()
    with (
        patch.object(hass.config_entries, "async_forward_entry_setups"),
        patch.object(hass.config_entries, "async_reload") as reload,
    ):
        await async_setup_entry(hass, mock_config_entry)
        hass.config_entries.async_update_entry(mock_config_entry, title="Garage")
        await hass.async_block_till_done()
    assert mock_config_entry.title == "Garage"
    reload.assert_not_awaited()


@patch("custom_components.solarmax.async_track_time_change")
@patch("custom_components.solarmax.SolarmaxCoordinator")
async def test_setup_entry_registers_midnight_listener_when_night_keep_values_enabled(
    mock_coordinator_class, mock_track_time_change, hass: HomeAssistant
):
    """night_keep_values=True must register the local-midnight callback."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Inverter",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 12345,
            CONF_DEVICE_NAME: "Test Inverter",
            CONF_UPDATE_INTERVAL: 30,
            CONF_NIGHT_KEEP_VALUES: True,
        },
        entry_id="test_entry_night_keep_values",
        unique_id="192.168.1.100:12345:night",
    )
    mock_coordinator = MagicMock()
    mock_coordinator.async_config_entry_first_refresh = AsyncMock()
    mock_coordinator_class.return_value = mock_coordinator

    with patch.object(hass.config_entries, "async_forward_entry_setups"):
        await async_setup_entry(hass, entry)

    mock_track_time_change.assert_called_once_with(
        hass, mock_coordinator.async_handle_midnight, hour=0, minute=0, second=0
    )


@patch("custom_components.solarmax.async_track_time_change")
@patch("custom_components.solarmax.SolarmaxCoordinator")
async def test_failed_setup_does_not_register_midnight_listener(
    mock_coordinator_class, mock_track_time_change, hass: HomeAssistant
):
    """A failed platform setup must not leave a midnight callback behind."""
    entry = _legacy_entry(data_update={CONF_NIGHT_KEEP_VALUES: True})
    mock_coordinator = MagicMock()
    mock_coordinator.async_config_entry_first_refresh = AsyncMock()
    mock_coordinator.async_shutdown = AsyncMock()
    mock_coordinator.engine.close = AsyncMock()
    mock_coordinator_class.return_value = mock_coordinator

    with (
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=RuntimeError("platform setup failed"),
        ),
        pytest.raises(RuntimeError, match="platform setup failed"),
    ):
        await async_setup_entry(hass, entry)

    mock_track_time_change.assert_not_called()


@patch("custom_components.solarmax.async_track_time_change")
@patch("custom_components.solarmax.SolarmaxCoordinator")
async def test_setup_entry_skips_midnight_listener_by_default(
    mock_coordinator_class,
    mock_track_time_change,
    hass: HomeAssistant,
    mock_config_entry,
):
    """night_keep_values absent (default False) must not register the callback."""
    mock_coordinator = MagicMock()
    mock_coordinator.async_config_entry_first_refresh = AsyncMock()
    mock_coordinator_class.return_value = mock_coordinator

    with patch.object(hass.config_entries, "async_forward_entry_setups"):
        await async_setup_entry(hass, mock_config_entry)

    mock_track_time_change.assert_not_called()


async def test_unload_entry_success(hass: HomeAssistant, mock_config_entry):
    """Test successful unload of config entry: engine closes, platforms unload."""
    mock_coordinator = MagicMock()
    mock_coordinator.engine.close = AsyncMock()
    mock_config_entry.runtime_data = mock_coordinator

    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=True
    ) as mock_unload:
        result = await async_unload_entry(hass, mock_config_entry)

        assert result is True
        mock_coordinator.engine.close.assert_awaited_once()
        mock_unload.assert_called_once_with(mock_config_entry, [Platform.SENSOR])


async def test_unload_entry_failed(hass: HomeAssistant, mock_config_entry):
    """A failed platform unload leaves the still-loaded engine usable."""
    mock_coordinator = MagicMock()
    mock_coordinator.engine.close = AsyncMock()
    mock_config_entry.runtime_data = mock_coordinator

    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ) as mock_unload:
        result = await async_unload_entry(hass, mock_config_entry)

        assert result is False
        mock_coordinator.engine.close.assert_not_awaited()
        mock_unload.assert_called_once_with(mock_config_entry, [Platform.SENSOR])


async def test_unload_closes_engine_after_platform_teardown(
    hass: HomeAssistant, mock_config_entry
):
    """Terminal close happens only after platform teardown succeeds."""
    call_order: list[str] = []

    mock_coordinator = MagicMock()

    async def _close() -> None:
        call_order.append("engine_close")

    mock_coordinator.engine.close = AsyncMock(side_effect=_close)
    mock_config_entry.runtime_data = mock_coordinator

    async def _unload_platforms(*args, **kwargs):
        call_order.append("platform_teardown")
        return True

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        side_effect=_unload_platforms,
    ):
        await async_unload_entry(hass, mock_config_entry)

    assert call_order == ["platform_teardown", "engine_close"]


async def test_setup_while_dark_creates_entities(hass, emulator):
    """Spec criterion 3: restart at night -> entities exist, OFFLINE_EXPECTED.

    The plan's only true end-to-end test: real coordinator + real engine +
    real entities over the emulator — the composition seam nothing else
    covers. Setup against a dark inverter must NOT raise ConfigEntryNotReady.
    """
    emulator.dark = True
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "127.0.0.1",
            "port": emulator.addr[1],
            "device_name": "E2E Inverter",
            "update_interval": 30,
        },
        unique_id="e2e",
    )
    entry.add_to_hass(hass)
    with patch.object(SolarmaxCoordinator, "sun_below_threshold", return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        status = hass.states.get("sensor.e2e_inverter_sys")
        assert status is not None
        assert status.state == "offline_expected"


def test_device_registry_updater_uses_config_entry_lookup(
    hass, mock_config_entry
) -> None:
    """Newer HA registries receive the config entry needed for disambiguation."""

    class FutureDeviceRegistry:
        def __init__(self) -> None:
            self.lookup = None
            self.updated = None

        def async_get_device_by_identifier(self, identifier, config_entry_id):
            self.lookup = (identifier, config_entry_id)
            return SimpleNamespace(id="device-id")

        def async_get_device(self, **_kwargs):
            raise AssertionError("deprecated registry lookup used")

        def async_update_device(self, device_id, **changes):
            self.updated = (device_id, changes)

    registry = FutureDeviceRegistry()
    coordinator = SimpleNamespace(
        device_model="SolarMax 7TP2",
        sw_version="40",
        serial_number="118767",
    )

    with patch("custom_components.solarmax.sensor.dr.async_get", return_value=registry):
        updater = _make_device_registry_updater(hass, mock_config_entry, coordinator)
        updater()

    assert registry.lookup == (
        (DOMAIN, mock_config_entry.entry_id),
        mock_config_entry.entry_id,
    )
    assert registry.updated == (
        "device-id",
        {
            "model": "SolarMax 7TP2",
            "sw_version": "40",
            "serial_number": "118767",
        },
    )


@pytest.mark.parametrize(
    ("sw_version", "serial_number", "expected_metadata"),
    [
        (None, None, {"model": "SolarMax 7TP2"}),
        ("40", None, {"model": "SolarMax 7TP2", "sw_version": "40"}),
        (
            None,
            "118767",
            {"model": "SolarMax 7TP2", "serial_number": "118767"},
        ),
    ],
)
def test_device_registry_updater_omits_unreported_metadata(
    hass,
    mock_config_entry,
    sw_version,
    serial_number,
    expected_metadata,
) -> None:
    """Partial static data must not clear existing device metadata."""

    class RecordingDeviceRegistry:
        def __init__(self) -> None:
            self.updated = None

        def async_get_device_by_identifier(self, *_args):
            return SimpleNamespace(id="device-id")

        def async_update_device(self, device_id, **changes):
            self.updated = (device_id, changes)

    registry = RecordingDeviceRegistry()
    coordinator = SimpleNamespace(
        device_model="SolarMax 7TP2",
        sw_version=sw_version,
        serial_number=serial_number,
    )

    with patch("custom_components.solarmax.sensor.dr.async_get", return_value=registry):
        updater = _make_device_registry_updater(hass, mock_config_entry, coordinator)
        updater()

    assert registry.updated == ("device-id", expected_metadata)


async def test_device_registry_updates_when_statics_arrive_later(
    hass, emulator, monkeypatch
):
    """Static device data should replace the setup-time placeholder."""
    from homeassistant.helpers import device_registry as dr

    emulator.respond_only(["PAC", "PDC", "SYS", "SAL", "KDY"])  # withhold device info
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "127.0.0.1",
            "port": emulator.addr[1],
            "device_name": "E2E Inverter",
            "update_interval": 30,
        },
        unique_id="e2e-device-info",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = dr.async_get(hass)
    get_by_identifier = getattr(registry, "async_get_device_by_identifier", None)

    def get_device():
        if get_by_identifier is not None:
            return get_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
        return registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})

    device = get_device()
    assert device is not None
    assert device.model == "Inverter"  # placeholder: statics never arrived

    if get_by_identifier is not None:

        def fail_deprecated_lookup(*_args, **_kwargs):
            raise AssertionError("deprecated device lookup used")

        monkeypatch.setattr(
            registry,
            "async_get_device",
            fail_deprecated_lookup,
        )

    emulator.respond_only(None)  # statics now available
    coordinator: SolarmaxCoordinator = entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    device = get_device()
    assert device is not None
    assert device.model == "SolarMax 7TP2"

    # Unload before the emulator fixture tears down: an unclosed connection
    # would leave the emulator's client-handling thread alive past teardown.
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
