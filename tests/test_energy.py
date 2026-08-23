from energy import RESET_CONFIRMATIONS, EnergyCounter


def test_first_reading_is_published_unchanged():
    c = EnergyCounter(title="Carport")
    assert c.value(17010.75) == 17010.75
    assert c.offset == 0.0


def test_counter_grows_with_the_source():
    c = EnergyCounter(title="Carport")
    c.value(100.0)
    assert c.value(103.5) == 103.5


def test_adopted_counter_continues_the_old_device():
    """Migration case: the legacy service stopped at 812.4 kWh while EVCC
    reports a lifetime total of 17010.75. VRM must not see 16198 kWh of
    charging in one hour."""
    c = EnergyCounter(adopt=812.4, title="Carport")
    assert round(c.value(17010.75), 3) == 812.4
    # and it keeps counting from there
    assert round(c.value(17012.75), 3) == 814.4


def test_adopt_is_consumed_only_once():
    c = EnergyCounter(adopt=812.4, title="Carport")
    c.value(17010.75)
    assert c.adopt is None
    assert "energy_adopt" not in c.as_dict()


def test_zero_reading_is_ignored_not_treated_as_reset():
    """EVCC returns 0 for a loadpoint whose meter is momentarily missing."""
    c = EnergyCounter(title="Heizstab")
    c.value(19341.908)
    assert c.value(0.0) == 19341.908
    assert c.value(19500.0) == 19500.0
    assert c.offset == 0.0


def test_single_low_reading_does_not_shift_the_offset():
    c = EnergyCounter(title="Carport")
    c.value(500.0)
    assert c.value(480.0) == 500.0     # held
    assert c.value(501.0) == 501.0     # source recovered, nothing shifted
    assert c.offset == 0.0


def test_confirmed_source_reset_continues_the_published_series():
    """EVCC reinstalled: its counter restarts near zero. The published counter
    must continue, not fall back and then re-count the same kWh."""
    c = EnergyCounter(title="Carport")
    c.value(17010.75)
    for _ in range(RESET_CONFIRMATIONS - 1):
        assert c.value(5.0) == 17010.75
    assert c.value(5.0) == 17010.75    # continues exactly where it stopped
    assert round(c.value(6.0), 3) == 17011.75


def test_published_counter_never_falls():
    c = EnergyCounter(title="Carport")
    c.value(100.0)
    c.offset = -50.0                   # would drag the value down
    assert c.value(100.5) == 100.0


def test_round_trip_through_state_dict():
    c = EnergyCounter(title="Carport")
    c.value(17010.75)
    c.value(17020.75)
    restored = EnergyCounter.from_dict(c.as_dict(), title="Carport")
    assert restored.offset == c.offset
    assert restored.source == c.source
    # After a restart the same source value publishes the same counter.
    assert restored.value(17020.75) == 17020.75


def test_dirty_flag_limits_writes():
    c = EnergyCounter(title="Carport")
    c.value(100.0)
    assert c.dirty is True             # first reading must be stored
    c.dirty = False
    c.value(100.2)
    assert c.dirty is False            # small step, no write
    c.value(101.5)
    assert c.dirty is True             # more than a kWh, write
