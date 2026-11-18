"""Schema migration must precede page writes and preserve recoverable state."""

import copy
import hashlib
import json

import httpx
import pytest

from src.status import STATUS_OPTIONS
from src.status_schema import status_options, status_schema_matches


@pytest.fixture
def schema_client(tmp_path, monkeypatch, notion_status_schema, mock_notion):
    monkeypatch.chdir(tmp_path)
    client = mock_notion
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


def test_backup_storage_failure_prevents_mutation(schema_client, notion_status_schema, monkeypatch, tmp_path):
    schema_client.client.databases.retrieve.return_value = old_schema(notion_status_schema)
    (tmp_path / "data").mkdir()
    def fail(directory):
        raise OSError("directory sync failed")
    monkeypatch.setattr("src.storage.fsync_directory", fail)
    monkeypatch.setattr("src.notion_client.fsync_directory", fail)
    for _ in range(2):
        with pytest.raises(OSError, match="directory sync failed"):
            schema_client.ensure_status_schema("db-a")
        assert backup_file(tmp_path).exists()
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


def test_real_pinned_sdk_forwards_group_and_color_and_preserves_other_properties(tmp_path, monkeypatch, notion_status_schema, notion_transport):
    monkeypatch.chdir(tmp_path)
    before = old_schema(notion_status_schema)
    before["properties"]["Name"] = {"type": "title", "title": {}}
    names_by_id = {
        option["id"]: option["name"]
        for option in before["properties"]["Status"]["status"]["options"]
    }
    expected = {
        "created": ("red", "To-do"),
        "dead": ("red", "To-do"),
        "starting": ("blue", "In progress"),
        "restarting": ("blue", "In progress"),
        "paused": ("blue", "In progress"),
        "unhealthy": ("blue", "In progress"),
        "running": ("green", "Complete"),
        "healthy": ("green", "Complete"),
        "exited": ("gray", "Complete"),
        "removed": ("gray", "Complete"),
    }
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
        sent = body["properties"]["Status"]["status"]["options"]
        assert len(sent) == len(expected)
        assert {names_by_id[option["id"]]: (option["color"], option["group"]) for option in sent} == expected
        current[0] = copy.deepcopy(notion_status_schema)
        current[0]["properties"]["Name"] = before["properties"]["Name"]
        return httpx.Response(200, json=current[0])
    client = notion_transport(handler)
    client.ensure_status_schema("db-a")
    assert calls == [("GET", "/v1/databases/db-a"), ("PATCH", "/v1/databases/db-a"), ("GET", "/v1/databases/db-a")]
    assert status_schema_matches(status_options(current[0]["properties"]["Status"]))
