import json
import math
import time
from typing import TypedDict

from src.logger import cache_logger
from src.storage import identity, read_json, write_json


class CacheEntry(TypedDict):
    page_id: str
    timestamp: float


class CacheManager:
    def __init__(self, cache_file: str = "data/cache.v2.json", ttl_seconds: int = 300) -> None:
        self.cache_file = cache_file
        self.ttl_seconds = ttl_seconds
        self.cache_data: dict[str, CacheEntry] = self._load_cache()

    def _load_cache(self) -> dict[str, CacheEntry]:
        try:
            data = read_json(self.cache_file)
        except (json.JSONDecodeError, UnicodeDecodeError):
            cache_logger.warning("Invalid page cache; rebuilding through Notion search.")
            return {}
        if data is None:
            return {}
        if not isinstance(data, dict) or data.get("version") != 2:
            cache_logger.warning("Legacy or unsupported page cache ignored; DB ownership is unknown.")
            return {}
        entries = data.get("entries")
        if not isinstance(entries, dict):
            return {}
        validated: dict[str, CacheEntry] = {}
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            page_id, timestamp = entry.get("page_id"), entry.get("timestamp")
            if (
                isinstance(page_id, str) and page_id
                and isinstance(timestamp, (int, float)) and math.isfinite(timestamp)
            ):
                validated[key] = {"page_id": page_id, "timestamp": float(timestamp)}
        return validated

    def _save_cache(self) -> None:
        write_json(self.cache_file, {"version": 2, "entries": self.cache_data})

    def get_page_id(self, database_id: str, container_name: str) -> str | None:
        key = identity(database_id, container_name)
        entry = self.cache_data.get(key)
        if entry is None:
            return None
        if time.time() - entry["timestamp"] > self.ttl_seconds:
            del self.cache_data[key]
            self._save_cache()
            return None
        return entry["page_id"]

    def set_page_id(self, database_id: str, container_name: str, page_id: str) -> None:
        self.cache_data[identity(database_id, container_name)] = {
            "page_id": page_id, "timestamp": time.time()
        }
        self._save_cache()

    def remove_page_id(self, database_id: str, container_name: str) -> None:
        if self.cache_data.pop(identity(database_id, container_name), None) is not None:
            self._save_cache()
