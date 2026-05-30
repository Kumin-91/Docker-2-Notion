import time
from typing import Any, Callable, TypeVar, cast
from notion_client import Client
from notion_client.errors import (
    APIErrorCode,
    APIResponseError,
    HTTPResponseError,
    RequestTimeoutError,
)
from src.models import DockerContainerInfo
from src.logger import notion_logger

T = TypeVar("T")

# 재시도 정책
_MAX_RETRIES = 4
_BASE_DELAY = 1.0   # 초
_MAX_DELAY = 30.0   # 초


class PageNotFoundError(Exception):
    """Notion 페이지가 존재하지 않음(수동 삭제 등). 캐시 무효화 후 재생성 신호로 사용."""

    def __init__(self, page_id: str) -> None:
        super().__init__(f"Notion page not found: {page_id}")
        self.page_id = page_id


def _is_retryable(exc: Exception) -> bool:
    """일시적(재시도 가능) 오류인지 판별. 429 또는 5xx, 요청 타임아웃."""
    if isinstance(exc, RequestTimeoutError):
        return True
    if isinstance(exc, HTTPResponseError):
        return exc.status == 429 or exc.status >= 500
    return False


def _rich_text(value: str) -> dict[str, Any]:
    """rich_text 속성 빌더. 빈 값은 빈 배열로 보내 속성을 비웁니다."""
    if not value:
        return {"rich_text": []}
    return {"rich_text": [{"text": {"content": value}}]}


class NotionClient:
    def __init__(self, api_key: str) -> None:
        """Notion 클라이언트 초기화."""
        self.api_key = api_key
        self.client = Client(auth=self.api_key)

        notion_logger.info("Connecting to Notion API...")

        # 연결 테스트 (일시 오류는 재시도, 인증 오류 등은 즉시 실패)
        try:
            self._request_with_retry("users.me", lambda: self.client.users.me())
        except Exception as e:
            raise ConnectionError(f"Unable to connect to Notion API with provided key: {e}")

    def _request_with_retry(self, label: str, func: Callable[[], T]) -> T:
        """Notion API 호출에 지수 백오프 재시도를 적용. Retry-After 헤더를 존중."""
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return func()
            except (HTTPResponseError, RequestTimeoutError) as e:
                if not _is_retryable(e) or attempt == _MAX_RETRIES:
                    raise

                # Retry-After 헤더 우선, 없으면 지수 백오프
                delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
                headers = getattr(e, "headers", None)
                if headers is not None:
                    retry_after = headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = min(float(retry_after), _MAX_DELAY)
                        except ValueError:
                            pass

                status = getattr(e, "status", "?")
                notion_logger.warning(
                    f"{label} failed (status={status}). "
                    f"Retry {attempt + 1}/{_MAX_RETRIES} in {delay:.1f}s..."
                )
                time.sleep(delay)

        # 도달하지 않음 (마지막 시도에서 raise)
        raise RuntimeError("unreachable")

    def _convert_property(self, container: DockerContainerInfo) -> dict[str, Any]:
        """DockerContainerInfo 객체를 Notion 페이지 속성 딕셔너리로 변환.

        - 빈 date(Seen/Created)는 속성 자체를 생략합니다(빈 start는 API 오류).
        - Stacks(multi_select)는 스택이 있을 때만 설정합니다(단독 컨테이너의 수동 입력 보존).
          Notion은 존재하지 않는 옵션 이름을 쓰면 자동으로 옵션을 생성합니다.
        """
        props: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": container.name}}]},
            "Status": {"status": {"name": container.status}},
            "IP": _rich_text(container.ip),
            "Ports": _rich_text(container.port),
            "Image": _rich_text(container.image),
        }
        if container.seen:
            props["Seen"] = {"date": {"start": container.seen}}
        if container.created:
            props["Created"] = {"date": {"start": container.created}}
        if container.stack:
            props["Stacks"] = {"multi_select": [{"name": container.stack}]}
        return props

    def get_database(self, database_id: str) -> dict[str, Any] | None:
        """데이터베이스 정보 조회."""
        notion_logger.debug(f"Retrieving database info for ID: {database_id}")
        try:
            return cast(
                dict[str, Any],
                self._request_with_retry(
                    f"get_database({database_id})",
                    lambda: self.client.databases.retrieve(database_id=database_id),
                ),
            )
        except Exception as e:
            notion_logger.error(f"Error retrieving database {database_id}: {e}")
            return None

    def update_page(self, page_id: str, container: DockerContainerInfo) -> bool:
        """Notion 페이지 업데이트.

        - 성공            -> True
        - 페이지 없음(404) -> PageNotFoundError 발생 (캐시 무효화 후 재생성)
        - 그 외 오류       -> 재시도 후 예외 전파 (호출측에서 skip)
        """
        notion_logger.debug(f"Updating page {page_id} for container: {container.name}")
        data = self._convert_property(container)
        try:
            self._request_with_retry(
                f"update_page({container.name})",
                lambda: self.client.pages.update(page_id=page_id, properties=data),
            )
            return True
        except APIResponseError as e:
            if e.code == APIErrorCode.ObjectNotFound:
                raise PageNotFoundError(page_id) from e
            raise

    def find_page_id(self, database_id: str, container_name: str) -> str:
        """데이터베이스에서 컨테이너 이름으로 페이지 ID 조회. 없거나 오류면 빈 문자열."""
        notion_logger.debug(f"Finding page in database {database_id} for: {container_name}")
        try:
            response = cast(
                dict[str, Any],
                self._request_with_retry(
                    f"find_page_id({container_name})",
                    lambda: self.client.databases.query(
                        database_id=database_id,
                        filter={"property": "Name", "title": {"equals": container_name}},
                    ),
                ),
            )
            results = response.get("results") or []
            if results:
                return str(results[0].get("id", ""))
            return ""
        except Exception as e:
            notion_logger.error(
                f"Error finding page for {container_name} in database {database_id}: {e}"
            )
            return ""

    def create_page(self, database_id: str, container: DockerContainerInfo) -> str:
        """Notion에 새 페이지 생성. 실패 시 빈 문자열."""
        notion_logger.debug(f"Creating new page in database {database_id} for: {container.name}")
        data = self._convert_property(container)
        try:
            page = cast(
                dict[str, Any],
                self._request_with_retry(
                    f"create_page({container.name})",
                    lambda: self.client.pages.create(
                        parent={"database_id": database_id}, properties=data
                    ),
                ),
            )
            return str(page.get("id", ""))
        except Exception as e:
            notion_logger.error(f"Error creating page for {container.name}: {e}")
            return ""
