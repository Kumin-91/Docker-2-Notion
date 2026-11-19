"""Single-owner Notion synchronizer with durable, coalesced retries."""

import time
from dataclasses import dataclass, replace
from datetime import datetime
from threading import Event
from typing import Any, Callable
from zoneinfo import ZoneInfo

from docker.errors import DockerException
from requests.exceptions import RequestException
from notion_client.errors import HTTPResponseError

from config.settings import Settings
from src.cache_manager import CacheManager
from src.docker_client import (
    DockerClient, compose_replacement, compose_temporary_target, is_compose_temporary_name,
)
from src.logger import main_logger
from src.models import DockerContainerInfo
from src.notion_client import (
    AmbiguousCreateError, NotionClient, PageNotFoundError,
    _is_retryable, retry_after_seconds,
)
from src.status import NotionStatus
from src.storage import identity, normalize_database_id
from src.sync_state import ManagedContainer, SyncJob, SyncState

INITIAL_RETRY = 30.0
MAX_RETRY = 300.0


@dataclass
class SchemaJob:
    due: float = 0.0
    attempts: int = 0
    blocked: str | None = None


class SyncService:
    def __init__(
        self, docker: DockerClient, notion: NotionClient, cache: CacheManager,
        settings: Settings, state: SyncState, stop_event: Event,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.docker = docker
        self.notion = notion
        self.cache = cache
        self.settings = settings
        self.state = state
        self.stop_event = stop_event
        self.clock = clock
        self.snapshot_due = 0.0
        self.snapshot_attempts = 0
        self.schema_jobs = {
            normalize_database_id(db): SchemaJob()
            for db in getattr(settings, "DB_IDS", {}).values()
        }
        # Operator fixes to credentials/schema are reevaluated after restart.
        for job in state.jobs.values():
            if job.blocked is not None:
                job.blocked = None
                job.due = self.clock()

    def submit(self, container: DockerContainerInfo, database_id: str | None = None) -> None:
        if not container.d2n_enabled or container.compose_temporary:
            return
        db = normalize_database_id(database_id or self.settings.resolve_db_id(container.d2n_database))
        key = identity(db, container.name)
        job = self.state.jobs.get(key)
        if job is not None and job.cleanup_page_id is not None:
            if container.status == NotionStatus.REMOVED:
                return  # Keep the already verified, page-specific removal.
            job = None  # The name is back in use, so the normal job takes over.
        if job is None:
            self.state.jobs[key] = SyncJob(db, container, due=self.clock())
        else:
            # Preserve backoff and unknown creation results across newer events.
            job.container = container
        self.state.save()

    def submit_event(self, event: dict[str, Any]) -> None:
        actor = event.get("Actor")
        if not isinstance(actor, dict) or not isinstance(attrs := actor.get("Attributes"), dict):
            return
        event_type = event.get("Type", "container")
        if event_type == "network":
            if event.get("Action") in ("connect", "disconnect"):
                self._submit_network_change(attrs.get("container"))
            return
        if event_type != "container":
            return
        if str(attrs.get("d2n.enabled", "false")).upper() != "TRUE":
            return
        name = str(event.get("name") or attrs.get("name", "")).lstrip("/")
        container_id = str(event.get("id") or actor.get("ID") or "")
        if not name or not container_id:
            return
        action = event.get("Action")
        if action == "rename":
            old_name = attrs.get("oldName")
            if isinstance(old_name, str):
                self._submit_compose_rename(container_id, old_name.lstrip("/"), name, attrs)
            return
        _, temporary = compose_replacement(name, attrs)
        if temporary:
            return
        removed = action == "destroy"
        self.submit(DockerContainerInfo(
            container_id=container_id, name=name,
            status=NotionStatus.REMOVED if removed else NotionStatus.CREATED,
            seen=datetime.now(ZoneInfo(self.settings.TIMEZONE)).isoformat(),
            ip="", port="", image=str(attrs.get("image", "")), created="",
            stack=str(attrs.get("com.docker.compose.project")
                      or attrs.get("com.docker.stack.namespace") or ""),
            d2n_enabled=True, d2n_database=str(attrs.get("d2n.database", "")),
        ))

    def _submit_network_change(self, container_id: Any) -> None:
        # A network event's actor ID identifies the network, not its container.
        if not isinstance(container_id, str) or not container_id.strip():
            return
        container = self._inspect_event_container(container_id)
        if container is not None:
            self.submit(container)

    def _inspect_event_container(self, container_id: str) -> DockerContainerInfo | None:
        try:
            return self.docker.get_container_info(container_id)
        except (DockerException, RequestException, ConnectionError) as exc:
            if self.snapshot_due == float("inf"):
                self._defer_snapshot(exc)
            return None

    def _submit_compose_rename(
        self, container_id: str, old_name: str, name: str, attrs: dict[str, Any],
    ) -> None:
        _, temporary = compose_replacement(old_name, attrs)
        if not temporary or compose_temporary_target(old_name) != name:
            return
        current = self._inspect_event_container(container_id)
        if (
            current is None or current.name != name or current.container_id != container_id
            or not current.d2n_enabled or not current.compose_replace_label or current.compose_temporary
        ):
            return
        self.submit(current)
        self._schedule_compose_cleanup(old_name, current)

    def _schedule_compose_cleanup(self, old_name: str, current: DockerContainerInfo) -> None:
        if (
            not current.d2n_enabled or current.compose_temporary
            or not is_compose_temporary_name(old_name, current.name, current.compose_replace_label)
        ):
            return
        db = normalize_database_id(self.settings.resolve_db_id(current.d2n_database))
        key = identity(db, old_name)
        entry = self.state.managed.get(key)
        final_entry = self.state.managed.get(identity(db, current.name))
        if (
            entry is None or entry.container.container_id != current.container_id
            or entry.container.status == NotionStatus.REMOVED
            or (final_entry is not None and final_entry.page_id == entry.page_id)
        ):
            return
        job = self.state.jobs.get(key)
        if job is not None and (job.uncertain or job.cleanup_page_id is not None):
            return
        removed = replace(entry.container, status=NotionStatus.REMOVED)
        if job is None:
            self.state.jobs[key] = SyncJob(db, removed, due=self.clock(), cleanup_page_id=entry.page_id)
        else:
            job.container = removed
            job.cleanup_page_id = entry.page_id
        self.state.save()

    def request_snapshot(self) -> None:
        self.snapshot_due = self.clock()

    def _defer_snapshot(self, exc: Exception) -> None:
        delay = min(INITIAL_RETRY * 2 ** min(self.snapshot_attempts, 4), MAX_RETRY)
        self.snapshot_attempts += 1
        self.snapshot_due = self.clock() + delay
        main_logger.warning(f"Docker snapshot deferred {delay:.1f}s: {exc}")

    def reconcile(self) -> None:
        # snapshot() raises on partial/failed reads: never infer removals then.
        snapshot = self.docker.snapshot()
        live_by_id = {container.container_id: container for container in snapshot.containers}
        live_names = {container.name for container in snapshot.containers}
        for container in snapshot.containers:
            self.submit(container)
        for entry in self.state.managed.values():
            previous = entry.container
            if previous.name in live_names or previous.status == NotionStatus.REMOVED:
                continue
            current = live_by_id.get(previous.container_id)
            if current is not None:
                self._schedule_compose_cleanup(previous.name, current)
            elif previous.container_id not in snapshot.container_ids:
                self.submit(
                    replace(previous, status=NotionStatus.REMOVED, ip="", port=""),
                    database_id=entry.database_id,
                )

    def _refresh(self, job: SyncJob) -> DockerContainerInfo | None:
        name = job.container.name
        current = self.docker.get_container_info(name)
        if current is not None:
            if not current.d2n_enabled or current.compose_temporary:
                return None
            db = normalize_database_id(self.settings.resolve_db_id(current.d2n_database))
            if db != job.database_id:
                return None  # Preserve the old DB's page on routing changes.
            return current  # Also replaces delayed destroy events for older IDs.
        # A job queued by an older release can outlive the temporary Docker name.
        if compose_temporary_target(name):
            renamed = self.docker.get_container_info(job.container.container_id)
            if (
                renamed is not None and not renamed.compose_temporary
                and renamed.container_id == job.container.container_id
                and is_compose_temporary_name(name, renamed.name, renamed.compose_replace_label)
            ):
                return None
        previous = self.state.managed.get(identity(job.database_id, name))
        container = previous.container if previous is not None else job.container
        return replace(
            container, status=NotionStatus.REMOVED, ip="", port="",
            seen=datetime.now(ZoneInfo(self.settings.TIMEZONE)).isoformat(),
        )

    def _sync(self, job: SyncJob) -> str | None:
        # Also covers durable history pointing to a previously configured DB.
        self.notion.ensure_status_schema(job.database_id)
        container = job.container
        page_id = self.cache.get_page_id(job.database_id, container.name)
        if page_id is not None:
            try:
                self.notion.update_page(page_id, container)
                return page_id
            except PageNotFoundError:
                self.cache.remove_page_id(job.database_id, container.name)
        # Search exceptions propagate. Only a successful empty result permits create.
        page_id = self.notion.find_page_id(job.database_id, container.name)
        if page_id is not None:
            self.cache.set_page_id(job.database_id, container.name, page_id)
            try:
                self.notion.update_page(page_id, container)
            except PageNotFoundError:
                self.cache.remove_page_id(job.database_id, container.name)
                raise
            return page_id
        if job.uncertain:
            raise AmbiguousCreateError(
                f"Creation result for {container.name} remains unknown; review required if search stays empty"
            )
        if container.status == NotionStatus.REMOVED:
            return None  # Do not create a new page for an already absent container.
        # Persist BEFORE the request. Even a process crash cannot lose the marker.
        job.uncertain = True
        self.state.save()
        try:
            page_id = self.notion.create_page(job.database_id, container)
        except HTTPResponseError:
            job.uncertain = False  # Explicit rejection; safe to retry after correction/cooldown.
            self.state.save()
            raise
        self.cache.set_page_id(job.database_id, container.name, page_id)
        return page_id

    def _run_schema(self, database_id: str, job: SchemaJob) -> None:
        try:
            self.notion.ensure_status_schema(database_id)
            del self.schema_jobs[database_id]
        except InterruptedError:
            return
        except Exception as exc:
            if isinstance(exc, OSError) and not _is_retryable(exc):
                raise
            if _is_retryable(exc):
                delay = max(
                    min(INITIAL_RETRY * 2 ** min(job.attempts, 4), MAX_RETRY),
                    retry_after_seconds(exc),
                )
                job.attempts += 1
                job.due = self.clock() + delay
                main_logger.warning(f"Status configuration for DB {database_id} deferred {delay:.1f}s: {exc}")
            else:
                job.blocked = str(exc)
                main_logger.error(f"Status configuration for DB {database_id} blocked; correct and restart: {exc}")

    def _run_compose_cleanup(self, key: str, job: SyncJob, page_id: str) -> None:
        entry = self.state.managed.get(key)
        name = job.container.name
        if (
            entry is not None and entry.page_id == page_id
            and entry.container.container_id == job.container.container_id
            and self.docker.get_container_info(name) is None
        ):
            self.notion.ensure_status_schema(job.database_id)
            if self.notion.mark_removed_if_matches(page_id, job.database_id, name):
                self.state.managed[key] = replace(entry, container=job.container)
            else:
                del self.state.managed[key]
                if self.cache.get_page_id(job.database_id, name) == page_id:
                    self.cache.remove_page_id(job.database_id, name)
                main_logger.info(f"Temporary page for {name} no longer matches its recorded identity")
        del self.state.jobs[key]
        self.state.save()

    def _run_job(self, key: str, job: SyncJob) -> None:
        try:
            if job.cleanup_page_id is not None:
                self._run_compose_cleanup(key, job, job.cleanup_page_id)
                return
            current = self._refresh(job)
            if current is None and not job.uncertain:
                del self.state.jobs[key]
                self.state.save()
                return
            if current is None:
                # An old creation marker must survive route/label changes.
                page_id = self.notion.find_page_id(job.database_id, job.container.name)
                if page_id is None:
                    raise AmbiguousCreateError("Previous creation still unconfirmed")
                self.cache.set_page_id(job.database_id, job.container.name, page_id)
            else:
                job.container = current
                page_id = self._sync(job)
            if current is not None and page_id is not None:
                self.state.managed[key] = ManagedContainer(job.database_id, current, page_id)
            elif current is not None:
                self.state.managed.pop(key, None)
            del self.state.jobs[key]
            self.state.save()
            main_logger.info(f"Synced {job.container.name} to DB {job.database_id}")
        except InterruptedError:
            self.state.save()
        except Exception as exc:
            retryable = (
                isinstance(exc, (AmbiguousCreateError, PageNotFoundError, DockerException, RequestException, ConnectionError))
                or _is_retryable(exc)
            )
            if isinstance(exc, OSError) and not retryable:
                raise  # Storage failures must not discard unknown creation results.
            if retryable:
                delay = min(INITIAL_RETRY * 2 ** min(job.attempts, 4), MAX_RETRY)
                delay = max(delay, retry_after_seconds(exc))
                job.attempts += 1
                job.due = self.clock() + delay
                main_logger.warning(f"Sync {job.container.name} deferred {delay:.1f}s: {exc}")
            else:
                job.blocked = str(exc)
                main_logger.error(f"Sync {job.container.name} blocked; correct configuration and restart: {exc}")
            self.state.save()

    def tick(self) -> None:
        if self.stop_event.is_set():
            return
        now = self.clock()
        if now >= self.snapshot_due:
            try:
                self.reconcile()
                self.snapshot_attempts = 0
                self.snapshot_due = float("inf")
            except InterruptedError:
                return
            except (DockerException, RequestException, ConnectionError) as exc:
                self._defer_snapshot(exc)
        # A full listing can take time; cooldown and job deadlines must use the
        # time after the listing finishes, rather than its start time.
        now = self.clock()
        if now < self.notion.retry_not_before:
            return
        schemas_due = [(db, job) for db, job in self.schema_jobs.items()
                       if job.blocked is None and job.due <= now]
        if schemas_due:
            db, schema_job = min(schemas_due, key=lambda item: item[1].due)
            self._run_schema(db, schema_job)
            return
        due = [(key, job) for key, job in self.state.jobs.items()
               if job.blocked is None and job.due <= now
               and job.database_id not in self.schema_jobs]
        if due:
            key, job = min(due, key=lambda item: item[1].due)
            self._run_job(key, job)

    def wait_seconds(self) -> float:
        now = self.clock()
        times = [self.snapshot_due]
        times.extend(max(job.due, self.notion.retry_not_before)
                     for job in self.schema_jobs.values() if job.blocked is None)
        times.extend(max(job.due, self.notion.retry_not_before)
                     for job in self.state.jobs.values()
                     if job.blocked is None and job.database_id not in self.schema_jobs)
        return max(0.0, min(1.0, min(times) - now))
