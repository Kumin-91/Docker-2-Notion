"""Docker health probe: worker progress plus a read-only Docker ping.

Health files are ephemeral and separate from durable synchronization state.
Monotonic timestamps avoid false failures when the system clock changes.
"""

import math
import os
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from src.storage import read_json, write_json

FAILURE_GRACE_SECONDS = 180.0
HEARTBEAT_INTERVAL_SECONDS = 5.0


def health_path() -> Path:
    return Path(os.getenv("D2N_HEALTH_FILE", "/tmp/d2n-health.json"))


def _failure_path(path: Path) -> Path:
    return path.with_name(path.name + ".docker-failure")


def _timestamp(value: Any) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


class HealthReporter:
    """Only the synchronization loop may advance its progress timestamp."""

    def __init__(
        self, docker_api_url: str, path: str | Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path) if path is not None else health_path()
        self.docker_api_url = docker_api_url
        self.clock = clock
        self.instance = uuid4().hex
        self.last_write: float | None = None

    def beat(self) -> None:
        now = self.clock()
        if self.last_write is not None and now - self.last_write < HEARTBEAT_INTERVAL_SECONDS:
            return
        if self.last_write is None:
            # A process/container restart gets its own failure window.
            _failure_path(self.path).unlink(missing_ok=True)
        write_json(str(self.path), {
            "version": 1, "instance": self.instance, "pid": os.getpid(),
            "loop_at": now, "docker_api_url": self.docker_api_url,
        })
        self.last_write = now

    def close(self) -> None:
        self.path.unlink(missing_ok=True)
        _failure_path(self.path).unlink(missing_ok=True)


def _ping_docker(docker_api_url: str) -> bool:
    # No settings/Notion/logging initialization in probe mode. These requests
    # only negotiate the API version and call Docker's read-only ping endpoint.
    from docker import DockerClient

    try:
        with closing(DockerClient(base_url=docker_api_url, timeout=3, version="auto")) as client:
            return bool(client.ping())
    except Exception:
        return False


def check_health(
    path: str | Path | None = None, clock: Callable[[], float] = time.monotonic,
    ping: Callable[[str], bool] = _ping_docker,
) -> int:
    """Exit 0 during brief Docker outages; exit 1 after 180 seconds."""
    target = Path(path) if path is not None else health_path()
    try:
        report = read_json(str(target))
        now = clock()
        if (
            not isinstance(report, dict) or report.get("version") != 1
            or not isinstance(report.get("instance"), str) or not report["instance"]
            or type(report.get("pid")) is not int or report["pid"] <= 0
            or not _timestamp(report.get("loop_at")) or report["loop_at"] > now
            or not isinstance(report.get("docker_api_url"), str) or not report["docker_api_url"]
        ):
            print("unhealthy: worker heartbeat missing or invalid")
            return 1
        try:
            os.kill(report["pid"], 0)
        except OSError:
            print("unhealthy: worker process is not running")
            return 1
        if now - report["loop_at"] >= FAILURE_GRACE_SECONDS:
            print("unhealthy: worker made no progress for 180 seconds")
            return 1
        failure_file = _failure_path(target)
        if ping(report["docker_api_url"]):
            failure_file.unlink(missing_ok=True)
            print("healthy: worker progressing, Docker reachable")
            return 0
        failure = read_json(str(failure_file))
        if failure is None or (
            isinstance(failure, dict) and failure.get("instance") != report["instance"]
        ):
            failure = {"instance": report["instance"], "since": now}
            write_json(str(failure_file), failure)
        if (
            not isinstance(failure, dict) or not _timestamp(failure.get("since"))
            or failure["since"] > now
        ):
            print("unhealthy: Docker failure history invalid")
            return 1
        elapsed = now - failure["since"]
        if elapsed >= FAILURE_GRACE_SECONDS:
            print("unhealthy: Docker unreachable for at least 180 seconds")
            return 1
        print(f"healthy: Docker outage within grace period ({elapsed:.0f}/180 seconds)")
        return 0
    except (OSError, ValueError, TypeError, OverflowError):
        print("unhealthy: health state cannot be read or written")
        return 1
