from __future__ import annotations

from scripts.container_resilience_smoke import hardened_prefix, scenarios


def test_resilience_scenarios_keep_the_container_boundary() -> None:
    all_scenarios = scenarios("paic:test")
    assert {scenario.name for scenario in all_scenarios} == {
        "bounded-memory-cpu-fd",
        "disk-exhaustion-fails-closed",
        "network-unavailable-fails-closed",
    }
    for scenario in all_scenarios:
        assert "--read-only" in scenario.args
        assert "--cap-drop" in scenario.args
        assert "ALL" in scenario.args
        assert "--network" in scenario.args
        assert "none" in scenario.args


def test_hardened_prefix_has_no_network_and_bounded_tmpfs() -> None:
    command = hardened_prefix("paic:test", "validate")
    assert command[:4] == ("docker", "run", "--rm", "--network")
    assert "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777" in command
