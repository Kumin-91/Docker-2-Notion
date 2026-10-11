from src.status import normalize_status
import pytest


def test_known_statuses_pass_through():
    assert normalize_status("running") == "running"
    assert normalize_status("exited") == "exited"
    assert normalize_status("paused") == "paused"
    assert normalize_status("created") == "created"
    assert normalize_status("restarting") == "restarting"


def test_dead_is_distinct_from_exited():
    assert normalize_status("dead") == "dead"


def test_removing_maps_to_removed():
    assert normalize_status("removing") == "removed"


def test_case_insensitive():
    assert normalize_status("RUNNING") == "running"


def test_unknown_status_lowercased_passthrough():
    assert normalize_status("WeIrD") == "weird"


@pytest.mark.parametrize("health", ["starting", "healthy", "unhealthy"])
def test_running_prefers_health_status(health):
    assert normalize_status("running", health) == health


@pytest.mark.parametrize("health", ["", "none", "unknown"])
def test_running_without_known_health_remains_running(health):
    assert normalize_status("running", health) == "running"


@pytest.mark.parametrize("status, expected", [
    ("created", "created"), ("restarting", "restarting"), ("paused", "paused"),
    ("exited", "exited"), ("dead", "dead"), ("removing", "removed"), ("removed", "removed"),
])
def test_inactive_or_restarting_status_overrides_stale_health(status, expected):
    assert normalize_status(status, "healthy") == expected
