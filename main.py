import sys
import time
import signal
from typing import Any, Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from config.settings import load_settings, Settings
from src.models import DockerContainerInfo
from src.status import NotionStatus
from src.docker_client import DockerClient
from src.notion_client import NotionClient, PageNotFoundError
from src.cache_manager import CacheManager
from src.logger import main_logger

FILTER = {
    "type": "container",
    "event": [
        "create",      # 생성됨 -> 노션: created
        "start",       # 실행 시작 -> 노션: running
        "stop",        # 중지 요청 -> 노션: exited
        "die",         # 프로세스 종료 -> 노션: exited
        "destroy",     # 컨테이너 삭제 -> 노션: removed
        "restart",     # 재시작 -> 노션: running (재시작 루프면 inspect가 restarting 반환)
        "pause",       # 일시 정지 -> 노션: paused
        "unpause",     # 정지 해제 -> 노션: running
    ],
}

# Docker 재연결 백오프 (초)
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 30.0


def sync_all(
    docker_client: DockerClient,
    notion_client: NotionClient,
    cache_manager: CacheManager,
    settings: Settings,
) -> None:
    containers = docker_client.list_all_containers()
    main_logger.info(f"Initial sync: Found {len(containers)} containers.")

    for container in containers:
        process_update(container, notion_client, cache_manager, settings)


def process_update(
    container: DockerContainerInfo,
    notion_client: NotionClient,
    cache_manager: CacheManager,
    settings: Settings,
) -> None:
    """컨테이너 정보를 Notion 페이지에 동기화.

    캐시를 활용하며, 페이지가 실제로 삭제된 경우(404)에만 캐시를 무효화하고
    재탐색/재생성합니다. 일시적 오류는 캐시를 유지한 채 건너뜁니다.
    """
    # 0. d2n.enabled 라벨이 false면 무시
    if container.d2n_enabled is False:
        main_logger.info(f"Skipping container {container.name} as d2n.enabled is set to false.")
        return

    d2n_db_id = settings.resolve_db_id(container.d2n_database)

    # 1. 캐시 확인 (이름 기준)
    page_id = cache_manager.get_page_id(container.name)
    if page_id:
        try:
            notion_client.update_page(page_id, container)
            main_logger.info(f"Updated existing page for {container.name} (ID: {page_id})")
            return
        except PageNotFoundError:
            # 페이지가 실제로 삭제됨 -> 캐시 무효화 후 재생성
            main_logger.warning(
                f"Page {page_id} for {container.name} not found. Invalidating cache and retrying..."
            )
            cache_manager.remove_page_id(container.name)
            page_id = None
        except Exception as e:
            # 일시 오류 등 -> 캐시 유지하고 건너뜀 (중복 생성 방지)
            main_logger.error(
                f"Failed to update page {page_id} for {container.name}: {e}. Skipping (cache kept)."
            )
            return

    # 2. 캐시 미스: Notion 검색 후 업데이트, 없으면 생성
    main_logger.info(f"Searching Notion for existing page: {container.name}")
    page_id = notion_client.find_page_id(d2n_db_id, container.name)

    if page_id:
        main_logger.info(f"Found existing page {page_id} for {container.name}. Updating cache.")
        cache_manager.set_page_id(container.name, page_id)
        try:
            notion_client.update_page(page_id, container)
            main_logger.info(f"Updated found page {page_id} for {container.name}")
            return
        except PageNotFoundError:
            # 방금 찾았으나 사라진 드문 경우 -> 생성으로 폴백
            cache_manager.remove_page_id(container.name)
            page_id = ""
        except Exception as e:
            main_logger.error(f"Failed to update found page {page_id} for {container.name}: {e}")
            return

    if not page_id:
        main_logger.info(f"No existing page found for {container.name}, creating new page...")
        new_id = notion_client.create_page(d2n_db_id, container)
        if new_id:
            main_logger.info(f"Created new page {new_id} for {container.name}")
            cache_manager.set_page_id(container.name, new_id)
        else:
            main_logger.error(f"Failed to create page for {container.name}")


def handle_event(
    event: dict[str, Any],
    docker_client: DockerClient,
    notion_client: NotionClient,
    cache_manager: CacheManager,
    settings: Settings,
) -> None:
    """단일 Docker 이벤트를 처리."""
    action = event.get("Action")
    actor = event.get("Actor", {})
    actor_attributes = actor.get("Attributes", {})
    container_id = event.get("id") or actor.get("ID")
    container_name = (event.get("name") or actor_attributes.get("name", "")).lstrip("/")

    main_logger.info(f"Detected event: {action} for container Name: {container_name}")

    # 1. destroy 전용 처리 (컨테이너가 사라져 inspect 불가 -> 라벨로 구성)
    if action == "destroy":
        d2n_enabled = actor_attributes.get("d2n.enabled", "FALSE").upper() == "TRUE"
        if not d2n_enabled:
            return

        removed_info = DockerContainerInfo(
            container_id=container_id or "",
            name=container_name,
            status=NotionStatus.REMOVED,
            seen=datetime.now(ZoneInfo(settings.TIMEZONE)).isoformat(),
            ip="",
            port="",
            image=actor_attributes.get("image", ""),
            created="",
            stack=(
                actor_attributes.get("com.docker.compose.project")
                or actor_attributes.get("com.docker.stack.namespace")
                or ""
            ),
            d2n_enabled=d2n_enabled,
            d2n_database=actor_attributes.get("d2n.database", ""),
        )

        process_update(removed_info, notion_client, cache_manager, settings)
        cache_manager.remove_page_id(container_name)
        return

    # 2. 그 외 이벤트 처리 (create, start, stop, die, ...)
    if not container_id:
        return

    container_info = docker_client.get_container_info(container_id)
    if container_info is None:
        return

    process_update(container_info, notion_client, cache_manager, settings)


def run_event_loop(
    docker_client: DockerClient,
    notion_client: NotionClient,
    cache_manager: CacheManager,
    settings: Settings,
    should_stop: Callable[[], bool],
) -> None:
    """이벤트 스트림을 소비하며, 연결이 끊기면 백오프 후 자동 재연결한다.

    (재)연결 직후에는 sync_all로 전체 상태를 다시 맞춰 끊긴 동안 놓친 변화를 보정한다.
    """
    backoff = _INITIAL_BACKOFF

    while not should_stop():
        try:
            if not docker_client.ping():
                raise ConnectionError("Docker daemon not reachable")

            # 연결 직후 전체 동기화 (초기 실행 + 재연결 후 보정)
            sync_all(docker_client, notion_client, cache_manager, settings)
            backoff = _INITIAL_BACKOFF

            for event in docker_client.monitor_changes(filters=FILTER):
                if should_stop():
                    return
                handle_event(event, docker_client, notion_client, cache_manager, settings)

            main_logger.warning("Docker event stream ended.")
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            main_logger.error(f"Docker connection lost: {e}")

        if should_stop():
            return

        main_logger.info(f"Reconnecting to Docker daemon in {backoff:.1f}s...")
        time.sleep(backoff)
        docker_client.reconnect()
        backoff = min(backoff * 2, _MAX_BACKOFF)


def main() -> None:
    stop_event = False

    def signal_handler(sig: int, frame: Any) -> None:
        nonlocal stop_event
        sig_name = signal.Signals(sig).name
        main_logger.info(f"Received signal {sig_name}. Initiating graceful shutdown...")
        stop_event = True
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    settings = load_settings()
    docker_client = DockerClient(settings)
    notion_client = NotionClient(settings.NOTION_API_KEY)
    cache_manager = CacheManager()

    try:
        run_event_loop(docker_client, notion_client, cache_manager, settings, lambda: stop_event)
    except (KeyboardInterrupt, SystemExit):
        main_logger.info("Shutting down gracefully...")
    except Exception as e:
        main_logger.error(f"Unexpected error: {e}")
    finally:
        docker_client.disconnect()
        main_logger.info("Cleanup complete. Exiting.")


if __name__ == "__main__":
    main()
