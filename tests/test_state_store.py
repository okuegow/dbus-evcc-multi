import json
import os

import pytest

from state_store import (
    DeviceInstanceExhausted,
    InvalidStateFile,
    StateStore,
)


def test_first_title_gets_first_di_in_range(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 59))
    assert s.get_or_allocate("HeatingElement") == 40


def test_second_title_gets_next_di(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 59))
    assert s.get_or_allocate("HeatingElement") == 40
    assert s.get_or_allocate("Wallbox") == 41


def test_existing_title_returns_same_di(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 59))
    s.get_or_allocate("HeatingElement")
    s.get_or_allocate("Wallbox")
    assert s.get_or_allocate("HeatingElement") == 40


def test_persists_across_instances(tmp_path):
    p = tmp_path / "state.json"
    s1 = StateStore(p, di_range=(40, 59))
    s1.get_or_allocate("HeatingElement")
    s1.get_or_allocate("Wallbox")
    s2 = StateStore(p, di_range=(40, 59))
    assert s2.get_or_allocate("HeatingElement") == 40
    assert s2.get_or_allocate("Wallbox") == 41
    assert s2.get_or_allocate("Heatpump") == 42


def test_seed_pre_assigns(tmp_path):
    p = tmp_path / "state.json"
    s = StateStore(p, di_range=(40, 59))
    s.seed({"HeatingElement": 56})
    assert s.get_or_allocate("HeatingElement") == 56
    assert s.get_or_allocate("Wallbox") == 40


def test_seed_skips_already_seeded_dis_in_auto_alloc(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 42))
    s.seed({"HeatingElement": 41})
    assert s.get_or_allocate("Wallbox") == 40
    assert s.get_or_allocate("Heatpump") == 42
    with pytest.raises(DeviceInstanceExhausted):
        s.get_or_allocate("Extra")


def test_exhausted_range_raises(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 41))
    s.get_or_allocate("A")
    s.get_or_allocate("B")
    with pytest.raises(DeviceInstanceExhausted):
        s.get_or_allocate("C")


def test_atomic_write_does_not_truncate_on_crash(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    s = StateStore(p, di_range=(40, 59))
    s.get_or_allocate("HeatingElement")

    import state_store as ss_module

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(ss_module.os, "replace", boom)
    with pytest.raises(OSError):
        s.get_or_allocate("Wallbox")
    monkeypatch.undo()

    s2 = StateStore(p, di_range=(40, 59))
    assert s2.get_or_allocate("HeatingElement") == 40


def test_reordered_titles_keep_their_dis(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 59))
    di_wb = s.get_or_allocate("Wallbox")
    di_hz = s.get_or_allocate("HeatingElement")
    di_hp = s.get_or_allocate("Heatpump")
    # EVCC config reordered: Heatpump, Wallbox, HeatingElement
    assert s.get_or_allocate("Heatpump") == di_hp
    assert s.get_or_allocate("Wallbox") == di_wb
    assert s.get_or_allocate("HeatingElement") == di_hz


def test_load_rejects_duplicate_dis(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"A": 40, "B": 40}))
    with pytest.raises(InvalidStateFile):
        StateStore(p, di_range=(40, 59))


def test_load_rejects_di_outside_range(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"A": 99}))
    with pytest.raises(InvalidStateFile):
        StateStore(p, di_range=(40, 59))


def test_seed_rejects_duplicate_dis(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 59))
    with pytest.raises(InvalidStateFile):
        s.seed({"A": 41, "B": 41})


def test_corrupt_json_file_starts_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("not json at all {")
    s = StateStore(p, di_range=(40, 59))
    assert s.get_or_allocate("X") == 40


def test_seed_does_not_overwrite_existing(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 59))
    s.get_or_allocate("HeatingElement")  # gets 40
    s.seed({"HeatingElement": 56})       # ignored, already mapped
    assert s.get_or_allocate("HeatingElement") == 40


def test_atomic_write_cleans_up_tmp_on_success(tmp_path):
    p = tmp_path / "state.json"
    s = StateStore(p, di_range=(40, 59))
    s.get_or_allocate("HeatingElement")
    # No leftover .tmp after a successful write
    assert not (tmp_path / "state.json.tmp").exists()


def test_seed_rolls_back_in_memory_on_flush_failure(tmp_path, monkeypatch):
    """If seed()'s flush blows up, the in-memory map must rewind so that the
    caller catching the OSError doesn't see a phantom seed."""
    p = tmp_path / "state.json"
    s = StateStore(p, di_range=(40, 59))
    s.get_or_allocate("Existing")
    snapshot_before = s.snapshot()

    import state_store as ss_module

    def boom(*a, **kw):
        raise OSError("seed flush failed")

    monkeypatch.setattr(ss_module.os, "replace", boom)
    with pytest.raises(OSError):
        s.seed({"NewTitle": 50})
    monkeypatch.undo()

    assert s.snapshot() == snapshot_before
    # Subsequent allocation still uses the lowest-free DI (not 41 + ghost)
    assert s.get_or_allocate("NewTitle") == 41


# --- v2 format: energy continuity ----------------------------------------

def test_reads_legacy_v1_state_file(tmp_path):
    p = tmp_path / "state.json"
    p.write_text('{"Carport": 49, "Heizstab": 56}')
    store = StateStore(p, di_range=(40, 59))
    assert store.snapshot() == {"Carport": 49, "Heizstab": 56}


def test_v1_file_is_rewritten_as_v2_on_next_write(tmp_path):
    import json
    p = tmp_path / "state.json"
    p.write_text('{"Carport": 49}')
    store = StateStore(p, di_range=(40, 59))
    store.get_or_allocate("Waermepumpe")
    data = json.loads(p.read_text())
    assert data["version"] == 2
    assert data["loadpoints"]["Carport"]["deviceinstance"] == 49


def test_energy_counter_is_empty_for_unknown_title(tmp_path):
    store = StateStore(tmp_path / "state.json", di_range=(40, 59))
    counter = store.energy_counter("Carport")
    assert counter.offset == 0.0
    assert counter.source is None


def test_seed_energy_requires_a_known_title(tmp_path):
    import pytest
    store = StateStore(tmp_path / "state.json", di_range=(40, 59))
    with pytest.raises(InvalidStateFile):
        store.seed_energy({"Carport": 812.4})


def test_seeded_counter_survives_a_reload(tmp_path):
    p = tmp_path / "state.json"
    store = StateStore(p, di_range=(40, 59))
    store.seed({"Carport": 49})
    store.seed_energy({"Carport": 812.4})

    reloaded = StateStore(p, di_range=(40, 59))
    counter = reloaded.energy_counter("Carport")
    assert counter.adopt == 812.4
    # First publish continues the old device instead of EVCC's own total.
    assert round(counter.value(17010.75), 3) == 812.4


def test_save_energy_persists_offset_and_clears_adopt(tmp_path):
    p = tmp_path / "state.json"
    store = StateStore(p, di_range=(40, 59))
    store.seed({"Carport": 49})
    store.seed_energy({"Carport": 812.4})

    counter = store.energy_counter("Carport")
    counter.value(17010.75)
    assert store.save_energy("Carport", counter) is True

    reloaded = StateStore(p, di_range=(40, 59))
    restored = reloaded.energy_counter("Carport")
    assert restored.adopt is None
    assert round(restored.value(17010.75), 3) == 812.4
    assert reloaded.snapshot()["Carport"] == 49


def test_save_energy_skips_write_when_counter_is_clean(tmp_path):
    store = StateStore(tmp_path / "state.json", di_range=(40, 59))
    store.seed({"Carport": 49})
    counter = store.energy_counter("Carport")
    counter.value(100.0)
    store.save_energy("Carport", counter)
    assert store.save_energy("Carport", counter) is False
