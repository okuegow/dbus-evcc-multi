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
    assert s.get_or_allocate("Heizstab") == 40


def test_second_title_gets_next_di(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 59))
    assert s.get_or_allocate("Heizstab") == 40
    assert s.get_or_allocate("Wallbox") == 41


def test_existing_title_returns_same_di(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 59))
    s.get_or_allocate("Heizstab")
    s.get_or_allocate("Wallbox")
    assert s.get_or_allocate("Heizstab") == 40


def test_persists_across_instances(tmp_path):
    p = tmp_path / "state.json"
    s1 = StateStore(p, di_range=(40, 59))
    s1.get_or_allocate("Heizstab")
    s1.get_or_allocate("Wallbox")
    s2 = StateStore(p, di_range=(40, 59))
    assert s2.get_or_allocate("Heizstab") == 40
    assert s2.get_or_allocate("Wallbox") == 41
    assert s2.get_or_allocate("Heatpump") == 42


def test_seed_pre_assigns(tmp_path):
    p = tmp_path / "state.json"
    s = StateStore(p, di_range=(40, 59))
    s.seed({"Heizstab": 56})
    assert s.get_or_allocate("Heizstab") == 56
    assert s.get_or_allocate("Wallbox") == 40


def test_seed_skips_already_seeded_dis_in_auto_alloc(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 42))
    s.seed({"Heizstab": 41})
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
    s.get_or_allocate("Heizstab")

    import state_store as ss_module

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(ss_module.os, "replace", boom)
    with pytest.raises(OSError):
        s.get_or_allocate("Wallbox")
    monkeypatch.undo()

    s2 = StateStore(p, di_range=(40, 59))
    assert s2.get_or_allocate("Heizstab") == 40


def test_reordered_titles_keep_their_dis(tmp_path):
    s = StateStore(tmp_path / "state.json", di_range=(40, 59))
    di_wb = s.get_or_allocate("Wallbox")
    di_hz = s.get_or_allocate("Heizstab")
    di_hp = s.get_or_allocate("Heatpump")
    # EVCC config reordered: Heatpump, Wallbox, Heizstab
    assert s.get_or_allocate("Heatpump") == di_hp
    assert s.get_or_allocate("Wallbox") == di_wb
    assert s.get_or_allocate("Heizstab") == di_hz


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
    s.get_or_allocate("Heizstab")  # gets 40
    s.seed({"Heizstab": 56})       # ignored, already mapped
    assert s.get_or_allocate("Heizstab") == 40


def test_atomic_write_cleans_up_tmp_on_success(tmp_path):
    p = tmp_path / "state.json"
    s = StateStore(p, di_range=(40, 59))
    s.get_or_allocate("Heizstab")
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
