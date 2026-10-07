import signal
from queue import Empty, Queue
from threading import Event
from typing import Any

from config.settings import load_settings
from src.cache_manager import CacheManager
from src.docker_client import DockerClient
from src.event_reader import DockerEventReader, StreamMessage
from src.logger import main_logger
from src.notion_client import NotionClient
from src.sync_service import SyncService
from src.sync_state import SyncState


def run_event_loop(service: SyncService, inbox: Queue[StreamMessage], stop_event: Event) -> None:
    """Timers fire even when Docker emits no events; drain bounded batches fairly."""
    while not stop_event.is_set():
        try:
            message = inbox.get(timeout=service.wait_seconds())
        except Empty:
            service.tick()
            continue
        messages = [message]
        for _ in range(99):
            try:
                messages.append(inbox.get_nowait())
            except Empty:
                break
        for message in messages:
            if message.event is None:
                service.request_snapshot()
            else:
                service.submit_event(message.event)
        service.tick()


def main() -> None:
    stop_event = Event()

    def signal_handler(sig: int, frame: Any) -> None:
        main_logger.info(f"Received {signal.Signals(sig).name}; saving pending work and stopping")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    settings = load_settings()
    # Load durable markers before connecting: corrupt state must fail closed.
    state = SyncState()
    cache = CacheManager()
    docker = DockerClient(settings, connect=False, stop_event=stop_event)
    event_docker = DockerClient(settings, connect=False, stop_event=stop_event)
    notion = NotionClient(settings.NOTION_API_KEY, stop_event)
    reader = DockerEventReader(event_docker, Queue(), stop_event)
    service = SyncService(docker, notion, cache, settings, state, stop_event)
    try:
        reader.start()
        run_event_loop(service, reader.inbox, stop_event)
    finally:
        stop_event.set()
        try:
            reader.close()
        except Exception as exc:
            main_logger.warning(f"Event reader cleanup failed: {exc}")
        finally:
            if reader.ident is not None:
                reader.join(timeout=5)
            try:
                state.save()
            finally:
                try:
                    docker.disconnect()
                finally:
                    notion.close()
        main_logger.info("Cleanup complete")


if __name__ == "__main__":
    main()
