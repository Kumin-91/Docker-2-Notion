import sys
import signal
from datetime import datetime
from zoneinfo import ZoneInfo
from config.settings import settings
from src.models import DockerContainerInfo
from src.docker_client import DockerClient
from src.notion_client import NotionClient
from src.cache_manager import CacheManager
from src.logger import main_logger

FILTER = {
    'type': 'container',
    'event': [
        'create',      # 생성됨 -> 노션: created
        'start',       # 실행 시작 -> 노션: running
        'stop',        # 중지 요청 -> 노션: exited
        'die',         # 프로세스 종료 -> 노션: exited
        'destroy',     # 컨테이너 삭제 -> 노션: removed
        'restarting',  # 재시작 중 -> 노션: restarting
        'pause',       # 일시 정지 -> 노션: paused
        'unpause'      # 정지 해제 -> 노션: running
    ]
}

def sync_all(docker_client: DockerClient, notion_client: NotionClient, cache_manager: CacheManager) -> None:
    containers = docker_client.list_all_containers()
    main_logger.info(f"Initial sync: Found {len(containers)} containers.")
    
    for container in containers:
        process_update(container, notion_client, cache_manager)

def process_update(container: DockerContainerInfo, notion_client: NotionClient, cache_manager: CacheManager) -> None:
    """
    컨테이너 정보를 Notion 페이지에 동기화합니다.
    캐시를 활용하며, 업데이트 실패 시 캐시를 무효화하고 재시도합니다.
    """
    # 0. d2n.enabled 태그가 false인 경우 무시
    if container.d2n_enabled is False:
        main_logger.info(f"Skipping container {container.name} as d2n.enabled is set to false.")
        return
    d2n_db_id = settings.DB_IDS.get(container.d2n_database) or settings.DEFAULT_DB_ID

    # 1. 캐시 확인 (이름 기준)
    page_id = cache_manager.get_page_id(container.name)

    # 캐시가 있다면 우선 업데이트 시도
    if page_id:
        if notion_client.update_page(page_id, container):
            main_logger.info(f"Updated existing page for {container.name} (ID: {page_id})")
            return
        
        # 업데이트 실패 (페이지 삭제됨 등) -> 캐시 무효화
        main_logger.warning(f"Failed to update page {page_id} for {container.name}. Invalidating cache and retrying...")
        cache_manager.remove_page_id(container.name)
        page_id = None

    # 2. 캐시가 없거나 유효하지 않은 경우: Notion 검색 또는 생성 로직 진입
    if not page_id:
        # Notion 검색 (이름 기준)
        main_logger.info(f"Searching Notion for existing page: {container.name}")
        page_id = notion_client.find_page_id(d2n_db_id, container.name)
        
        if page_id:
            # 찾았으면 캐시에 저장 후 업데이트
            main_logger.info(f"Found existing page {page_id} for {container.name}. Updating cache.")
            cache_manager.set_page_id(container.name, page_id)
            if notion_client.update_page(page_id, container):
                main_logger.info(f"Updated found page {page_id} for {container.name}")
        else:
            # 검색해도 없으면 새로 생성
            main_logger.info(f"No existing page found for {container.name}, creating new page...")
            page_id = notion_client.create_page(d2n_db_id, container)
            if page_id:
                main_logger.info(f"Created new page {page_id} for {container.name}")
                cache_manager.set_page_id(container.name, page_id)
            else:
                main_logger.error(f"Failed to create page for {container.name}")

def main():
    stop_event = False

    def signal_handler(sig, frame):
        nonlocal stop_event
        sig_name = signal.Signals(sig).name
        main_logger.info(f"Received signal {sig_name}. Initiating graceful shutdown...")
        stop_event = True
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # DockerClient 인스턴스 생성
    docker_client = DockerClient(settings.DOCKER_API_URL)

    # NotionClient 인스턴스 생성
    notion_client = NotionClient(settings.NOTION_API_KEY)

    # CacheManager 인스턴스 생성
    cache_manager = CacheManager()

    # 초기 동기화 실행
    sync_all(docker_client, notion_client, cache_manager)

    try:
        for event in docker_client.monitor_changes(filters=FILTER):
            # 0. 이벤트에서 컨테이너 정보 가져오기
            action = event.get("Action")
            actor = event.get("Actor", {})
            actor_attributes = actor.get("Attributes", {})
            container_id = event.get("id") or actor.get("ID")
            container_name = (event.get("name") or actor_attributes.get("name", "")).lstrip("/")

            main_logger.info(f"Detected event: {action} for container Name: {container_name}")

            # 1. destroy 전용 처리
            if action == "destroy":
                d2n_enabled = actor_attributes.get('d2n.enabled', 'FALSE').upper() == 'TRUE'
                d2n_database = actor_attributes.get('d2n.database', settings.DEFAULT_DB_ID)

                if not d2n_enabled: continue

                removed_info = DockerContainerInfo(
                    container_id=container_id,
                    name=container_name,
                    status="removed",
                    seen=datetime.now(ZoneInfo(settings.TIMEZONE)).isoformat(),
                    ip="",
                    port="",
                    d2n_enabled=d2n_enabled,
                    d2n_database=d2n_database
                )
                
                process_update(removed_info, notion_client, cache_manager)
                cache_manager.remove_page_id(container_name)
                continue

           # 2. 그 외 이벤트 처리 (create, start, stop, die)
            if not container_id: continue

            container_info = docker_client.get_container_info(container_id)
            if container_info is None: continue

            process_update(container_info, notion_client, cache_manager)

    except (KeyboardInterrupt, SystemExit):
        main_logger.info("Shutting down gracefully...")
    except Exception as e:
        main_logger.error(f"Unexpected error: {e}")
    finally:
        docker_client.disconnect()
        main_logger.info("Cleanup complete. Exiting.")

if __name__ == "__main__":
    main()