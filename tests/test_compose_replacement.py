"""Regression tests for Compose's temporary name during container recreation."""

import json
from dataclasses import replace

import httpx
import pytest
from notion_client.errors import HTTPResponseError
from requests.exceptions import ConnectionError

from src.docker_client import DockerSnapshot, compose_replacement
from src.event_reader import FILTER
from src.storage import identity, read_json
from src.sync_service import SyncService
from src.sync_state import ManagedContainer, SyncJob, SyncState


OLD_ID = "0123456789abcdef" * 4
FINAL_NAME = "app-web-1"
TEMP_NAME = f"{OLD_ID[:12]}_{FINAL_NAME}"
NEW_ID = "new-id"
LABELS = {
    "d2n.enabled": "true",
    "d2n.database": "A",
    "com.docker.compose.project": "app",
    "com.docker.compose.service": "web",
    "com.docker.compose.container-number": "1",
    "com.docker.compose.replace": "web-1",
}


def container_event(action, name, container_id, **attributes):
    assert action in FILTER["event"]
    return {
        "Type": "container",
        "Action": action,
        "Actor": {"ID": container_id, "Attributes": {"name": name, **attributes}},
    }


def rename_event():
    return container_event("rename", FINAL_NAME, NEW_ID, oldName=f"/{TEMP_NAME}", **LABELS)


@pytest.fixture
def recreation(harness):
    h = harness
    old = replace(h.container, name=FINAL_NAME, container_id=OLD_ID)
    final = replace(old, container_id=NEW_ID, image="nginx:new", compose_replace_label="web-1")
    staged = replace(final, name=TEMP_NAME, status="created", compose_temporary=True)
    inventory = {old.name: old, staged.name: staged}
    h.docker.get_container_info.side_effect = lambda reference: next(
        (item for item in inventory.values() if reference in (item.name, item.container_id)), None,
    )
    h.docker.snapshot.side_effect = lambda: DockerSnapshot(
        {item.container_id for item in inventory.values()}, list(inventory.values()),
    )
    return old, staged, final, inventory


@pytest.mark.parametrize(
    ("label", "name", "temporary"),
    [
        ("web-1", TEMP_NAME, True),
        ("web-1", FINAL_NAME, False),
        ("web_1", f"{OLD_ID[:12]}_app_web_1", True),
        ("app_custom", f"{OLD_ID[:12]}_app_custom", True),
        (f"{OLD_ID[:12]}_custom", f"{OLD_ID[:12]}_custom", False),
        (OLD_ID, TEMP_NAME, True),
        (OLD_ID, "ffffffffffff_app-web-1", False),
        ("", TEMP_NAME, False),
        ("web-1", f"{OLD_ID[:12]}_another-service", False),
    ],
)
def test_compose_replacement_names(label, name, temporary):
    assert compose_replacement(name, {**LABELS, "com.docker.compose.replace": label})[1] is temporary


@pytest.mark.parametrize("start", [False, True])
def test_compose_recreation_reuses_final_page_and_never_creates_temporary_page(harness, recreation, start):
    h = harness
    old, staged, final, inventory = recreation
    h.cache.set_page_id("db-a", FINAL_NAME, "service-page")
    h.notion.find_page_id.return_value = None
    h.service.submit_event(container_event("create", staged.name, staged.container_id, **LABELS))
    h.service.tick()
    h.service.submit_event({
        "Type": "network", "Action": "connect", "Actor": {"ID": "net", "Attributes": {"container": NEW_ID}},
    })
    h.service.request_snapshot()
    h.service.tick()
    assert not h.state.jobs
    h.notion.update_page.assert_called_once_with("service-page", old)
    h.notion.update_page.reset_mock()
    h.notion.find_page_id.assert_not_called()

    del inventory[old.name]
    h.service.submit_event(container_event("destroy", old.name, OLD_ID, **{"d2n.enabled": "true"}))
    del inventory[staged.name]
    current = replace(final, status="running" if start else "created")
    inventory[current.name] = current
    h.service.submit_event(rename_event())
    if start:
        h.service.submit_event(container_event("start", current.name, NEW_ID, **LABELS))
    h.service.tick()
    h.notion.update_page.assert_called_once_with("service-page", current)
    h.notion.create_page.assert_not_called()
    h.notion.mark_removed_if_matches.assert_not_called()


def test_normal_created_container_is_synced_even_with_similar_name(harness):
    h = harness
    current = replace(h.container, name=TEMP_NAME, status="created")
    h.docker.get_container_info.return_value = current
    h.notion.find_page_id.return_value = None
    h.service.submit_event(container_event("create", current.name, current.container_id, **{"d2n.enabled": "true"}))
    h.service.tick()
    h.notion.create_page.assert_called_once_with("db-a", current)


@pytest.mark.parametrize("renamed", [False, True])
@pytest.mark.parametrize("uncertain", [False, True])
def test_legacy_queued_temporary_job_cannot_write_a_page(harness, recreation, renamed, uncertain):
    h = harness
    _, staged, final, inventory = recreation
    key = identity("db-a", staged.name)
    h.state.jobs[key] = SyncJob("db-a", staged, due=h.now[0], uncertain=uncertain)
    h.state.save()
    h.service.state = SyncState(h.state.path)
    h.notion.find_page_id.return_value = None
    if renamed:
        del inventory[staged.name]
        inventory[final.name] = final
    h.service.tick()
    h.notion.create_page.assert_not_called()
    h.notion.update_page.assert_not_called()
    h.notion.mark_removed_if_matches.assert_not_called()
    if uncertain:
        h.notion.find_page_id.assert_called_once_with("db-a", staged.name)
        assert h.service.state.jobs[key].uncertain
    else:
        h.notion.find_page_id.assert_not_called()
        assert key not in h.service.state.jobs


@pytest.mark.parametrize("trigger", ["rename", "snapshot"])
def test_retires_only_the_recorded_temporary_page(
    harness, recreation, notion_transport, notion_status_schema, trigger,
):
    h = harness
    _, staged, final, inventory = recreation
    key = identity("db-a", staged.name)
    h.state.managed[key] = ManagedContainer("db-a", staged, "temp-page")
    h.cache.set_page_id("db-a", final.name, "service-page")
    h.cache.set_page_id("db-a", staged.name, "unrelated-cache-entry")
    del inventory[staged.name]
    inventory[final.name] = final
    updates = {}

    def handle(request):
        assert request.headers["Notion-Version"] == "2022-06-28"
        if request.method == "GET" and request.url.path == "/v1/databases/db-a":
            return httpx.Response(200, json=notion_status_schema)
        if request.method == "GET" and request.url.path == "/v1/pages/temp-page":
            return httpx.Response(200, json={
                "parent": {"type": "database_id", "database_id": "db-a"},
                "properties": {"Name": {"title": [{"plain_text": staged.name}]}},
            })
        if request.method == "PATCH" and request.url.path.startswith("/v1/pages/"):
            updates[request.url.path.rsplit("/", 1)[-1]] = json.loads(request.content)
            return httpx.Response(200, json={})
        pytest.fail(f"Unexpected Notion request: {request.method} {request.url.path}")

    h.service.notion = notion_transport(handle)
    if trigger == "rename":
        h.service.submit_event(rename_event())
    else:
        h.service.request_snapshot()
    h.service.tick()
    h.service.tick()

    assert updates["service-page"]["properties"]["Status"] == {"status": {"name": "running"}}
    assert updates["temp-page"] == {"properties": {"Status": {"status": {"name": "removed"}}}}
    assert h.state.managed[key].container.status == "removed"
    assert h.state.managed[identity("db-a", final.name)].page_id == "service-page"
    assert not h.state.jobs


@pytest.mark.parametrize("conflict", ["different-id", "different-db", "same-page", "name-in-use"])
def test_cleanup_skips_conflicting_identity(harness, recreation, conflict):
    h = harness
    old, staged, final, inventory = recreation
    db = "db-b" if conflict == "different-db" else "db-a"
    previous = replace(staged, container_id="other-id" if conflict == "different-id" else NEW_ID)
    h.state.managed[identity(db, staged.name)] = ManagedContainer(db, previous, "temp-page")
    if conflict == "same-page":
        h.state.managed[identity("db-a", final.name)] = ManagedContainer("db-a", old, "temp-page")
    del inventory[staged.name]
    inventory[final.name] = final
    h.service.submit_event(rename_event())
    if conflict == "name-in-use":
        inventory[staged.name] = replace(old, name=staged.name)
    h.service.tick()
    h.service.tick()
    h.notion.mark_removed_if_matches.assert_not_called()


@pytest.mark.parametrize("changed", ["database", "title", "missing"])
def test_cleanup_leaves_a_moved_renamed_or_missing_notion_page_untouched(notion_transport, changed):
    def handle(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/pages/temp-page"
        if changed == "missing":
            return httpx.Response(404, json={"code": "object_not_found", "message": "gone"})
        return httpx.Response(200, json={
            "parent": {"database_id": "db-b" if changed == "database" else "db-a"},
            "properties": {"Name": {"title": [{"plain_text": "renamed" if changed == "title" else TEMP_NAME}]}},
        })

    notion = notion_transport(handle)
    assert not notion.mark_removed_if_matches("temp-page", "db-a", TEMP_NAME)


def test_cleanup_retries_recorded_page_after_restart(harness, recreation):
    h = harness
    _, staged, final, inventory = recreation
    key = identity("db-a", staged.name)
    h.state.managed[key] = ManagedContainer("db-a", staged, "temp-page")
    h.notion.mark_removed_if_matches.side_effect = [
        HTTPResponseError(httpx.Response(503, text="unavailable")), True,
    ]
    del inventory[staged.name]
    inventory[final.name] = final
    h.service.submit_event(rename_event())
    h.service.tick()
    h.service.tick()
    saved = read_json(h.state.path)
    assert saved["jobs"][key]["cleanup_page_id"] == "temp-page"
    assert "compose_temporary" not in saved["managed"][key]["container"]
    state = SyncState(h.state.path)
    service = SyncService(h.docker, h.notion, h.cache, h.settings, state, h.stop, lambda: h.now[0])
    service.tick()
    service.tick()
    assert h.notion.mark_removed_if_matches.call_count == 1
    h.now[0] += 30
    service.tick()
    h.notion.mark_removed_if_matches.assert_called_with("temp-page", "db-a", staged.name)
    assert state.managed[key].container.status == "removed"
    assert not state.jobs


def test_rename_inspection_error_defers_to_snapshot(harness, recreation):
    h = harness
    h.docker.get_container_info.side_effect = ConnectionError("unavailable")
    h.service.submit_event(rename_event())
    assert h.service.snapshot_due == h.now[0] + 30
    assert not h.state.jobs


def test_manual_rename_does_not_move_pages(harness):
    h = harness
    h.service.submit_event(container_event("rename", "renamed", "id-old", oldName="/web", **{"d2n.enabled": "true"}))
    h.docker.get_container_info.assert_not_called()
    assert not h.state.jobs
