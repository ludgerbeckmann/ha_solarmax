"""Test the Solarmax sensor functionality."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest
from freezegun import freeze_time
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from custom_components.solarmax.connection import EngineSnapshot, EngineState
from custom_components.solarmax.const import CONF_NIGHT_KEEP_VALUES, SENSOR_TYPES
from custom_components.solarmax.coordinator import SolarmaxCoordinator
from custom_components.solarmax.sensor import SolarmaxSensor

_SENSOR_BY_KEY = {description.key: description for description in SENSOR_TYPES}


def _make_snapshot(
    state: EngineState = EngineState.ONLINE,
    values: dict | None = None,
    *,
    reconnecting: bool = False,
    expected_outside_twilight: bool = False,
    fault_since=None,
) -> EngineSnapshot:
    """Build an EngineSnapshot for tests, defaulting to a healthy online poll."""
    return EngineSnapshot(
        state=state,
        values=values if values is not None else {},
        shutdown_announced=False,
        reconnecting=reconnecting,
        expected_outside_twilight=expected_outside_twilight,
        fault_since=fault_since,
        diagnostics={},
    )


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    coordinator = Mock(spec=SolarmaxCoordinator)
    coordinator.data = _make_snapshot(
        values={
            "SYS": {"value": 20004, "raw_value": 20004},
            "PAC": {"value": 1500.0, "raw_value": 3000},
        }
    )
    coordinator.last_successful_update = None
    # Plain Mock: hass.config/hass.states are instance attributes and thus
    # not visible to a spec'd Mock
    coordinator.hass = Mock()
    coordinator.hass.config.language = "en"
    return coordinator


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    entry = Mock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.data = {
        "host": "192.168.1.100",
        "port": 12345,
        "device_name": "Test Inverter",
    }
    entry.options = {}
    return entry


def _make_sensor(coordinator, entry, sensor_key):
    return SolarmaxSensor(
        coordinator,
        entry,
        _SENSOR_BY_KEY[sensor_key],
        "Test Inverter",
    )


def _set_state(coordinator, state: EngineState) -> None:
    coordinator.data = replace(coordinator.data, state=state)


def _set_night(coordinator) -> None:
    _set_state(coordinator, EngineState.OFFLINE_EXPECTED)


def _set_fault(coordinator) -> None:
    _set_state(coordinator, EngineState.OFFLINE_FAULT)


def test_sensor_available_when_online(mock_coordinator, mock_config_entry):
    """Test sensor is available while the engine reports ONLINE."""
    sensor = _make_sensor(mock_coordinator, mock_config_entry, "PAC")

    assert sensor.available is True


def test_runtime_option_overrides_legacy_night_preference(
    mock_coordinator, mock_config_entry
):
    """A migrated option must take precedence over legacy entry data."""
    mock_config_entry.data[CONF_NIGHT_KEEP_VALUES] = False
    mock_config_entry.options[CONF_NIGHT_KEEP_VALUES] = True
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "PAC")

    assert sensor.native_value == 0


def test_sys_sensor_available_when_offline_fault(mock_coordinator, mock_config_entry):
    """Test SYS sensor remains available during an OFFLINE_FAULT."""
    _set_fault(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "SYS")

    assert sensor.available is True


def test_other_sensor_unavailable_when_offline_fault(
    mock_coordinator, mock_config_entry
):
    """A daytime fault makes normal sensors unavailable immediately."""
    _set_fault(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "PAC")

    assert sensor.available is False


def test_sys_sensor_shows_offline_fault_during_daytime_outage(
    mock_coordinator, mock_config_entry
):
    """Test SYS sensor shows the new offline_fault key on OFFLINE_FAULT."""
    _set_fault(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "SYS")

    assert sensor.native_value == "offline_fault"


def test_sys_sensor_shows_offline_expected_when_expected_offline(
    mock_coordinator, mock_config_entry
):
    """Test SYS sensor shows the new offline_expected key on OFFLINE_EXPECTED."""
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "SYS")

    assert sensor.native_value == "offline_expected"


def test_sys_sensor_shows_unknown_state(mock_coordinator, mock_config_entry):
    """Test SYS sensor shows unknown for EngineState.UNKNOWN."""
    _set_state(mock_coordinator, EngineState.UNKNOWN)

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "SYS")

    assert sensor.native_value == "unknown"


def test_sys_sensor_offline_attributes(mock_coordinator, mock_config_entry):
    """Test SYS sensor shows offline attributes when the engine is offline.

    A real OFFLINE_FAULT snapshot always carries a fault_since (the engine
    sets it on the same branch that assigns the state), so that is the
    realistic pairing to test here rather than a bare OFFLINE_FAULT.
    """
    fault_since = dt_util.utcnow() - timedelta(minutes=5)
    mock_coordinator.data = _make_snapshot(
        state=EngineState.OFFLINE_FAULT, fault_since=fault_since
    )
    mock_coordinator.last_successful_update = dt_util.now()

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "SYS")

    attributes = sensor.extra_state_attributes
    assert attributes["raw_value"] == "offline"
    assert attributes["code"] == "offline"
    assert attributes["fault_since"] == fault_since.isoformat()
    assert "last_successful_update" in attributes
    assert "consecutive_failures" not in attributes
    assert "reconnecting" not in attributes
    assert "expected_outside_twilight" not in attributes


def test_status_reports_reconnecting_attribute(mock_coordinator, mock_config_entry):
    """The status sensor surfaces the engine's reconnecting flag while offline.

    UNKNOWN-with-reconnecting is the startup grace window, which precedes
    any fault ever being declared, so fault_since is genuinely absent here.
    """
    mock_coordinator.data = _make_snapshot(state=EngineState.UNKNOWN, reconnecting=True)

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "SYS")
    attributes = sensor.extra_state_attributes

    assert sensor.native_value == "unknown"
    assert attributes["reconnecting"] is True
    assert "fault_since" not in attributes


def test_status_reports_outside_twilight_anomaly(mock_coordinator, mock_config_entry):
    """A shutdown-armed offline outside the twilight window is surfaced."""
    mock_coordinator.data = _make_snapshot(
        state=EngineState.OFFLINE_EXPECTED, expected_outside_twilight=True
    )

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "SYS")
    attributes = sensor.extra_state_attributes

    assert sensor.native_value == "offline_expected"
    assert attributes["expected_outside_twilight"] is True


def test_normal_sensor_operation(mock_coordinator, mock_config_entry):
    """Test normal sensor operation when the engine is ONLINE."""
    sensor = _make_sensor(mock_coordinator, mock_config_entry, "SYS")

    # Should show enum option key (HA handles translation)
    assert sensor.native_value == "mpp_operation"

    # Should show normal attributes
    attributes = sensor.extra_state_attributes
    assert attributes["raw_value"] == 20004
    assert attributes["code"] == 20004


def test_description_metadata_flows_through(mock_coordinator, mock_config_entry):
    """A main sensor exposes its description's unit/class/state metadata."""
    sensor = _make_sensor(mock_coordinator, mock_config_entry, "PAC")

    assert sensor.native_unit_of_measurement == "W"
    assert sensor.device_class == SensorDeviceClass.POWER
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.translation_key == "pac"
    assert sensor.entity_registry_enabled_default is True
    assert sensor.entity_category is None


def test_diagnostic_sensor_disabled_by_default(mock_coordinator, mock_config_entry):
    """A diagnostic sensor is opt-in and categorized as diagnostic."""
    sensor = _make_sensor(mock_coordinator, mock_config_entry, "UL1")

    assert sensor.entity_registry_enabled_default is False
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC


def test_unique_id_and_entity_id_scheme(mock_coordinator, mock_config_entry):
    """unique_id and forced entity_id keep the pre-migration scheme."""
    sensor = _make_sensor(mock_coordinator, mock_config_entry, "PAC")

    assert sensor.unique_id == "test_entry_id-pac"
    assert sensor.entity_id == "sensor.test_inverter_pac"


def test_enum_sensor_description(mock_coordinator, mock_config_entry):
    """The status sensor is an enum with options sourced from the description."""
    sensor = _make_sensor(mock_coordinator, mock_config_entry, "SYS")

    assert sensor.device_class == SensorDeviceClass.ENUM
    assert sensor.options
    assert "mpp_operation" in sensor.options
    assert "offline_expected" in sensor.options
    assert "offline_fault" in sensor.options


def test_no_invalid_device_state_class_combinations():
    """Guard against device and state class combinations HA rejects."""
    valid_energy_state_classes = {
        None,
        SensorStateClass.TOTAL,
        SensorStateClass.TOTAL_INCREASING,
    }
    for description in SENSOR_TYPES:
        if description.device_class == SensorDeviceClass.ENERGY:
            assert description.state_class in valid_energy_state_classes, (
                f"{description.key}: invalid energy state_class "
                f"{description.state_class}"
            )
        if description.device_class == SensorDeviceClass.ENUM:
            assert description.state_class is None, f"{description.key}: enum + state"
            assert description.native_unit_of_measurement is None, (
                f"{description.key}: enum sensors must not have a unit"
            )


@pytest.fixture
def night_entry(mock_config_entry):
    """A config entry with the night-keep-values option enabled."""
    mock_config_entry.data = {**mock_config_entry.data, "night_keep_values": True}
    return mock_config_entry


def test_zero_policy_sensor_reads_zero_at_night(mock_coordinator, night_entry):
    """PAC is available and reads 0 at night when the option is on."""
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "PAC")

    assert sensor.available is True
    assert sensor.native_value == 0


def test_zero_policy_sensor_is_unavailable_without_prior_data(
    mock_coordinator, night_entry
):
    """A never-reported register must not become a synthetic zero."""
    mock_coordinator.data = _make_snapshot(values={})
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "PAC")

    assert sensor.available is False
    assert sensor.native_value is None


def test_hold_policy_sensor_keeps_last_value_at_night(mock_coordinator, night_entry):
    """KT0 holds the last successful reading rather than going unavailable."""
    mock_coordinator.data.values["KT0"] = {"value": 12345, "raw_value": 12345}
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "KT0")

    assert sensor.available is True
    assert sensor.native_value == 12345


def test_hold_policy_sensor_survives_midnight(mock_coordinator, night_entry):
    """Only KDY resets at midnight; lifetime totals keep holding."""
    mock_coordinator.data.values["KT0"] = {"value": 12345, "raw_value": 12345}
    mock_coordinator.last_successful_update = dt_util.now() - timedelta(days=1)
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "KT0")

    assert sensor.native_value == 12345


def test_hold_policy_sensor_unavailable_with_nothing_to_hold(
    mock_coordinator, night_entry
):
    """An available sensor reporting `unknown` is worse than an absent one.

    Happens for real when the inverter model does not support the key, so its
    value never appears in the snapshot's values.
    """
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "KLM")

    assert sensor.available is False


def test_unavailable_policy_sensor_still_unavailable_at_night(
    mock_coordinator, night_entry
):
    """AC grid voltage has no honest night value, so it stays unavailable."""
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "UL1")

    assert sensor.available is False


def test_night_policy_ignored_during_daytime_outage(mock_coordinator, night_entry):
    """A daytime fault is a real fault and must not be smoothed over."""
    _set_fault(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "PAC")

    assert sensor.available is False


def test_night_policy_ignored_when_option_disabled(mock_coordinator, mock_config_entry):
    """Default-off installs keep the original behaviour exactly."""
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, mock_config_entry, "PAC")

    assert sensor.available is False


def test_kdy_holds_before_midnight(mock_coordinator, night_entry):
    """Same local day as the last poll: the day's total still stands."""
    with freeze_time("2026-01-01 12:00:00"):
        mock_coordinator.data.values["KDY"] = {"value": 24.5, "raw_value": 245}
        mock_coordinator.last_successful_update = dt_util.now()
        _set_night(mock_coordinator)

        sensor = _make_sensor(mock_coordinator, night_entry, "KDY")

        assert sensor.available is True
        assert sensor.native_value == 24.5


def test_kdy_reads_zero_after_midnight(mock_coordinator, night_entry):
    """Last poll was an earlier local day: today's total is 0."""
    mock_coordinator.data.values["KDY"] = {"value": 24.5, "raw_value": 245}
    mock_coordinator.last_successful_update = dt_util.now() - timedelta(days=1)
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "KDY")

    assert sensor.available is True
    assert sensor.native_value == 0


def test_kdy_after_midnight_is_unavailable_without_prior_data(
    mock_coordinator, night_entry
):
    """A timestamp alone does not prove that the inverter supports KDY."""
    mock_coordinator.data = _make_snapshot(values={})
    mock_coordinator.last_successful_update = dt_util.now() - timedelta(days=1)
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "KDY")

    assert sensor.available is False
    assert sensor.native_value is None


def test_kdy_unavailable_when_never_polled(mock_coordinator, night_entry):
    """No successful poll ever: nothing to hold and no day boundary crossed."""
    mock_coordinator.data = _make_snapshot(values={})
    mock_coordinator.last_successful_update = None
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "KDY")

    assert sensor.available is False


def test_night_value_source_reports_zero(mock_coordinator, night_entry):
    """A synthesised zero says so, and does not advertise a stale raw_value."""
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "PAC")
    attributes = sensor.extra_state_attributes

    assert attributes["night_value_source"] == "zero"
    assert attributes["raw_value"] == 0


def test_night_value_source_reports_unavailable_without_prior_data(
    mock_coordinator, night_entry
):
    """Expose why a never-reported night sensor has no value."""
    mock_coordinator.data = _make_snapshot(values={})
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "PAC")

    assert sensor.extra_state_attributes["night_value_source"] == "unavailable"


def test_night_value_source_reports_hold(mock_coordinator, night_entry):
    """A held value keeps its original raw_value."""
    mock_coordinator.data.values["KT0"] = {"value": 12345, "raw_value": 12345}
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "KT0")
    attributes = sensor.extra_state_attributes

    assert attributes["night_value_source"] == "hold"
    assert attributes["raw_value"] == 12345


def test_held_alarm_sensor_keeps_decoded_attributes(mock_coordinator, night_entry):
    """SAL holds so a dusk alarm stays legible — decoding must survive."""
    mock_coordinator.data.values["SAL"] = {"value": 6, "raw_value": 6}
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "SAL")
    attributes = sensor.extra_state_attributes

    assert attributes["night_value_source"] == "hold"
    assert attributes["code"] == 6
    assert attributes["active_alarms"] == [
        "insulation_fault_dc",
        "earth_fault_current",
    ]


def test_night_value_source_absent_during_normal_operation(
    mock_coordinator, night_entry
):
    """Absence means the reading is real — automations test for presence."""
    sensor = _make_sensor(mock_coordinator, night_entry, "PAC")

    assert "night_value_source" not in (sensor.extra_state_attributes or {})


def test_kdy_after_midnight_reports_zero_source_and_raw(mock_coordinator, night_entry):
    """KDY's midnight zero is synthetic too — it must not show a stale raw_value."""
    mock_coordinator.data.values["KDY"] = {"value": 24.5, "raw_value": 245}
    mock_coordinator.last_successful_update = dt_util.now() - timedelta(days=1)
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "KDY")
    attributes = sensor.extra_state_attributes

    assert attributes["night_value_source"] == "zero"
    assert attributes["raw_value"] == 0


# --- Anomalous expected outage outside twilight -----------------------------


def test_zero_policy_sensor_unavailable_when_anomalous_expected(
    mock_coordinator, night_entry
):
    """A ZERO-policy sensor (PAC) must not fabricate a 0 when the armed
    disconnect happened outside twilight — that is an anomaly (e.g.
    shading), not a normal dusk, so there is no honest zero to report."""
    mock_coordinator.data = _make_snapshot(
        state=EngineState.OFFLINE_EXPECTED, expected_outside_twilight=True
    )

    sensor = _make_sensor(mock_coordinator, night_entry, "PAC")

    assert sensor.available is False
    assert sensor.native_value is None


def test_hold_policy_sensor_still_holds_when_anomalous_expected(
    mock_coordinator, night_entry
):
    """A HOLD-policy sensor (KT0) keeps holding its last-known value even
    when the disconnect is anomalous — only ZERO-policy sensors are gated."""
    mock_coordinator.data = _make_snapshot(
        state=EngineState.OFFLINE_EXPECTED,
        values={"KT0": {"value": 12345, "raw_value": 12345}},
        expected_outside_twilight=True,
    )

    sensor = _make_sensor(mock_coordinator, night_entry, "KT0")

    assert sensor.available is True
    assert sensor.native_value == 12345


def test_normal_dusk_zero_policy_sensor_still_reads_zero(mock_coordinator, night_entry):
    """Sanity: a normal (twilight) EXPECTED is unaffected — PAC still reads
    0 exactly as before (the default `expected_outside_twilight=False`)."""
    _set_night(mock_coordinator)

    sensor = _make_sensor(mock_coordinator, night_entry, "PAC")

    assert sensor.available is True
    assert sensor.native_value == 0


def test_anomalous_expected_night_value_source_is_unavailable(
    mock_coordinator, night_entry
):
    """The night_value_source attribute reports the gated state honestly."""
    mock_coordinator.data = _make_snapshot(
        state=EngineState.OFFLINE_EXPECTED, expected_outside_twilight=True
    )

    sensor = _make_sensor(mock_coordinator, night_entry, "PAC")

    assert sensor.extra_state_attributes["night_value_source"] == "unavailable"
