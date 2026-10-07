"""Single-owner Notion synchronizer with durable, coalesced retries."""

import time
from dataclasses import replace
from datetime import datetime
from threading import Event
from typing import Any, Callable
from zoneinfo import ZoneInfo

from docker.errors import DockerException
from requests.exceptions import RequestException
from notion_client.errors import HTTPResponseError

from config.settings import Settings
from src.cache_manager import CacheManager
from src.docker_client import DockerClient
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
        # Operator fixes to credentials/schema are reevaluated after restart.
        for job in state.jobs.values():
            if job.blocked is not None:
                job.blocked = None
                job.due = self.clock()

    def submit(self, container: DockerContainerInfo, database_id: str | None = None) -> None:
        if not container.d2n_enabled:
            return
        db = normalize_database_id(database_id or self.settings.resolve_db_id(container.d2n_database))
        key = identity(db, container.name)
        job = self.state.jobs.get(key)
        if job is None:
            self.state.jobs[key] = SyncJob(db, container, due=self.clock())
        else:
            # Preserve backoff and unknown creation results across newer events.
            job.container = container
        self.state.save()

    def submit_event(self, event: dict[str, Any]) -> None:
        attrs = event.get("Actor", {}).get("Attributes", {})
        if str(attrs.get("d2n.enabled", "false")).upper() != "TRUE":
            return
        name = str(event.get("name") or attrs.get("name", "")).lstrip("/")
        container_id = str(event.get("id") or event.get("Actor", {}).get("ID") or "")
        if not name or not container_id:
            return
        removed = event.get("Action") == "destroy"
        self.submit(DockerContainerInfo(
            container_id=container_id, name=name,
            status=NotionStatus.REMOVED if removed else NotionStatus.CREATED,
            seen=datetime.now(ZoneInfo(self.settings.TIMEZONE)).isoformat(),
            ip="", port="", image=str(attrs.get("image", "")), created="",
            stack=str(attrs.get("com.docker.compose.project")
                      or attrs.get("com.docker.stack.namespace") or ""),
            d2n_enabled=True, d2n_database=str(attrs.get("d2n.database", "")),
        ))

    def request_snapshot(self) -> None:
        self.snapshot_due = self.clock()

    def reconcile(self) -> None:
        # snapshot() raises on partial/failed reads: never infer removals then.
        snapshot = self.docker.snapshot()
        live_keys = set()
        live_names = {container.name for container in snapshot.containers}
        for container in snapshot.containers:
            if container.d2n_enabled:
                db = self.settings.resolve_db_id(container.d2n_database)
                live_keys.add(identity(db, container.name))
                self.submit(container)
        for key, entry in list(self.state.managed.items()):
            if (
                key not in live_keys
                and entry.container.container_id not in snapshot.container_ids
                and entry.container.name not in live_names
                and entry.container.status != NotionStatus.REMOVED
            ):
                self.submit(
                    replace(entry.container, status=NotionStatus.REMOVED, ip="", port=""),
                    database_id=entry.database_id,
                )

    def _refresh(self, job: SyncJob) -> DockerContainerInfo | None:
        current = self.docker.get_container_info(job.container.name)
        if current is not None:
            if not current.d2n_enabled:
                return None
            db = normalize_database_id(self.settings.resolve_db_id(current.d2n_database))
            if db != job.database_id:
                return None  # Preserve the old DB's page on routing changes.
            return current  # Also replaces delayed destroy events for older IDs.
        previous = self.state.managed.get(identity(job.database_id, job.container.name))
        container = previous.container if previous is not None else job.container
        return replace(
            container, status=NotionStatus.REMOVED, ip="", port="",
            seen=datetime.now(ZoneInfo(self.settings.TIMEZONE)).isoformat(),
        )

    def _sync(self, job: SyncJob) -> str | None:
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

    def _run_job(self, key: str, job: SyncJob) -> None:
        try:
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
                delay = min(INITIAL_RETRY * 2 ** min(self.snapshot_attempts, 4), MAX_RETRY)
                self.snapshot_attempts += 1
                self.snapshot_due = self.clock() + delay
                main_logger.warning(f"Docker snapshot deferred {delay:.1f}s: {exc}")
        # A full listing can take time; cooldown and job deadlines must use the
        # time after the listing finishes, rather than its start time.
        now = self.clock()
        if now < self.notion.retry_not_before:
            return
        due = [(key, job) for key, job in self.state.jobs.items()
               if job.blocked is None and job.due <= now]
        if due:
            key, job = min(due, key=lambda item: item[1].due)
            self._run_job(key, job)

    def wait_seconds(self) -> float:
        now = self.clock()
        times = [self.snapshot_due]
        times.extend(max(job.due, self.notion.retry_not_before)
                     for job in self.state.jobs.values() if job.blocked is None)
        return max(0.0, min(1.0, min(times) - now))
