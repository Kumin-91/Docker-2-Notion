import time
from src.cache_manager import CacheManager


def _cache(tmp_path, ttl=300):
    return CacheManager(cache_file=str(tmp_path / "cache.json"), ttl_seconds=ttl)


def test_set_and_get(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("web", "page-1")
    assert cm.get_page_id("web") == "page-1"


def test_get_missing_returns_none(tmp_path):
    cm = _cache(tmp_path)
    assert cm.get_page_id("nope") is None


def test_remove(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("web", "page-1")
    cm.remove_page_id("web")
    assert cm.get_page_id("web") is None


def test_ttl_expiry(tmp_path):
    cm = _cache(tmp_path)
    cm.set_page_id("web", "page-1")
    # 타임스탬프를 과거로 강제하여 만료 유도
    cm.cache_data["web"]["timestamp"] = time.time() - 10_000
    assert cm.get_page_id("web") is None


def test_persistence_across_instances(tmp_path):
    cache_file = str(tmp_path / "cache.json")
    CacheManager(cache_file=cache_file).set_page_id("web", "page-1")
    reopened = CacheManager(cache_file=cache_file)
    assert reopened.get_page_id("web") == "page-1"
