"""Tests for the resource governor."""

import pytest

from watchtower.config import ResourceConfig
from watchtower.governor import GovernorState, ResourceGovernor, ResourceSnapshot


@pytest.fixture
def config():
    return ResourceConfig(
        max_cpu_percent=20,
        max_memory_mb=768,
        throttle_cpu_percent=15,
        throttle_memory_mb=600,
        system_cpu_ceiling=80,
        system_memory_ceiling=85,
    )


@pytest.fixture
def governor(config):
    return ResourceGovernor(config)


class TestGovernorStates:
    def test_initial_state_is_full(self, governor):
        assert governor.state == GovernorState.FULL

    def test_full_operation(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=5,
                watchtower_memory_mb=200,
                system_cpu_percent=30,
                system_memory_percent=40,
            )
        )
        assert governor.state == GovernorState.FULL
        assert governor.poll_interval_multiplier == 1

    def test_throttled_on_cpu(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=17,  # above throttle (15), below hard (20)
                watchtower_memory_mb=200,
                system_cpu_percent=30,
                system_memory_percent=40,
            )
        )
        assert governor.state == GovernorState.THROTTLED
        assert governor.poll_interval_multiplier == 2

    def test_throttled_on_memory(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=5,
                watchtower_memory_mb=650,  # above throttle (600), below hard (768)
                system_cpu_percent=30,
                system_memory_percent=40,
            )
        )
        assert governor.state == GovernorState.THROTTLED

    def test_paused_on_hard_cpu(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=25,  # above hard limit (20)
                watchtower_memory_mb=200,
                system_cpu_percent=30,
                system_memory_percent=40,
            )
        )
        assert governor.state == GovernorState.PAUSED
        assert governor.poll_interval_multiplier == 4

    def test_paused_on_hard_memory(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=5,
                watchtower_memory_mb=800,  # above hard limit (768)
                system_cpu_percent=30,
                system_memory_percent=40,
            )
        )
        assert governor.state == GovernorState.PAUSED

    def test_paused_on_system_cpu(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=5,
                watchtower_memory_mb=200,
                system_cpu_percent=85,  # above ceiling (80)
                system_memory_percent=40,
            )
        )
        assert governor.state == GovernorState.PAUSED

    def test_paused_on_system_memory(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=5,
                watchtower_memory_mb=200,
                system_cpu_percent=30,
                system_memory_percent=90,  # above ceiling (85)
            )
        )
        assert governor.state == GovernorState.PAUSED

    def test_dormant_on_crisis_cpu(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=5,
                watchtower_memory_mb=200,
                system_cpu_percent=97,  # crisis: > 95
                system_memory_percent=40,
            )
        )
        assert governor.state == GovernorState.DORMANT
        assert governor.poll_interval_multiplier == 8

    def test_dormant_on_crisis_memory(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=5,
                watchtower_memory_mb=200,
                system_cpu_percent=30,
                system_memory_percent=97,  # crisis: > 95
            )
        )
        assert governor.state == GovernorState.DORMANT


class TestGovernorPermissions:
    def test_full_allows_everything(self, governor):
        assert governor.can_investigate()
        assert governor.can_run_llm()
        assert governor.should_collect()

    def test_throttled_allows_investigate_not_llm(self, governor):
        governor.update(ResourceSnapshot(watchtower_cpu_percent=17))
        assert governor.can_investigate()
        assert not governor.can_run_llm()
        assert governor.should_collect()

    def test_paused_blocks_investigate(self, governor):
        governor.update(ResourceSnapshot(watchtower_cpu_percent=25))
        assert not governor.can_investigate()
        assert not governor.can_run_llm()
        assert governor.should_collect()

    def test_dormant_blocks_collect(self, governor):
        governor.update(ResourceSnapshot(system_cpu_percent=97))
        assert not governor.can_investigate()
        assert not governor.can_run_llm()
        assert not governor.should_collect()


class TestGovernorMetrics:
    def test_defer_investigation(self, governor):
        assert governor.investigations_deferred == 0
        governor.defer_investigation()
        governor.defer_investigation()
        assert governor.investigations_deferred == 2

    def test_state_changes_tracked(self, governor):
        governor.update(ResourceSnapshot(watchtower_cpu_percent=5))  # FULL (no change)
        governor.update(ResourceSnapshot(watchtower_cpu_percent=17))  # -> THROTTLED
        governor.update(ResourceSnapshot(watchtower_cpu_percent=25))  # -> PAUSED
        governor.update(ResourceSnapshot(system_cpu_percent=97))  # -> DORMANT

        status = governor.get_status()
        assert status["state"] == "dormant"
        assert status["state_changes"] == 3

    def test_get_status(self, governor):
        governor.update(
            ResourceSnapshot(
                watchtower_cpu_percent=10,
                watchtower_memory_mb=300,
                system_cpu_percent=50,
                system_memory_percent=60,
            )
        )
        status = governor.get_status()
        assert status["state"] == "full"
        assert status["watchtower_cpu_percent"] == 10
        assert status["watchtower_memory_mb"] == 300
        assert status["poll_interval_multiplier"] == 1

    def test_recovery_from_paused_to_full(self, governor):
        governor.update(ResourceSnapshot(watchtower_cpu_percent=25))
        assert governor.state == GovernorState.PAUSED

        governor.update(ResourceSnapshot(watchtower_cpu_percent=5))
        assert governor.state == GovernorState.FULL
        assert governor.poll_interval_multiplier == 1
