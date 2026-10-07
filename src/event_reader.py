"""Docker stream intake runs independently of Notion requests and retry timers."""

from dataclasses import dataclass
from queue import Queue
from threading import Event, Thread
from typing import Any

from src.docker_client import DockerClient
from src.logger import docker_logger

FILTER = {
    "type": "container",
    "event": ["create", "start", "stop", "die", "destroy", "restart", "pause", "unpause"],
    "label": "d2n.enabled",
}


@dataclass
class StreamMessage:
    event: dict[str, Any] | None = None  # None requests a reconciliation after connection.


class DockerEventReader(Thread):
    def __init__(self, docker: DockerClient, inbox: Queue[StreamMessage], stop_event: Event) -> None:
        super().__init__(name="docker-events", daemon=True)
        self.docker = docker
        self.inbox = inbox
        self.stop_event = stop_event

    def run(self) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                if not self.docker.reconnect():
                    raise ConnectionError("Docker daemon is unavailable")
                stream = self.docker.monitor_changes(filters=FILTER)
                # Open the stream before requesting the snapshot so changes
                # during a slow initial sync are queued instead of being lost.
                self.inbox.put(StreamMessage())
                backoff = 1.0
                for event in stream:
                    if self.stop_event.is_set():
                        break
                    self.inbox.put(StreamMessage(event))
            except Exception as exc:
                if not self.stop_event.is_set():
                    docker_logger.warning(f"Docker event connection interrupted: {exc}")
            finally:
                try:
                    self.docker.disconnect()
                except Exception as exc:
                    docker_logger.warning(f"Docker disconnect failed: {exc}")
            if self.stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)

    def close(self) -> None:
        self.stop_event.set()
        self.docker.disconnect()  # Closing the stream interrupts its blocking read.
