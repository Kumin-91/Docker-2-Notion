"""Schema startup scheduling, isolation, and recovery before page writes."""

from dataclasses import replace

import httpx
import pytest
from notion_client.errors import HTTPResponseError

from src.storage import identity
from src.sync_service import SchemaJob, SyncService
from src.sync_state import SyncState


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
    other = replace(h.container, name="other", d2n_database="B")
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
    assert SyncState(h.state.path).jobs[identity("db-a", "web")].uncertain
    h.notion.create_page.assert_not_called()
