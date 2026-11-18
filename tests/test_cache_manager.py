import time
import json
import pytest
from src.cache_manager import CacheManager


def _cache(tmp_path, ttl=300):
    return CacheManager(cache_file=str(tmp_path / "cache.json"), ttl_seconds=ttl)


def test_cache_round_trip_survives_restart(tmp_path):
    cm = _cache(tmp_path)
    assert cm.get_page_id("db-a", "web") is None
    cm.set_page_id("db-a", "web", "page-1")
    assert cm.get_page_id("db-a", "web") == "page-1"
    assert _cache(tmp_path).get_page_id("db-a", "web") == "page-1"


def test_database_isolation_and_targeted_removal(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("db-a", "web", "page-a")
    cm.set_page_id("db-b", "web", "page-b")
    assert cm.get_page_id("db-a", "web") == "page-a"
    assert cm.get_page_id("db-b", "web") == "page-b"
    cm.remove_page_id("db-a", "web")
    assert cm.get_page_id("db-a", "web") is None
    assert cm.get_page_id("db-b", "web") == "page-b"
    assert _cache(tmp_path).get_page_id("db-a", "web") is None


def test_ttl_boundary_expires_only_the_older_database_entry(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("src.cache_manager.time.time", lambda: now[0])
    cm = _cache(tmp_path)
    cm.set_page_id("db-a", "web", "page-a")
    now[0] += 1
    cm.set_page_id("db-b", "web", "page-b")
    now[0] = 1300.0
    assert cm.get_page_id("db-a", "web") == "page-a"
    now[0] += 1
    assert cm.get_page_id("db-a", "web") is None
    assert cm.get_page_id("db-b", "web") == "page-b"
    assert _cache(tmp_path).get_page_id("db-a", "web") is None


def test_equivalent_uuid_ids_share_cache(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("12345678123456781234567812345678", "web", "page-a")
    assert cm.get_page_id("12345678-1234-5678-1234-567812345678", "web") == "page-a"


def test_identity_has_no_delimiter_collision(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("a:b", "c", "one")
    cm.set_page_id("a", "b:c", "two")
    assert cm.get_page_id("a:b", "c") == "one"
    assert cm.get_page_id("a", "b:c") == "two"


@pytest.mark.parametrize("data", [{"web": {"page_id": "legacy", "timestamp": time.time()}}, {"version": 9}, {"version": 2, "entries": []}])
def test_legacy_and_invalid_shapes_are_not_reused(tmp_path, data):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(data))
    assert CacheManager(str(path)).get_page_id("db-a", "web") is None


def test_atomic_cache_write_failure_keeps_previous_file(tmp_path, monkeypatch):
    cm = _cache(tmp_path)
    cm.set_page_id("db-a", "web", "page-a")
    previous = (tmp_path / "cache.json").read_text()
    monkeypatch.setattr("src.storage.os.replace", lambda *args: (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError):
        cm.set_page_id("db-a", "web", "page-b")
    assert (tmp_path / "cache.json").read_text() == previous
    assert list(tmp_path.iterdir()) == [tmp_path / "cache.json"]
