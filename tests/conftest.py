from dataclasses import replace
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.cache_manager import CacheManager
from src.docker_client import DockerClient, DockerSnapshot
from src.models import DockerContainerInfo
from src.notion_client import NotionClient
from src.sync_service import SyncService
from src.sync_state import SyncState


@pytest.fixture
def container():
    return DockerContainerInfo(
        container_id="id-old", name="web", status="running",
        seen="2026-10-07T10:00:00+09:00", ip="10.0.0.2: bridge",
        port="80 → 8080/tcp", image="nginx:latest",
        created="2026-10-07T09:00:00+09:00", stack="app",
        d2n_enabled=True, d2n_database="A",
    )


@pytest.fixture
def harness(tmp_path, container):
    now = [1000.0]
    settings = SimpleNamespace(
        TIMEZONE="Asia/Seoul",
        resolve_db_id=lambda name: {"A": "db-a", "B": "db-b", "Alias": "db-a"}.get(name, "db-a"),
    )
    docker = MagicMock(spec=DockerClient)
    docker.get_container_info.return_value = container
    docker.snapshot.return_value = DockerSnapshot({container.container_id}, [container])
    notion = MagicMock(spec=NotionClient)
    notion.retry_not_before = 0.0
    notion.find_page_id.return_value = "page-a"
    notion.create_page.return_value = "new-page"
    cache = CacheManager(str(tmp_path / "cache.v2.json"))
    state = SyncState(str(tmp_path / "sync-state.v1.json"))
    stop = Event()
    service = SyncService(docker, notion, cache, settings, state, stop, lambda: now[0])
    service.snapshot_due = float("inf")
    return SimpleNamespace(
        service=service, docker=docker, notion=notion, cache=cache, state=state,
        settings=settings, stop=stop, now=now, container=container, replace=replace,
    )
