from src.status import normalize_status


def test_known_statuses_pass_through():
    assert normalize_status("running") == "running"
    assert normalize_status("exited") == "exited"
    assert normalize_status("paused") == "paused"
    assert normalize_status("created") == "created"
    assert normalize_status("restarting") == "restarting"


def test_dead_maps_to_exited():
    assert normalize_status("dead") == "exited"


def test_removing_maps_to_removed():
    assert normalize_status("removing") == "removed"


def test_case_insensitive():
    assert normalize_status("RUNNING") == "running"


def test_unknown_status_lowercased_passthrough():
    assert normalize_status("WeIrD") == "weird"
