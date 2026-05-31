import json
import os
import time
from src.logger import cache_logger

# 캐시 엔트리: {container_name: {"page_id": str, "timestamp": float}}
CacheData = dict[str, dict[str, str | float]]


class CacheManager:
    def __init__(self, cache_file: str = "data/cache.json", ttl_seconds: int = 300) -> None:
        self.cache_file = cache_file
        self.ttl_seconds = ttl_seconds
        self.cache_data: CacheData = self._load_cache()
        cache_logger.info(
            f"CacheManager initialized with cache file: {self.cache_file} and TTL: {self.ttl_seconds} seconds"
        )

    def _load_cache(self) -> CacheData:
        """캐시 파일에서 데이터를 로드"""
        cache_logger.info(f"Loading cache from file: {self.cache_file}")
        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r", encoding="utf-8") as file:
                try:
                    return json.load(file)
                except json.JSONDecodeError:
                    cache_logger.error(f"Cache file {self.cache_file} contains invalid JSON.")
                    return {}
        cache_logger.info(f"Cache file {self.cache_file} does not exist. Starting with empty cache.")
        return {}

    def _save_cache(self) -> None:
        """캐시 데이터를 파일에 저장"""
        cache_logger.debug(f"Saving cache to file: {self.cache_file}")
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w", encoding="utf-8") as file:
            json.dump(self.cache_data, file, ensure_ascii=False, indent=4)

    def get_page_id(self, container_name: str) -> str | None:
        """컨테이너 이름으로 캐시된 페이지 ID를 조회. TTL 검사 포함."""
        cache_logger.debug(f"Retrieving page ID from cache for container: {container_name}")
        entry = self.cache_data.get(container_name)
        if not entry:
            return None

        saved_time = float(entry.get("timestamp", 0))
        if time.time() - saved_time > self.ttl_seconds:
            cache_logger.debug(
                f"Cache entry for container {container_name} has expired. Removing from cache."
            )
            del self.cache_data[container_name]
            self._save_cache()
            return None

        return str(entry.get("page_id"))

    def set_page_id(self, container_name: str, page_id: str) -> None:
        """컨테이너 이름에 대한 페이지 ID를 캐시에 저장"""
        cache_logger.debug(f"Setting page ID in cache for container: {container_name}")
        self.cache_data[container_name] = {
            "page_id": page_id,
            "timestamp": time.time(),
        }
        self._save_cache()

    def remove_page_id(self, container_name: str) -> None:
        """컨테이너 이름에 대한 캐시된 페이지 ID를 제거"""
        cache_logger.debug(f"Removing page ID from cache for container: {container_name}")
        if container_name in self.cache_data:
            del self.cache_data[container_name]
            self._save_cache()
