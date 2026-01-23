from typing import Any, Dict, Optional, cast
from notion_client import Client
from src.models import DockerContainerInfo
from src.logger import notion_logger

class NotionClient:
    def __init__(self, notion_api_url: str) -> None:
        """Notion 클라이언트 초기화"""
        self.notion_api_url = notion_api_url
        self.client = Client(auth=self.notion_api_url)

        notion_logger.info("Connecting to Notion API...")

        # 연결 테스트
        try:
            self.client.users.me()
        except Exception as e:
            raise ConnectionError(f"Unable to connect to Notion API with provided key: {e}")
        
    def _convert_property(self, container: DockerContainerInfo) -> dict:
        """DockerContainerInfo 객체를 Notion 페이지 속성 딕셔너리로 변환"""
        return {
            "Name": {"title": [{"text": {"content": container.name}}]},
            "Status": {"status": {"name": container.status}},
            "Seen": {"date": {"start": container.seen}},
            "IP": {"rich_text": [{"text": {"content": container.ip}}]},
            "Ports": {"rich_text": [{"text": {"content": container.port}}]}
        }
        
    def get_database(self, database_id: str) -> Optional[Dict[str, Any]]:
        """데이터베이스 정보 조회"""
        notion_logger.debug(f"Retrieving database info for ID: {database_id}")
        try:
            return cast(Dict[str, Any], self.client.databases.retrieve(database_id=database_id))
        except Exception as e:
            notion_logger.error(f"Error retrieving database {database_id}: {e}")
            return None
        
    def update_page(self, page_id: str, container: DockerContainerInfo) -> bool:
        """Notion 페이지 업데이트"""
        notion_logger.debug(f"Updating page with ID: {page_id} for container: {container.name}")
        data = self._convert_property(container)
        try:
            self.client.pages.update(page_id=page_id, properties=data)
            return True
        except Exception as e:
            notion_logger.error(f"Error updating page {page_id} for container {container.name}: {e}")
            return False
        
    def find_page_id(self, database_id: str, container_name: str) -> str:
        """데이터베이스에서 컨테이너 이름으로 페이지 ID 조회"""
        notion_logger.debug(f"Finding page ID in database {database_id} for container: {container_name}")
        try:
            response = cast(Dict[str, Any], self.client.databases.query(
                database_id=database_id,
                filter={
                    "property": "Name",
                    "title": {
                        "equals": container_name
                    }
                }
            ))
            if response.get("results"):
                # mypy thinks results might be list or something else, but we know it's list of dicts
                results = cast(list[Dict[str, Any]], response.get("results"))
                if results and isinstance(results, list) and len(results) > 0:
                     return str(results[0].get("id", ""))
            return ""
        except Exception as e:
            notion_logger.error(f"Error finding page for container {container_name} in database {database_id}: {e}")
            return ""

    def create_page(self, database_id: str, container: DockerContainerInfo) -> str:
        """Notion에 새 페이지 생성"""
        notion_logger.debug(f"Creating new page in database {database_id} for container: {container.name}")
        data = self._convert_property(container)
        try:
            page = cast(Dict[str, Any], self.client.pages.create(parent={"database_id": database_id}, properties=data))
            return str(page.get("id", ""))
        except Exception as e:
            notion_logger.error(f"Error creating page for {container.name}: {e}")
            return ""