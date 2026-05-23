import vrm_tunnel
from vrm_tunnel import DnatRule


def test_add_when_rule_absent_then_appends(monkeypatch):
    calls = []

    def fake_call(argv, **kw):
        calls.append(argv)
        # -C (check) returns nonzero => rule absent => proceed to -A (add).
        if "-C" in argv:
            return 1
        return 0  # -A succeeds

    monkeypatch.setattr(vrm_tunnel.subprocess, "call", fake_call)
    r = DnatRule("172.20.4.135", 80, "172.20.4.135:8099")
    r.add()
    assert r.added is True
    assert any("-A" in c for c in calls)
    assert calls[-1][:3] == ["iptables", "-t", "nat"]
    assert "--to-destination" in calls[-1]
    assert "172.20.4.135:8099" in calls[-1]


def test_add_when_rule_present_does_not_append(monkeypatch):
    calls = []

    def fake_call(argv, **kw):
        calls.append(argv)
        return 0  # -C returns 0 => rule already present

    monkeypatch.setattr(vrm_tunnel.subprocess, "call", fake_call)
    r = DnatRule("172.20.4.135", 80, "172.20.4.135:8099")
    r.add()
    assert r.added is False
    assert all("-A" not in c for c in calls)


def test_remove_only_deletes_when_added(monkeypatch):
    calls = []
    monkeypatch.setattr(vrm_tunnel.subprocess, "call",
                        lambda argv, **kw: calls.append(argv) or 0)
    r = DnatRule("172.20.4.135", 80, "172.20.4.135:8099")
    # never added -> remove() is a no-op
    r.remove()
    assert all("-D" not in c for c in calls)
    # simulate a successful add, then remove deletes exactly once
    r.added = True
    r.remove()
    assert any("-D" in c for c in calls)
    assert r.added is False
