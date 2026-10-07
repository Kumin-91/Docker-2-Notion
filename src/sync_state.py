"""Durable work and inventory, independent of the expiring page-ID cache."""

import math
from dataclasses import asdict, dataclass, fields
from typing import Any
from pathlib import Path

from src.models import DockerContainerInfo
from src.storage import identity, read_json, write_json


@dataclass
class SyncJob:
    database_id: str
    container: DockerContainerInfo
    due: float = 0.0
    attempts: int = 0
    uncertain: bool = False
    blocked: str | None = None


@dataclass
class ManagedContainer:
    database_id: str
    container: DockerContainerInfo
    page_id: str


def _container(value: Any) -> DockerContainerInfo:
    if not isinstance(value, dict):
        raise ValueError("Invalid container in sync state")
    for field in fields(DockerContainerInfo):
        expected = bool if field.name == "d2n_enabled" else str
        if not isinstance(value.get(field.name), expected):
            raise ValueError(f"Invalid container field: {field.name}")
    return DockerContainerInfo(**value)


class SyncState:
    def __init__(self, path: str = "data/sync-state.v1.json") -> None:
        self.path = path
        self.jobs: dict[str, SyncJob] = {}
        self.managed: dict[str, ManagedContainer] = {}
        raw = read_json(path)
        if raw is None and not Path(path).exists():
            return
        # Never silently discard creation markers or management history.
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise ValueError(f"Unsupported/corrupt sync state: {path}; restore a valid backup")
        for collection in ("jobs", "managed"):
            if not isinstance(raw.get(collection), dict):
                raise ValueError(f"Invalid {collection} in sync state")
        for key, value in raw["jobs"].items():
            if not isinstance(value, dict):
                raise ValueError("Invalid sync job")
            container = _container(value.get("container"))
            db = value.get("database_id")
            due, attempts = value.get("due"), value.get("attempts")
            if (
                not isinstance(db, str) or not db or key != identity(db, container.name)
                or not isinstance(due, (float, int)) or not math.isfinite(due) or due < 0
                or type(attempts) is not int or attempts < 0
                or type(value.get("uncertain")) is not bool
                or (value.get("blocked") is not None and not isinstance(value["blocked"], str))
            ):
                raise ValueError("Invalid sync job fields")
            self.jobs[key] = SyncJob(**{**value, "container": container})
        for key, value in raw["managed"].items():
            if not isinstance(value, dict):
                raise ValueError("Invalid managed container")
            container = _container(value.get("container"))
            db, page_id = value.get("database_id"), value.get("page_id")
            if (
                not isinstance(db, str) or not db or key != identity(db, container.name)
                or not isinstance(page_id, str) or not page_id
            ):
                raise ValueError("Invalid management history")
            self.managed[key] = ManagedContainer(db, container, page_id)

    def save(self) -> None:
        write_json(self.path, {
            "version": 1,
            "jobs": {key: asdict(job) for key, job in self.jobs.items()},
            "managed": {key: asdict(entry) for key, entry in self.managed.items()},
        })
