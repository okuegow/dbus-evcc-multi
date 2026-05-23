"""Tests for the discovery + parsing half of the auto-migrator.

We never touch a real /data/ filesystem - everything runs against tmp_path
fixtures that fake a customer Cerbo layout.
"""
from pathlib import Path

import pytest

from migrator import (
    LegacyInstall,
    discover_installations,
    parse_legacy_config,
)


def _make_install(
    base: Path, name: str, *, ini_content: str | None = None,
    write_uninstall: bool = True,
) -> Path:
    """Create a fake /data/dbus-evcc-<name>/ with the given config.ini."""
    d = base / ("dbus-evcc-" + name)
    d.mkdir()
    if ini_content is not None:
        (d / "config.ini").write_text(ini_content)
    if write_uninstall:
        (d / "uninstall.sh").write_text("#!/bin/sh\necho noop\n")
        (d / "uninstall.sh").chmod(0o755)
    return d


BRUCKSCH_INI = """\
[DEFAULT]
AccessType = OnPremise
SignOfLifeLog = 1
Deviceinstance = 56
CustomName = Heizstab

[ONPREMISE]
Host = 192.168.1.50:7070
"""

DSTEINKOPF_INI = """\
[DEFAULT]
AccessType = OnPremise
SignOfLifeLog = 5
Deviceinstance = 49
CustomName = Garage Wallbox
LoadpointIndex = 1

[ONPREMISE]
Host = 192.168.1.50:7070
"""

NO_CUSTOMNAME_INI = """\
[DEFAULT]
Deviceinstance = 51

[ONPREMISE]
Host = 192.168.1.50:7070
"""

BROKEN_INI = "this is not valid ini { garbage"


# ----- parse_legacy_config -----------------------------------------------

def test_parse_brucksch_full(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(BRUCKSCH_INI)
    install = parse_legacy_config(p)
    assert install.deviceinstance == 56
    assert install.custom_name == "Heizstab"
    assert install.host == "192.168.1.50:7070"
    assert install.loadpoint_index is None
    assert install.errors == []


def test_parse_dsteinkopf_fork_with_index(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(DSTEINKOPF_INI)
    install = parse_legacy_config(p)
    assert install.deviceinstance == 49
    assert install.custom_name == "Garage Wallbox"
    assert install.loadpoint_index == 1


def test_parse_missing_customname_returns_none(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(NO_CUSTOMNAME_INI)
    install = parse_legacy_config(p)
    assert install.deviceinstance == 51
    assert install.custom_name is None
    assert install.host == "192.168.1.50:7070"


def test_parse_missing_deviceinstance_records_error(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text("[DEFAULT]\nCustomName = X\n[ONPREMISE]\nHost = h:1\n")
    install = parse_legacy_config(p)
    assert install.deviceinstance is None
    assert any("Deviceinstance" in e for e in install.errors)


def test_parse_invalid_deviceinstance_records_error(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text("[DEFAULT]\nDeviceinstance = banana\n[ONPREMISE]\nHost = h:1\n")
    install = parse_legacy_config(p)
    assert install.deviceinstance is None
    assert any("banana" in e or "integer" in e.lower() for e in install.errors)


def test_parse_completely_broken_ini_records_error(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(BROKEN_INI)
    install = parse_legacy_config(p)
    assert install.deviceinstance is None
    assert install.errors  # has at least one error string


def test_parse_missing_file_records_error(tmp_path):
    install = parse_legacy_config(tmp_path / "does-not-exist.ini")
    assert install.deviceinstance is None
    assert any("not found" in e.lower() or "no such" in e.lower()
               for e in install.errors)


# ----- discover_installations --------------------------------------------

def test_discover_finds_all_legacy_bridges(tmp_path):
    _make_install(tmp_path, "heizstab", ini_content=BRUCKSCH_INI)
    _make_install(tmp_path, "garage", ini_content=DSTEINKOPF_INI)
    found = discover_installations(tmp_path)
    names = sorted(i.path.name for i in found)
    assert names == ["dbus-evcc-garage", "dbus-evcc-heizstab"]


def test_discover_skips_our_own_multi_directory(tmp_path):
    _make_install(tmp_path, "heizstab", ini_content=BRUCKSCH_INI)
    # Our own multi/ must not be reported as legacy
    multi = tmp_path / "dbus-evcc-multi"
    multi.mkdir()
    (multi / "config.ini").write_text("[DEFAULT]\nDeviceinstance = 40\n")
    found = discover_installations(tmp_path)
    names = [i.path.name for i in found]
    assert "dbus-evcc-multi" not in names
    assert "dbus-evcc-heizstab" in names


def test_discover_returns_empty_when_no_legacy(tmp_path):
    # only dbus-evcc-multi exists
    (tmp_path / "dbus-evcc-multi").mkdir()
    found = discover_installations(tmp_path)
    assert found == []


def test_discover_ignores_unrelated_dirs(tmp_path):
    _make_install(tmp_path, "heizstab", ini_content=BRUCKSCH_INI)
    (tmp_path / "evcc").mkdir()
    (tmp_path / "evcc" / "evcc.yaml").write_text("noop")
    (tmp_path / "other-stuff").mkdir()
    found = discover_installations(tmp_path)
    assert len(found) == 1
    assert found[0].path.name == "dbus-evcc-heizstab"


def test_discover_returns_install_with_missing_config_ini(tmp_path):
    """A dbus-evcc-* dir without config.ini is reported with an error,
    not silently dropped. Otherwise the operator wouldn't know it's there."""
    d = tmp_path / "dbus-evcc-truncated"
    d.mkdir()
    found = discover_installations(tmp_path)
    assert len(found) == 1
    inst = found[0]
    assert inst.path.name == "dbus-evcc-truncated"
    assert inst.deviceinstance is None
    assert inst.errors  # explains why we can't use it


def test_discover_returns_install_when_ini_is_broken(tmp_path):
    _make_install(tmp_path, "garbage", ini_content=BROKEN_INI)
    found = discover_installations(tmp_path)
    assert len(found) == 1
    assert found[0].errors


def test_discover_detects_uninstall_script_presence(tmp_path):
    _make_install(tmp_path, "withus", ini_content=BRUCKSCH_INI,
                  write_uninstall=True)
    _make_install(tmp_path, "without", ini_content=BRUCKSCH_INI,
                  write_uninstall=False)
    by_name = {i.path.name: i for i in discover_installations(tmp_path)}
    assert by_name["dbus-evcc-withus"].uninstall_script is not None
    assert by_name["dbus-evcc-without"].uninstall_script is None


def test_discover_returns_sorted_by_path(tmp_path):
    """Stable order makes the operator's diff against state.json predictable."""
    _make_install(tmp_path, "zwallbox", ini_content=BRUCKSCH_INI)
    _make_install(tmp_path, "aheizstab", ini_content=BRUCKSCH_INI)
    _make_install(tmp_path, "mgarage", ini_content=BRUCKSCH_INI)
    found = discover_installations(tmp_path)
    names = [i.path.name for i in found]
    assert names == [
        "dbus-evcc-aheizstab",
        "dbus-evcc-mgarage",
        "dbus-evcc-zwallbox",
    ]


def test_discover_dedupes_symlinked_aliases(tmp_path):
    """SF1: /data/dbus-evcc-current -> /data/dbus-evcc-heizstab must NOT
    produce two installations pointing at the same uninstall.sh."""
    _make_install(tmp_path, "heizstab", ini_content=BRUCKSCH_INI)
    (tmp_path / "dbus-evcc-current").symlink_to(tmp_path / "dbus-evcc-heizstab")
    found = discover_installations(tmp_path)
    # The real directory wins (sorted by name, 'current' < 'heizstab' alphabetically,
    # so the symlink is encountered first - but after resolve() both point at
    # the same target, the duplicate is dropped).
    assert len(found) == 1
    # The kept entry must be the real directory, not the symlink alias
    assert found[0].path.resolve() == (tmp_path / "dbus-evcc-heizstab").resolve()


# ----- LegacyInstall.is_usable -------------------------------------------

def test_is_usable_true_when_di_present_and_no_errors(tmp_path):
    _make_install(tmp_path, "x", ini_content=BRUCKSCH_INI)
    inst = discover_installations(tmp_path)[0]
    assert inst.is_usable is True


def test_is_usable_false_when_di_missing(tmp_path):
    _make_install(tmp_path, "x", ini_content="[DEFAULT]\n[ONPREMISE]\n")
    inst = discover_installations(tmp_path)[0]
    assert inst.is_usable is False


# ----- propose_mappings (title matching) ---------------------------------

from migrator import Proposal, propose_mappings


def _install(name, di, custom_name=None, loadpoint_index=None):
    return LegacyInstall(
        path=Path("/data") / ("dbus-evcc-" + name),
        deviceinstance=di,
        custom_name=custom_name,
        loadpoint_index=loadpoint_index,
    )


def test_propose_matches_via_loadpoint_index_first():
    """If LoadpointIndex is set, that's authoritative - it points to a
    specific slot in EVCC's loadpoints[] array."""
    installs = [_install("garage", 49, custom_name="Garage", loadpoint_index=1)]
    titles = ["Heizstab", "Wallbox", "Wärmepumpe"]
    proposals = propose_mappings(installs, titles)
    assert proposals[0].evcc_title == "Wallbox"
    assert proposals[0].confidence == "index"


def test_propose_matches_via_customname_case_insensitive():
    installs = [_install("h", 56, custom_name="heizstab")]
    titles = ["Heizstab", "Garage"]
    proposals = propose_mappings(installs, titles)
    assert proposals[0].evcc_title == "Heizstab"
    assert proposals[0].confidence == "exact-name"


def test_propose_marks_ambiguous_when_no_match():
    installs = [_install("legacy", 56, custom_name="OldName")]
    titles = ["Heizstab", "Garage"]
    proposals = propose_mappings(installs, titles)
    assert proposals[0].evcc_title is None
    assert proposals[0].confidence == "needs-operator"


def test_propose_skips_install_without_di():
    installs = [_install("broken", None, custom_name="X")]
    titles = ["X"]
    proposals = propose_mappings(installs, titles)
    assert proposals == []  # unusable, dropped


def test_propose_index_out_of_range_does_NOT_fall_back():
    """SF2: a stale LoadpointIndex is a strong signal that the install is
    out of sync with EVCC. Don't silently fall back to name matching - the
    name could collide with a different loadpoint. Require operator review."""
    installs = [_install("x", 49, custom_name="Heizstab", loadpoint_index=99)]
    titles = ["Heizstab", "Garage"]
    proposals = propose_mappings(installs, titles)
    assert proposals[0].evcc_title is None
    assert proposals[0].confidence == "needs-operator"


def test_propose_duplicate_evcc_titles_force_needs_operator():
    """BLOCKER: if EVCC returns two loadpoints with the same title, our
    identity model collapses them. Don't auto-match - even an exact-name
    match would seed only one of the two. Force operator review."""
    installs = [_install("h", 56, custom_name="Heizstab")]
    titles = ["Heizstab", "Heizstab", "Garage"]
    proposals = propose_mappings(installs, titles)
    assert proposals[0].evcc_title is None
    assert proposals[0].confidence == "needs-operator"


def test_propose_index_pointing_into_duplicate_block_force_needs_operator():
    """Same BLOCKER but via index: if titles[idx] is a duplicate, no auto."""
    installs = [_install("g", 49, custom_name="anything", loadpoint_index=0)]
    titles = ["Heizstab", "Heizstab"]
    proposals = propose_mappings(installs, titles)
    assert proposals[0].evcc_title is None
    assert proposals[0].confidence == "needs-operator"


def test_propose_no_customname_no_index_returns_needs_operator():
    installs = [_install("blank", 49)]
    titles = ["Heizstab", "Garage"]
    proposals = propose_mappings(installs, titles)
    assert proposals[0].evcc_title is None
    assert proposals[0].confidence == "needs-operator"


def test_propose_keeps_di_and_install_path_in_proposal():
    install = _install("heizstab", 56, custom_name="Heizstab")
    proposals = propose_mappings([install], ["Heizstab"])
    p = proposals[0]
    assert p.deviceinstance == 56
    assert p.install is install


def test_propose_multiple_installs_one_call():
    installs = [
        _install("hz", 56, custom_name="Heizstab"),
        _install("ga", 49, custom_name="Garage", loadpoint_index=1),
    ]
    titles = ["Heizstab", "Garage"]
    proposals = propose_mappings(installs, titles)
    assert len(proposals) == 2
    by_di = {p.deviceinstance: p for p in proposals}
    assert by_di[56].evcc_title == "Heizstab"
    assert by_di[49].evcc_title == "Garage"


def test_propose_empty_evcc_titles_makes_all_needs_operator():
    """If EVCC is unreachable, the CLI may pass an empty titles list;
    then every proposal needs operator input."""
    installs = [_install("h", 56, custom_name="Heizstab")]
    proposals = propose_mappings(installs, [])
    assert proposals[0].confidence == "needs-operator"
    assert proposals[0].evcc_title is None
