from dataclasses import replace
from queue import Queue

import httpx
import pytest
from notion_client.errors import HTTPResponseError
from requests.exceptions import ConnectionError as DockerConnectionError

from main import run_event_loop
from src.docker_client import DockerSnapshot
from src.notion_client import AmbiguousCreateError, DuplicatePagesError, PageNotFoundError
from src.storage import identity
from src.sync_service import SyncService
from src.sync_state import ManagedContainer, SyncState


def error(status=503, retry_after=None):
    return HTTPResponseError(httpx.Response(status, text="error", headers={} if retry_after is None else {"Retry-After": retry_after}))


def restart(h):
    state = SyncState(h.state.path)
    service = SyncService(h.docker, h.notion, h.cache, h.settings, state, h.stop, lambda: h.now[0])
    service.snapshot_due = float("inf")
    h.state, h.service = state, service


def test_failed_search_never_creates(harness):
    h = harness
    h.notion.find_page_id.side_effect = error()
    h.service.submit(h.container)
    h.service.tick()
    h.notion.create_page.assert_not_called()
    job = h.state.jobs[identity("db-a", "web")]
    assert job.attempts == 1 and job.due == 1030


def test_permanent_search_error_blocks_until_restart(harness):
    h = harness
    h.notion.find_page_id.side_effect = error(401)
    h.service.submit(h.container)
    h.service.tick()
    assert h.state.jobs[identity("db-a", "web")].blocked
    h.now[0] = 2000
    h.service.tick()
    assert h.notion.find_page_id.call_count == 1
    h.notion.find_page_id.side_effect = None
    restart(h)
    h.service.tick()
    assert not h.state.jobs


def test_target_change_never_uses_old_database_cache(harness):
    h = harness
    h.cache.set_page_id("db-a", "web", "old-page")
    new = replace(h.container, d2n_database="B")
    h.docker.get_container_info.return_value = new
    h.notion.find_page_id.return_value = "page-b"
    h.service.submit(new)
    h.service.tick()
    h.notion.find_page_id.assert_called_once_with("db-b", "web")
    h.notion.update_page.assert_called_once_with("page-b", new)
    assert h.cache.get_page_id("db-a", "web") == "old-page"


def test_alias_uses_same_database_cache(harness):
    h = harness
    h.cache.set_page_id("db-a", "web", "page-a")
    new = replace(h.container, d2n_database="Alias")
    h.docker.get_container_info.return_value = new
    h.service.submit(new)
    h.service.tick()
    h.notion.find_page_id.assert_not_called()
    h.notion.update_page.assert_called_once_with("page-a", new)


def test_update_failure_preserves_cache_and_retries(harness):
    h = harness
    h.cache.set_page_id("db-a", "web", "page-a")
    h.notion.update_page.side_effect = error()
    h.service.submit(h.container)
    h.service.tick()
    assert h.cache.get_page_id("db-a", "web") == "page-a"
    h.notion.find_page_id.assert_not_called()
    h.notion.create_page.assert_not_called()
    h.notion.update_page.side_effect = None
    h.now[0] = 1030
    h.service.tick()
    assert not h.state.jobs


def test_cached_404_searches_then_creates_once(harness):
    h = harness
    h.cache.set_page_id("db-a", "web", "deleted")
    h.notion.update_page.side_effect = PageNotFoundError("deleted")
    h.notion.find_page_id.return_value = None
    h.service.submit(h.container)
    h.service.tick()
    h.notion.create_page.assert_called_once_with("db-a", h.container)
    assert h.cache.get_page_id("db-a", "web") == "new-page"


def test_found_page_disappears_then_retries_safely(harness):
    h = harness
    h.notion.update_page.side_effect = PageNotFoundError("page-a")
    h.service.submit(h.container)
    h.service.tick()
    assert h.cache.get_page_id("db-a", "web") is None
    h.notion.create_page.assert_not_called()
    assert h.state.jobs[identity("db-a", "web")].due == 1030


def test_unknown_creation_survives_restart_and_searches_without_creating_again(harness):
    h = harness
    h.notion.find_page_id.return_value = None
    h.notion.create_page.side_effect = AmbiguousCreateError("response lost")
    h.service.submit(h.container)
    h.service.tick()
    assert SyncState(h.state.path).jobs[identity("db-a", "web")].uncertain
    restart(h)
    h.now[0] = 1030
    h.service.tick()
    assert h.notion.create_page.call_count == 1
    assert h.state.jobs[identity("db-a", "web")].due == 1090
    h.notion.find_page_id.return_value = "accepted-page"
    h.now[0] = 1090
    h.service.tick()
    assert not h.state.jobs
    assert h.cache.get_page_id("db-a", "web") == "accepted-page"
    assert h.notion.create_page.call_count == 1


def test_creation_marker_written_before_network_request_even_on_crash(harness):
    h = harness
    h.notion.find_page_id.return_value = None
    def crash(*args):
        persisted = SyncState(h.state.path)
        assert persisted.jobs[identity("db-a", "web")].uncertain
        raise KeyboardInterrupt
    h.notion.create_page.side_effect = crash
    h.service.submit(h.container)
    with pytest.raises(KeyboardInterrupt):
        h.service.tick()
    restart(h)
    h.notion.create_page.side_effect = None
    h.service.tick()
    assert h.notion.create_page.call_count == 1


def test_new_event_preserves_unknown_creation_marker_and_backoff(harness):
    h = harness
    h.notion.find_page_id.return_value = None
    h.notion.create_page.side_effect = AmbiguousCreateError("lost")
    h.service.submit(h.container)
    h.service.tick()
    h.service.submit(replace(h.container, container_id="replacement", status="exited"))
    job = h.state.jobs[identity("db-a", "web")]
    assert job.uncertain and job.due == 1030 and job.container.container_id == "replacement"


@pytest.mark.parametrize("status", [400, 429])
def test_rejected_creation_clears_marker(harness, status):
    h = harness
    h.notion.find_page_id.return_value = None
    h.notion.create_page.side_effect = error(status)
    h.service.submit(h.container)
    h.service.tick()
    job = h.state.jobs[identity("db-a", "web")]
    assert not job.uncertain
    assert bool(job.blocked) == (status == 400)


def test_backoff_grows_and_caps_without_real_sleep(harness):
    h = harness
    h.notion.find_page_id.side_effect = error()
    h.service.submit(h.container)
    for delay in [30, 60, 120, 240, 300, 300]:
        h.service.tick()
        job = h.state.jobs[identity("db-a", "web")]
        assert job.due == h.now[0] + delay
        h.now[0] = job.due


def test_long_retry_after_controls_schedule(harness):
    h = harness
    h.notion.find_page_id.side_effect = error(429, "600")
    h.service.submit(h.container)
    h.service.tick()
    assert h.state.jobs[identity("db-a", "web")].due == 1600


def test_global_cooldown_defers_other_containers(harness):
    h = harness
    h.notion.retry_not_before = 1120
    h.service.submit(h.container)
    h.service.submit(replace(h.container, name="db"))
    h.service.tick()
    h.notion.find_page_id.assert_not_called()
    assert h.service.wait_seconds() == 1
    h.now[0] = 1120
    h.service.tick()
    assert h.notion.find_page_id.call_count == 1


def test_delayed_destroy_cannot_overwrite_recreated_container(harness):
    h = harness
    current = replace(h.container, container_id="new-id", status="running")
    h.docker.get_container_info.return_value = current
    h.service.submit(replace(h.container, status="removed"))
    h.service.tick()
    h.notion.update_page.assert_called_once_with("page-a", current)


def test_failed_running_update_retries_latest_exited_state(harness):
    h = harness
    h.notion.find_page_id.side_effect = error()
    h.service.submit(h.container)
    h.service.tick()
    current = replace(h.container, status="exited")
    h.docker.get_container_info.return_value = current
    h.notion.find_page_id.side_effect = None
    h.now[0] = 1030
    h.service.tick()
    h.notion.update_page.assert_called_once_with("page-a", current)


def test_missing_container_updates_removed_without_creating_new_page(harness):
    h = harness
    h.docker.get_container_info.return_value = None
    h.service.submit(h.container)
    h.service.tick()
    updated = h.notion.update_page.call_args.args[1]
    assert updated.status == "removed" and updated.ip == updated.port == ""
    h.notion.create_page.assert_not_called()


def test_missing_container_and_page_does_not_create_tombstone(harness):
    h = harness
    h.docker.get_container_info.return_value = None
    h.notion.find_page_id.return_value = None
    h.service.submit(h.container)
    h.service.tick()
    h.notion.create_page.assert_not_called()
    assert not h.state.jobs


def test_docker_connection_failure_is_not_container_removal(harness):
    h = harness
    h.docker.get_container_info.side_effect = DockerConnectionError("offline")
    h.service.submit(h.container)
    h.service.tick()
    h.notion.find_page_id.assert_not_called()
    h.notion.update_page.assert_not_called()
    assert h.state.jobs[identity("db-a", "web")].due == 1030


def test_pending_work_recovers_after_restart(harness):
    h = harness
    h.notion.find_page_id.side_effect = error()
    h.service.submit(h.container)
    h.service.tick()
    restart(h)
    assert h.state.jobs[identity("db-a", "web")].due == 1030
    h.notion.find_page_id.side_effect = None
    h.now[0] = 1030
    h.service.tick()
    assert not h.state.jobs
    assert SyncState(h.state.path).managed[identity("db-a", "web")].page_id == "page-a"


def test_offline_deletion_is_reconciled_from_persistent_history(harness):
    h = harness
    h.state.managed[identity("db-a", "web")] = ManagedContainer("db-a", h.container, "page-a")
    h.state.save()
    restart(h)
    h.docker.snapshot.return_value = DockerSnapshot(set(), [])
    h.docker.get_container_info.return_value = None
    h.service.request_snapshot()
    h.service.tick()
    assert h.notion.update_page.call_args.args[1].status == "removed"
    assert h.state.managed[identity("db-a", "web")].container.status == "removed"


def test_failed_snapshot_never_marks_history_removed(harness):
    h = harness
    h.state.managed[identity("db-a", "web")] = ManagedContainer("db-a", h.container, "page-a")
    h.docker.snapshot.side_effect = DockerConnectionError("partial read")
    h.service.request_snapshot()
    h.service.tick()
    h.notion.update_page.assert_not_called()
    assert h.state.managed[identity("db-a", "web")].container.status == "running"
    assert h.service.snapshot_due == 1030


def test_snapshot_inspect_race_does_not_mark_listed_id_removed(harness):
    h = harness
    h.state.managed[identity("db-a", "web")] = ManagedContainer("db-a", h.container, "page-a")
    h.docker.snapshot.return_value = DockerSnapshot({h.container.container_id}, [])
    h.service.reconcile()
    assert not h.state.jobs


def test_database_switch_preserves_previous_page(harness):
    h = harness
    h.state.managed[identity("db-a", "web")] = ManagedContainer("db-a", h.container, "page-a")
    current = replace(h.container, container_id="new-id", d2n_database="B")
    h.docker.snapshot.return_value = DockerSnapshot({current.container_id}, [current])
    h.service.reconcile()
    assert identity("db-a", "web") not in h.state.jobs
    assert identity("db-b", "web") in h.state.jobs


def test_offline_removal_uses_recorded_db_even_after_mapping_change(harness):
    h = harness
    h.state.managed[identity("db-a", "web")] = ManagedContainer("db-a", h.container, "page-a")
    h.settings.resolve_db_id = lambda name: "db-b"
    h.docker.snapshot.return_value = DockerSnapshot(set(), [])
    h.docker.get_container_info.return_value = None
    h.service.reconcile()
    h.service.tick()
    h.notion.find_page_id.assert_called_once_with("db-a", "web")


def test_events_disabled_by_label_are_ignored(harness):
    h = harness
    h.service.submit_event({"Action": "start", "Actor": {"ID": "one", "Attributes": {"name": "web"}}})
    assert not h.state.jobs


def test_destroy_event_is_persisted_and_db_scoped(harness):
    h = harness
    h.service.submit_event({"Action": "destroy", "Actor": {"ID": "one", "Attributes": {
        "name": "/web", "d2n.enabled": "TRUE", "d2n.database": "B", "image": "nginx"}}})
    job = SyncState(h.state.path).jobs[identity("db-b", "web")]
    assert job.container.status == "removed" and job.container.name == "web"


def test_duplicate_pages_block_without_creation(harness):
    h = harness
    h.notion.find_page_id.side_effect = DuplicatePagesError("duplicates")
    h.service.submit(h.container)
    h.service.tick()
    h.notion.create_page.assert_not_called()
    assert h.state.jobs[identity("db-a", "web")].blocked


def test_event_loop_processes_due_work_without_any_docker_event(harness):
    h = harness
    h.service.submit(h.container)
    h.notion.update_page.side_effect = lambda *args: h.stop.set()
    run_event_loop(h.service, Queue(), h.stop)
    h.notion.update_page.assert_called_once()
    assert not h.state.jobs


def test_shutdown_keeps_pending_work(harness):
    h = harness
    h.service.submit(h.container)
    h.stop.set()
    h.service.tick()
    h.notion.find_page_id.assert_not_called()
    assert SyncState(h.state.path).jobs


def test_uncertain_marker_survives_route_change(harness):
    h = harness
    h.notion.find_page_id.return_value = None
    h.notion.create_page.side_effect = AmbiguousCreateError("lost")
    h.service.submit(h.container)
    h.service.tick()
    h.docker.get_container_info.return_value = replace(h.container, d2n_database="B")
    h.now[0] = 1030
    h.service.tick()
    assert h.state.jobs[identity("db-a", "web")].uncertain
    assert h.notion.create_page.call_count == 1


def test_initial_snapshot_transient_failure_retries_without_reconnect(harness):
    h = harness
    h.docker.snapshot.side_effect = [DockerConnectionError("offline"), DockerSnapshot({h.container.container_id}, [h.container])]
    h.service.request_snapshot()
    h.service.tick()
    assert h.service.snapshot_due == 1030
    h.now[0] = 1030
    h.service.tick()
    assert h.docker.snapshot.call_count == 2
    h.notion.update_page.assert_called_once()


def test_disabled_current_container_is_not_updated(harness):
    h = harness
    h.docker.get_container_info.return_value = replace(h.container, d2n_enabled=False)
    h.service.submit(h.container)
    h.service.tick()
    h.notion.find_page_id.assert_not_called()
    assert not h.state.jobs


def test_regular_job_is_cancelled_if_route_changed_before_retry(harness):
    h = harness
    h.service.submit(h.container)
    h.docker.get_container_info.return_value = replace(h.container, d2n_database="B")
    h.service.tick()
    h.notion.find_page_id.assert_not_called()
    assert not h.state.jobs


def test_storage_failure_before_creation_fails_closed(harness, monkeypatch):
    h = harness
    h.notion.find_page_id.return_value = None
    h.service.submit(h.container)
    monkeypatch.setattr(h.state, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        h.service.tick()
    h.notion.create_page.assert_not_called()


def test_deleted_page_of_absent_container_is_not_recreated(harness):
    h = harness
    h.cache.set_page_id("db-a", "web", "old-page")
    h.notion.update_page.side_effect = PageNotFoundError("old-page")
    h.notion.find_page_id.return_value = None
    h.docker.get_container_info.return_value = None
    h.service.submit(replace(h.container, status="removed"))
    h.service.tick()
    h.notion.create_page.assert_not_called()
    assert not h.state.jobs


def test_repeated_events_coalesce_to_single_job(harness):
    h = harness
    for status in ["created", "running", "exited", "running"]:
        h.service.submit(replace(h.container, status=status))
    assert len(h.state.jobs) == 1
    h.service.tick()
    h.notion.update_page.assert_called_once()


def test_removed_history_does_not_repeat_on_every_reconciliation(harness):
    h = harness
    h.state.managed[identity("db-a", "web")] = ManagedContainer("db-a", replace(h.container, status="removed"), "page-a")
    h.docker.snapshot.return_value = DockerSnapshot(set(), [])
    h.service.reconcile()
    assert not h.state.jobs


def test_slow_failed_snapshot_backs_off_from_failure_time(harness):
    h = harness
    def slow_failure():
        h.now[0] += 45
        raise DockerConnectionError("slow inspect failed")
    h.docker.snapshot.side_effect = slow_failure
    h.service.request_snapshot()
    h.service.tick()
    assert h.now[0] == 1045
    assert h.service.snapshot_due == 1075
    h.service.tick()
    assert h.docker.snapshot.call_count == 1
