"""Test diagnostics functionality."""

import json
import pathlib
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.solarmax.connection import EngineSnapshot, EngineState
from custom_components.solarmax.coordinator import SolarmaxCoordinator
from custom_components.solarmax.diagnostics import async_get_config_entry_diagnostics

MANIFEST = json.loads(
    (
        pathlib.Path(__file__).parent.parent
        / "custom_components"
        / "solarmax"
        / "manifest.json"
    ).read_text()
)


def _snapshot(**overrides) -> EngineSnapshot:
    """A real EngineSnapshot, matching the shape the engine actually produces."""
    defaults: dict = {
        "state": EngineState.ONLINE,
        "values": {
            "PAC": {"value": 1000, "raw_value": 1000},
            "PDC": {"value": 1050, "raw_value": 1050},
        },
        "shutdown_announced": False,
        "reconnecting": False,
        "expected_outside_twilight": False,
        "fault_since": None,
        "diagnostics": {
            "connection_attempts": 5,
            "reconnects": 1,
            "timeouts": 0,
            "polls_ok": 10,
            "last_successful_poll": datetime(2025, 9, 11, 10, 0, tzinfo=UTC),
            "last_shutdown_announcement": None,
            "transitions": [],
        },
    }
    defaults.update(overrides)
    return EngineSnapshot(**defaults)


def _mock_coordinator(**attrs) -> MagicMock:
    """A coordinator mock spec'd against the real class.

    `spec=SolarmaxCoordinator` makes an access to a removed attribute (e.g.
    the old `is_expected_offline`/`consecutive_failures`/`api`) raise
    AttributeError immediately, the way it would in production — this is
    what would have caught diagnostics.py's live AttributeError before it
    shipped.
    """
    coordinator = MagicMock(spec=SolarmaxCoordinator)
    coordinator.last_update_success = True
    coordinator.last_exception = None
    coordinator.update_interval.total_seconds.return_value = 30
    coordinator.device_model = "SolarMax 7TP2"
    coordinator.sun_source = "sun.sun"
    coordinator.last_successful_update = None
    coordinator.data = _snapshot()
    for key, value in attrs.items():
        setattr(coordinator, key, value)
    return coordinator


@pytest.mark.asyncio
async def test_config_entry_diagnostics(hass: HomeAssistant, mock_config_entry):
    """Test config entry diagnostics."""
    mock_coordinator = _mock_coordinator()
    mock_config_entry.runtime_data = mock_coordinator

    with patch.object(hass.config, "as_dict", return_value={"version": "2024.1.0"}):
        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Verify diagnostics structure
    assert "config_entry" in diagnostics
    assert "coordinator" in diagnostics
    assert "connection" in diagnostics
    assert "sensor_data" in diagnostics
    assert "system_info" in diagnostics
    assert "device_info" in diagnostics

    # Verify config entry data
    config_data = diagnostics["config_entry"]
    assert config_data["domain"] == "solarmax"
    assert config_data["title"] == "Test Solarmax"
    assert config_data["data"]["host"] == "**REDACTED**"
    assert "port" in config_data["data"]

    # Verify coordinator data (snapshot-derived fields)
    coordinator_data = diagnostics["coordinator"]
    assert coordinator_data["state"] == EngineState.ONLINE
    assert coordinator_data["reconnecting"] is False
    assert coordinator_data["fault_since"] is None
    assert coordinator_data["sun_source"] == "sun.sun"

    # Engine counters and transitions are exposed unchanged.
    connection_data = diagnostics["connection"]
    assert connection_data["connection_attempts"] == 5
    assert connection_data["reconnects"] == 1
    assert connection_data["timeouts"] == 0
    assert connection_data["transitions"] == []

    # Verify sensor data
    sensor_data = diagnostics["sensor_data"]
    assert "PAC" in sensor_data
    assert sensor_data["PAC"]["value"] == 1000
    assert "PDC" in sensor_data
    assert sensor_data["PDC"]["value"] == 1050

    # Verify system info
    system_info = diagnostics["system_info"]
    assert system_info["ha_version"] == "2024.1.0"
    # Compare against the manifest so this assertion never goes stale
    assert system_info["integration_version"] == MANIFEST["version"]

    # Verify device info uses JSON-serializable identifier tuples and the
    # detected model
    device_info = diagnostics["device_info"]
    assert device_info["identifiers"] == [("solarmax", mock_config_entry.entry_id)]
    assert device_info["model"] == "SolarMax 7TP2"


@pytest.mark.asyncio
async def test_diagnostics_with_no_data(hass: HomeAssistant, mock_config_entry):
    """Test diagnostics when the coordinator has not completed a poll yet."""
    mock_coordinator = _mock_coordinator(
        data=None,
        device_model=None,
    )
    mock_config_entry.runtime_data = mock_coordinator

    with patch.object(hass.config, "as_dict", return_value={"version": "2024.1.0"}):
        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Verify basic structure still exists
    assert "config_entry" in diagnostics
    assert "coordinator" in diagnostics
    assert "sensor_data" in diagnostics

    # Verify no-snapshot handling
    coordinator_data = diagnostics["coordinator"]
    assert coordinator_data["state"] is None
    assert coordinator_data["reconnecting"] is None
    assert coordinator_data["fault_since"] is None

    # Verify empty sensor/connection data
    assert diagnostics["sensor_data"] == {}
    assert diagnostics["connection"] == {}


@pytest.mark.asyncio
async def test_diagnostics_reports_fault_since_and_reconnecting(
    hass: HomeAssistant, mock_config_entry
):
    """A live fault must surface fault_since/reconnecting for support triage."""
    fault_since = datetime(2025, 9, 11, 9, 30, tzinfo=UTC)
    mock_coordinator = _mock_coordinator(
        data=_snapshot(
            state=EngineState.OFFLINE_FAULT,
            reconnecting=True,
            fault_since=fault_since,
        )
    )
    mock_config_entry.runtime_data = mock_coordinator

    with patch.object(hass.config, "as_dict", return_value={"version": "2024.1.0"}):
        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    coordinator_data = diagnostics["coordinator"]
    assert coordinator_data["state"] == EngineState.OFFLINE_FAULT
    assert coordinator_data["reconnecting"] is True
    assert coordinator_data["fault_since"] == fault_since.isoformat()


@pytest.mark.asyncio
async def test_diagnostics_redacts_sensitive_data(
    hass: HomeAssistant, mock_config_entry
):
    """Test that sensitive data is properly redacted."""
    mock_coordinator = _mock_coordinator(data=_snapshot(values={}))
    mock_config_entry.runtime_data = mock_coordinator

    with patch.object(hass.config, "as_dict", return_value={"version": "2024.1.0"}):
        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    # Verify sensitive data is redacted
    config_data = diagnostics["config_entry"]["data"]
    assert config_data["host"] == "**REDACTED**"
    assert config_data["port"] == 12345
    assert config_data["device_name"] == "Test Inverter"


@pytest.mark.asyncio
async def test_diagnostics_redacts_serial_number_in_sensor_data(
    hass: HomeAssistant, mock_config_entry
):
    """Diagnostics must redact the inverter serial number."""
    mock_coordinator = _mock_coordinator(
        data=_snapshot(
            values={
                "PAC": {"value": 1000, "raw_value": 1000},
                "DIN": {"value": 123456789, "raw_value": 123456789},
            }
        )
    )
    mock_config_entry.runtime_data = mock_coordinator

    with patch.object(hass.config, "as_dict", return_value={"version": "2024.1.0"}):
        diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)

    assert "123456789" not in str(diagnostics)  # no raw DIN value anywhere
    assert diagnostics["sensor_data"]["DIN"] == "**REDACTED**"
    assert diagnostics["sensor_data"]["PAC"]["value"] == 1000  # unrelated data intact
