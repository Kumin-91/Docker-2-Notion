"""Schema migration must precede page writes and preserve recoverable state."""

import copy
import hashlib
import json
from threading import Event
from unittest.mock import MagicMock

import httpx
import pytest
from notion_client import Client
from notion_client.errors import HTTPResponseError

from src.notion_client import NotionClient
from src.status import STATUS_OPTIONS
from src.status_schema import status_options, status_schema_matches
from src.storage import identity
from src.sync_service import SchemaJob, SyncService


@pytest.fixture
def schema_client(tmp_path, monkeypatch, notion_status_schema):
    monkeypatch.chdir(tmp_path)
    client = NotionClient.__new__(NotionClient)
    client.client = MagicMock()
    client.stop_event = Event()
    client.retry_not_before = 0.0
    client._prepared_status_databases = set()
    monkeypatch.setattr(client.stop_event, "wait", lambda delay: False)
    client.client.databases.retrieve.return_value = copy.deepcopy(notion_status_schema)
    return client


def old_schema(schema):
    old = copy.deepcopy(schema)
    status = old["properties"]["Status"]["status"]
    status["options"].append({"id": "legacy", "name": "obsolete", "color": "yellow"})
    status["groups"][0]["option_ids"].append("legacy")
    return old


def backup_file(tmp_path, database_id="db-a"):
    key = hashlib.sha256(database_id.encode()).hexdigest()
    return tmp_path / "data" / f"notion-status-backup.{key}.json"


def test_matching_schema_is_checked_once_without_mutation(schema_client):
    client = schema_client
    client.ensure_status_schema("db-a")
    client.ensure_status_schema("db-a")
    client.client.databases.retrieve.assert_called_once_with(database_id="db-a")
    client.client.databases.update.assert_not_called()


def test_backup_precedes_mutation_and_original_ids_are_retained(schema_client, notion_status_schema, tmp_path):
    client = schema_client
    before = old_schema(notion_status_schema)
    client.client.databases.retrieve.side_effect = [before, notion_status_schema]
    def update(**kwargs):
        backup = json.loads(backup_file(tmp_path).read_text())
        assert backup["status_property"] == before["properties"]["Status"]
        assert set(kwargs["properties"]) == {"Status"}
        options = kwargs["properties"]["Status"]["status"]["options"]
        assert "legacy" not in {option.get("id") for option in options}
        assert {option["id"] for option in options} == {option["name"] for option in STATUS_OPTIONS}
        return notion_status_schema
    client.client.databases.update.side_effect = update
    client.ensure_status_schema("db-a")
    assert client._prepared_status_databases == {"db-a"}


def test_missing_status_property_is_created(schema_client, notion_status_schema):
    client = schema_client
    client.client.databases.retrieve.side_effect = [{"properties": {}}, notion_status_schema]
    client.ensure_status_schema("db-a")
    properties = client.client.databases.update.call_args.kwargs["properties"]
    assert {option["name"] for option in properties["Status"]["status"]["options"]} == {option["name"] for option in STATUS_OPTIONS}


def test_upgrade_from_six_options_adds_health_and_dead_without_replacing_existing_ids(schema_client, notion_status_schema):
    before = copy.deepcopy(notion_status_schema)
    added = {"starting", "healthy", "unhealthy", "dead"}
    status = before["properties"]["Status"]["status"]
    status["options"] = [option for option in status["options"] if option["name"] not in added]
    for group in status["groups"]:
        group["option_ids"] = [option_id for option_id in group["option_ids"] if option_id not in added]
    schema_client.client.databases.retrieve.side_effect = [before, notion_status_schema]
    schema_client.ensure_status_schema("db-a")
    sent = schema_client.client.databases.update.call_args.kwargs["properties"]["Status"]["status"]["options"]
    assert {option["name"] for option in sent if "name" in option} == added
    assert {option["id"] for option in sent if "id" in option} == {option["name"] for option in status["options"]}


def test_option_color_and_group_changes_are_reconciled(schema_client, notion_status_schema):
    before = copy.deepcopy(notion_status_schema)
    status = before["properties"]["Status"]["status"]
    status["options"][0]["color"] = "yellow"
    status["groups"][0]["option_ids"].remove("created")
    status["groups"][1]["option_ids"].append("created")
    previous = {
        "starting": ("red", "To-do"),
        "running": ("blue", "In progress"),
        "exited": ("green", "Complete"),
    }
    for option in status["options"]:
        if option["name"] in previous:
            color, group_name = previous[option["name"]]
            option["color"] = color
            for group in status["groups"]:
                if option["id"] in group["option_ids"]:
                    group["option_ids"].remove(option["id"])
                if group["name"] == group_name:
                    group["option_ids"].append(option["id"])
    schema_client.client.databases.retrieve.side_effect = [before, notion_status_schema]
    schema_client.ensure_status_schema("db-a")
    sent = schema_client.client.databases.update.call_args.kwargs["properties"]["Status"]["status"]["options"]
    created = next(option for option in sent if option.get("id") == "created")
    assert created == {"id": "created", "color": "red", "group": "To-do"}
    by_id = {option["id"]: option for option in sent}
    assert by_id["starting"] == {"id": "starting", "color": "blue", "group": "In progress"}
    assert by_id["running"] == {"id": "running", "color": "green", "group": "Complete"}
    assert by_id["exited"] == {"id": "exited", "color": "gray", "group": "Complete"}


def test_wrong_property_type_is_not_converted_or_removed(schema_client):
    client = schema_client
    client.client.databases.retrieve.return_value = {"properties": {"Status": {"type": "select"}}}
    with pytest.raises(ValueError, match="must be a status"):
        client.ensure_status_schema("db-a")
    client.client.databases.update.assert_not_called()


def test_custom_group_definitions_require_ui_change_before_mutation(schema_client, notion_status_schema):
    schema = copy.deepcopy(notion_status_schema)
    schema["properties"]["Status"]["status"]["groups"][0]["name"] = "Custom"
    schema_client.client.databases.retrieve.return_value = schema
    with pytest.raises(ValueError, match="Set Status groups"):
        schema_client.ensure_status_schema("db-a")
    schema_client.client.databases.update.assert_not_called()


def test_option_spelling_is_not_silently_renamed_or_deleted(schema_client, notion_status_schema):
    schema = copy.deepcopy(notion_status_schema)
    schema["properties"]["Status"]["status"]["options"][0]["name"] = "Created"
    schema_client.client.databases.retrieve.return_value = schema
    with pytest.raises(ValueError, match="Rename Status option"):
        schema_client.ensure_status_schema("db-a")
    schema_client.client.databases.update.assert_not_called()


@pytest.mark.parametrize("data", ["null", "{", '[]', '{"version":2}', '{"version":1,"database_id":"other"}'])
def test_invalid_backup_prevents_mutation(schema_client, notion_status_schema, tmp_path, data):
    file = backup_file(tmp_path)
    file.parent.mkdir()
    file.write_text(data)
    schema_client.client.databases.retrieve.return_value = old_schema(notion_status_schema)
    with pytest.raises(ValueError):
        schema_client.ensure_status_schema("db-a")
    schema_client.client.databases.update.assert_not_called()
    assert file.read_text() == data


def test_backup_storage_failure_prevents_mutation(schema_client, notion_status_schema, monkeypatch):
    schema_client.client.databases.retrieve.return_value = old_schema(notion_status_schema)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr("src.notion_client.write_json", fail)
    with pytest.raises(OSError, match="disk full"):
        schema_client.ensure_status_schema("db-a")
    schema_client.client.databases.update.assert_not_called()


def test_original_backup_survives_retry_after_update_response_loss(schema_client, notion_status_schema, tmp_path):
    client = schema_client
    before = old_schema(notion_status_schema)
    client.client.databases.retrieve.side_effect = [before, before, notion_status_schema]
    client.client.databases.update.side_effect = httpx.ReadTimeout("lost response")
    with pytest.raises(httpx.ReadTimeout):
        client.ensure_status_schema("db-a")
    saved = backup_file(tmp_path).read_text()
    client.client.databases.update.side_effect = None
    client.ensure_status_schema("db-a")
    assert backup_file(tmp_path).read_text() == saved
    assert json.loads(saved)["status_property"] == before["properties"]["Status"]


def test_ignored_schema_update_is_not_marked_ready(schema_client, notion_status_schema):
    schema_client.client.databases.retrieve.return_value = old_schema(notion_status_schema)
    with pytest.raises(ValueError, match="did not match"):
        schema_client.ensure_status_schema("db-a")
    assert not schema_client._prepared_status_databases


def test_real_pinned_sdk_forwards_group_and_color_and_preserves_other_properties(tmp_path, monkeypatch, notion_status_schema):
    monkeypatch.chdir(tmp_path)
    before = old_schema(notion_status_schema)
    before["properties"]["Name"] = {"type": "title", "title": {}}
    current = [before]
    calls = []
    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.headers["Notion-Version"] == "2022-06-28"
        assert request.url.path == "/v1/databases/db-a"
        if request.method == "GET":
            return httpx.Response(200, json=current[0])
        assert request.method == "PATCH"
        assert backup_file(tmp_path).exists()
        body = json.loads(request.content)
        assert set(body["properties"]) == {"Status"}
        sent = {option["id"]: option for option in body["properties"]["Status"]["status"]["options"]}
        for option in STATUS_OPTIONS:
            assert sent[option["name"]]["color"] == option["color"]
            assert sent[option["name"]]["group"] == option["group"]
        assert "legacy" not in sent
        current[0] = copy.deepcopy(notion_status_schema)
        current[0]["properties"]["Name"] = before["properties"]["Name"]
        return httpx.Response(200, json=current[0])
    client = NotionClient("fake-token")
    client.client.close()
    client.client = Client(client=httpx.Client(transport=httpx.MockTransport(handler)), auth="fake-token")
    try:
        client.ensure_status_schema("db-a")
        assert calls == [("GET", "/v1/databases/db-a"), ("PATCH", "/v1/databases/db-a"), ("GET", "/v1/databases/db-a")]
        assert status_schema_matches(status_options(current[0]["properties"]["Status"]))
    finally:
        client.close()


def test_startup_configures_each_unique_db_even_without_containers(harness):
    h = harness
    h.settings.DB_IDS = {"A": "db-a", "B": "db-b", "Alias": "db-a"}
    service = SyncService(h.docker, h.notion, h.cache, h.settings, h.state, h.stop, lambda: h.now[0])
    service.snapshot_due = float("inf")
    service.tick()
    service.tick()
    assert h.notion.ensure_status_schema.call_count == 2
    assert not service.schema_jobs
    h.notion.update_page.assert_not_called()


def test_schema_failure_defers_pages_without_busy_retry_and_other_db_can_proceed(harness):
    h = harness
    h.service.schema_jobs = {"db-a": SchemaJob()}
    h.service.submit(h.container)
    def ensure(db):
        if db == "db-a":
            raise HTTPResponseError(httpx.Response(503, text="temporary"))
    h.notion.ensure_status_schema.side_effect = ensure
    h.service.tick()
    h.service.tick()
    assert h.service.schema_jobs["db-a"].due == 1030
    assert h.notion.ensure_status_schema.call_count == 1
    assert h.service.wait_seconds() == 1.0
    h.notion.find_page_id.assert_not_called()
    h.notion.create_page.assert_not_called()
    other = h.replace(h.container, name="other", d2n_database="B")
    h.docker.get_container_info.return_value = other
    h.service.submit(other)
    h.service.tick()
    h.notion.update_page.assert_called_once()
    h.now[0] = 1030
    h.notion.ensure_status_schema.side_effect = None
    h.docker.get_container_info.return_value = h.container
    h.service.tick()
    h.service.tick()
    assert identity("db-a", "web") not in h.state.jobs


def test_permanent_schema_error_blocks_pages_until_restart(harness):
    h = harness
    h.settings.DB_IDS = {"A": "db-a"}
    h.service.schema_jobs = {"db-a": SchemaJob()}
    h.service.submit(h.container)
    h.notion.ensure_status_schema.side_effect = ValueError("bad schema")
    h.service.tick()
    h.now[0] = 2000
    h.service.tick()
    assert h.service.schema_jobs["db-a"].blocked
    assert h.notion.ensure_status_schema.call_count == 1
    h.notion.create_page.assert_not_called()
    h.notion.update_page.assert_not_called()
    assert h.service.wait_seconds() == 1.0
    h.notion.ensure_status_schema.side_effect = None
    service = SyncService(h.docker, h.notion, h.cache, h.settings, h.state, h.stop, lambda: h.now[0])
    service.snapshot_due = float("inf")
    service.tick()
    service.tick()
    assert not h.state.jobs


def test_schema_429_honors_worker_cooldown(harness):
    h = harness
    h.service.schema_jobs = {"db-a": SchemaJob()}
    def limit(db):
        h.notion.retry_not_before = 1600
        raise HTTPResponseError(httpx.Response(429, headers={"Retry-After": "600"}, text="limited"))
    h.notion.ensure_status_schema.side_effect = limit
    h.service.tick()
    assert h.service.schema_jobs["db-a"].due == 1600
    h.now[0] = 1500
    h.service.tick()
    assert h.notion.ensure_status_schema.call_count == 1


def test_storage_error_during_schema_initialization_keeps_uncertain_creation_marker(harness):
    h = harness
    h.service.schema_jobs = {"db-a": SchemaJob()}
    h.service.submit(h.container)
    job = h.state.jobs[identity("db-a", "web")]
    job.uncertain = True
    h.state.save()
    h.notion.ensure_status_schema.side_effect = OSError("backup disk full")
    with pytest.raises(OSError):
        h.service.tick()
    from src.sync_state import SyncState
    assert SyncState(h.state.path).jobs[identity("db-a", "web")].uncertain
    h.notion.create_page.assert_not_called()
