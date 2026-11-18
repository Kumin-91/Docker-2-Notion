import json
import os
from dataclasses import replace

import pytest

from src.storage import identity
from src.sync_state import ManagedContainer, SyncJob, SyncState


def test_jobs_and_inventory_persist_independently_of_cache(tmp_path, container):
    path = str(tmp_path / "state.json")
    state = SyncState(path)
    key = identity("db-a", "web")
    state.jobs[key] = SyncJob("db-a", container, due=1234, attempts=3, uncertain=True)
    state.managed[key] = ManagedContainer("db-a", replace(container, status="exited"), "page-a")
    state.save()
    reopened = SyncState(path)
    assert reopened.jobs[key].uncertain and reopened.jobs[key].attempts == 3
    assert reopened.managed[key].page_id == "page-a"
    assert reopened.managed[key].container.status == "exited"


def test_new_state_directories_and_file_are_synced_in_order(tmp_path, monkeypatch):
    parent = tmp_path / "new" / "data"
    path = parent / "state.json"
    synced = []
    real_fsync = os.fsync

    def record(fd):
        synced.append((os.fstat(fd).st_ino, path.exists()))
        real_fsync(fd)

    monkeypatch.setattr("src.storage.os.fsync", record)

    SyncState(str(path)).save()

    assert synced == [
        (tmp_path.stat().st_ino, False),
        (parent.parent.stat().st_ino, False),
        (path.stat().st_ino, False),
        (parent.stat().st_ino, True),
    ]


@pytest.mark.parametrize("data", ["{", "null", "[]", '{"version":2}', '{"version":1,"jobs":[],"managed":{}}'])
def test_corrupt_state_fails_closed_without_overwriting(tmp_path, data):
    path = tmp_path / "state.json"
    path.write_text(data)
    with pytest.raises((ValueError, TypeError)):
        SyncState(str(path))
    assert path.read_text() == data


@pytest.mark.parametrize("change", [
    {"database_id": "another-db"}, {"due": -1}, {"due": float("nan")},
    {"attempts": -1}, {"attempts": True}, {"uncertain": "false"}, {"container": {}},
])
def test_invalid_job_fields_never_discard_markers(tmp_path, container, change):
    path = str(tmp_path / "state.json")
    state = SyncState(path)
    key = identity("db-a", "web")
    state.jobs[key] = SyncJob("db-a", container, uncertain=True)
    state.save()
    with open(path) as file:
        data = json.load(file)
    data["jobs"][key].update(change)
    with open(path, "w") as file:
        json.dump(data, file)
    with open(path) as file:
        previous = file.read()
    with pytest.raises((ValueError, TypeError)):
        SyncState(path)
    with open(path) as file:
        assert file.read() == previous
