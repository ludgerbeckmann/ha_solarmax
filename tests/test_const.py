"""Test the Solarmax constants and night-policy table."""

from homeassistant.components.sensor import SensorStateClass

from custom_components.solarmax.const import (
    NIGHT_POLICY,
    SENSOR_TYPES,
    NightPolicy,
)

_SENSOR_BY_KEY = {description.key: description for description in SENSOR_TYPES}


def test_night_policy_keys_all_exist_as_sensors():
    """Every policy entry must name a real sensor."""
    assert set(NIGHT_POLICY) <= set(_SENSOR_BY_KEY)


def test_zero_policy_never_zeroes_a_cumulative_counter():
    """Zeroing a TOTAL_INCREASING sensor reads as a meter reset to HA.

    This only covers the ZERO group. KDY is TOTAL_INCREASING and is zeroed
    too, via HOLD_UNTIL_MIDNIGHT — but that midnight zero mirrors the
    inverter's own daily counter reset, so HA reading it as a meter reset
    is correct, not an exception to the rule checked here.
    """
    for key, policy in NIGHT_POLICY.items():
        if policy is not NightPolicy.ZERO:
            continue
        state_class = _SENSOR_BY_KEY[key].state_class
        assert state_class is not SensorStateClass.TOTAL_INCREASING, (
            f"{key} would be zeroed but is TOTAL_INCREASING"
        )


def test_night_policy_group_membership():
    """Pin the ratified mapping so a future edit is a deliberate act."""
    zero = {k for k, v in NIGHT_POLICY.items() if v is NightPolicy.ZERO}
    hold = {k for k, v in NIGHT_POLICY.items() if v is NightPolicy.HOLD}
    midnight = {
        k for k, v in NIGHT_POLICY.items() if v is NightPolicy.HOLD_UNTIL_MIDNIGHT
    }

    assert zero == {
        "PAC",
        "PDC",
        "PD01",
        "PD02",
        "PD03",
        "PRL",
        "IDC",
        "ID01",
        "ID02",
        "ID03",
        "IL1",
        "IL2",
        "IL3",
        "UDC",
        "UD01",
        "UD02",
        "UD03",
    }
    assert hold == {
        "KMT",
        "KYR",
        "KT0",
        "KHR",
        "CAC",
        "KLD",
        "KLM",
        "KLY",
        "PIN",
        "ULH",
        "ULL",
        "TNH",
        "TNL",
        "SAL",
    }
    assert midnight == {"KDY"}


def test_ac_side_and_thermal_sensors_stay_unavailable():
    """Grid voltage/frequency and temperatures have no honest night value."""
    for key in ("UL1", "UL2", "UL3", "TNF", "TKK", "TK2", "TK3", "SYS"):
        assert NIGHT_POLICY.get(key, NightPolicy.UNAVAILABLE) is (
            NightPolicy.UNAVAILABLE
        )
