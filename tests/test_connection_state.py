"""Pure state-machine tests — no sockets, no HA."""

from custom_components.solarmax.connection import ArmingTracker


def _reading(value, raw=None):
    return {"value": value, "raw_value": raw if raw is not None else value}


def test_sys_20002_arms():
    tracker = ArmingTracker()
    tracker.observe({"SYS": _reading(20002), "PDC": _reading(800.0, 1600)})
    assert tracker.armed is True


def test_low_pdc_arms_even_with_healthy_sys():
    """The 20002 window (30s-2min) can slip between polls; PDC is the net."""
    tracker = ArmingTracker()
    tracker.observe({"SYS": _reading(20008), "PDC": _reading(1.5, 3)})
    assert tracker.armed is True


def test_pdc_threshold_is_strictly_below_25():
    tracker = ArmingTracker()
    tracker.observe({"PDC": _reading(25.0, 50)})
    assert tracker.armed is False
    tracker.observe({"PDC": _reading(24.5, 49)})
    assert tracker.armed is True


def test_healthy_poll_disarms():
    """Single-poll semantics: arming reflects the LAST successful poll."""
    tracker = ArmingTracker()
    tracker.observe({"SYS": _reading(20002), "PDC": _reading(1.5, 3)})
    tracker.observe({"SYS": _reading(20008), "PDC": _reading(800.0, 1600)})
    assert tracker.armed is False


def test_partial_frame_without_evidence_keeps_arming():
    """A dying frame missing SYS and PDC is not proof of recovery."""
    tracker = ArmingTracker()
    tracker.observe({"SYS": _reading(20002)})
    tracker.observe({"KDY": _reading(19)})
    assert tracker.armed is True
