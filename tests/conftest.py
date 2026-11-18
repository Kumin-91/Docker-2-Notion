from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from notion_client import Client

from src.cache_manager import CacheManager
from src.docker_client import DockerClient, DockerSnapshot
from src.models import DockerContainerInfo
from src.notion_client import NotionClient
from src.sync_service import SyncService
from src.sync_state import SyncState
from src.status import STATUS_OPTIONS


@pytest.fixture
def mock_notion(monkeypatch):
    """Keep application initialization real; mock only the external SDK."""
    sdk = MagicMock()
    monkeypatch.setattr("src.notion_client.Client", lambda **kwargs: sdk)
    return NotionClient("test-token")


@pytest.fixture
def notion_transport(monkeypatch):
    """Use the pinned SDK with an in-memory HTTP transport and close every client."""
    clients = []

    def create(handler, stop_event=None):
        sdk = Client(client=httpx.Client(transport=httpx.MockTransport(handler)), auth="test-token")
        with monkeypatch.context() as patch:
            patch.setattr("src.notion_client.Client", lambda **kwargs: sdk)
            client = NotionClient("test-token", stop_event)
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.close()


@pytest.fixture
def notion_status_schema():
    return {"properties": {"Status": {
        "id": "stat", "type": "status", "status": {
            "options": [{"id": option["name"], "name": option["name"], "color": option["color"]}
                        for option in STATUS_OPTIONS],
            "groups": [{"name": group, "option_ids": [option["name"] for option in STATUS_OPTIONS
                                                      if option["group"] == group]}
                       for group in ("To-do", "In progress", "Complete")],
        },
    }}}


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
        settings=settings, stop=stop, now=now, container=container,
    )
