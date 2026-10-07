import time
import json
import pytest
from src.storage import identity
from src.cache_manager import CacheManager


def _cache(tmp_path, ttl=300):
    return CacheManager(cache_file=str(tmp_path / "cache.json"), ttl_seconds=ttl)


def test_set_and_get(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("db-a", "web", "page-1")
    assert cm.get_page_id("db-a", "web") == "page-1"


def test_get_missing_returns_none(tmp_path):
    cm = _cache(tmp_path)
    assert cm.get_page_id("db-a", "nope") is None


def test_remove(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("db-a", "web", "page-1")
    cm.remove_page_id("db-a", "web")
    assert cm.get_page_id("db-a", "web") is None


def test_ttl_expiry(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("db-a", "web", "page-1")
    # 타임스탬프를 과거로 강제하여 만료 유도
    cm.cache_data[identity("db-a", "web")]["timestamp"] = time.time() - 10_000
    assert cm.get_page_id("db-a", "web") is None


def test_persistence_across_instances(tmp_path):
    cache_file = str(tmp_path / "cache.json")
    CacheManager(cache_file=cache_file).set_page_id("db-a", "web", "page-1")
    reopened = CacheManager(cache_file=cache_file)
    assert reopened.get_page_id("db-a", "web") == "page-1"


def test_database_isolation_and_targeted_removal(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("db-a", "web", "page-a")
    cm.set_page_id("db-b", "web", "page-b")
    assert cm.get_page_id("db-a", "web") == "page-a"
    assert cm.get_page_id("db-b", "web") == "page-b"
    cm.remove_page_id("db-a", "web")
    assert cm.get_page_id("db-b", "web") == "page-b"


def test_expiry_isolated_to_database(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("db-a", "web", "page-a")
    cm.set_page_id("db-b", "web", "page-b")
    cm.cache_data[identity("db-a", "web")]["timestamp"] = 0
    assert cm.get_page_id("db-a", "web") is None
    assert cm.get_page_id("db-b", "web") == "page-b"


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
