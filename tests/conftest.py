"""Test configuration for Solarmax integration."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solarmax.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations in all tests."""
    yield


@pytest.fixture
def mock_config_entry():
    """Return a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Solarmax",
        data={
            "host": "192.168.1.100",
            "port": 12345,
            "device_name": "Test Inverter",
            "update_interval": 30,
        },
        unique_id="test_inverter",
    )


@pytest.fixture
async def mock_solarmax_setup(hass: HomeAssistant):
    """Set up the Solarmax integration."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


@pytest.fixture
def emulator(socket_enabled):
    """A running MaxComm inverter emulator on an ephemeral port."""
    from tests.emulator import EmulatorHandle

    handle = EmulatorHandle()
    handle.start()
    yield handle
    handle.stop()
