import time
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Event
import httpx
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


class AmbiguousCreateError(Exception):
    """Creation may have succeeded; search again without issuing another create."""


class DuplicatePagesError(Exception):
    """Multiple matching pages need review; never select one arbitrarily."""


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (RequestTimeoutError, httpx.TransportError)):
        return True
    if isinstance(exc, HTTPResponseError):
        return exc.status in (408, 429) or exc.status >= 500
    return False


def retry_after_seconds(exc: Exception) -> float:
    headers = getattr(exc, "headers", None)
    value = headers.get("Retry-After") if headers is not None else None
    if value is None:
        return 0.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0


def _rich_text(value: str) -> dict[str, Any]:
    """rich_text 속성 빌더. 빈 값은 빈 배열로 보내 속성을 비웁니다."""
    if not value:
        return {"rich_text": []}
    return {"rich_text": [{"text": {"content": value}}]}


class NotionClient:
    def __init__(self, api_key: str, stop_event: Event | None = None) -> None:
        self.client = Client(auth=api_key, timeout_ms=5000)
        self.stop_event = stop_event or Event()
        self.retry_not_before = 0.0
        # Authentication is checked by actual requests, so transient startup
        # failures use the same durable scheduler as failures during operation.

    def close(self) -> None:
        self.client.close()

    def _request_with_retry(self, label: str, func: Callable[[], T]) -> T:
        for attempt in range(_MAX_RETRIES + 1):
            if self.stop_event.is_set():
                raise InterruptedError("Shutdown requested")
            try:
                return func()
            except (HTTPResponseError, RequestTimeoutError, httpx.TransportError) as exc:
                retry_after = retry_after_seconds(exc)
                if getattr(exc, "status", None) == 429:
                    self.retry_not_before = max(
                        self.retry_not_before, time.time() + max(1.0, retry_after)
                    )
                    raise  # Worker-wide cooldown; do not block other event intake.
                if not _is_retryable(exc) or attempt == _MAX_RETRIES:
                    raise
                if retry_after > _MAX_DELAY:
                    raise  # Long waits belong to the scheduler.
                delay = max(min(_BASE_DELAY * 2 ** attempt, _MAX_DELAY), retry_after)
                notion_logger.warning(f"{label}: retry {attempt + 1}/{_MAX_RETRIES} in {delay:.1f}s")
                if self.stop_event.wait(delay):
                    raise InterruptedError("Shutdown requested") from exc
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

    def find_page_id(self, database_id: str, container_name: str) -> str | None:
        """None only means a successful, empty search. All failures propagate."""
        response = self._request_with_retry(
            f"find_page_id({container_name})",
            lambda: self.client.databases.query(
                database_id=database_id,
                filter={"property": "Name", "title": {"equals": container_name}},
                page_size=2,
            ),
        )
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise ValueError("Malformed Notion search response")
        results = response["results"]
        if len(results) > 1 or response.get("has_more"):
            raise DuplicatePagesError(f"Multiple pages for {container_name} in {database_id}")
        if not results:
            return None
        page_id = results[0].get("id")
        if not isinstance(page_id, str) or not page_id:
            raise ValueError("Missing page ID in Notion search response")
        return page_id

    def create_page(self, database_id: str, container: DockerContainerInfo) -> str:
        """Send creation once; uncertain responses must be reconciled by search."""
        if self.stop_event.is_set():
            raise InterruptedError("Shutdown requested")
        try:
            page = self.client.pages.create(
                parent={"database_id": database_id},
                properties=self._convert_property(container),
            )
            page_id = page.get("id") if isinstance(page, dict) else None
            if not isinstance(page_id, str) or not page_id:
                raise ValueError("Missing page ID in creation response")
            return page_id
        except HTTPResponseError as exc:
            if exc.status < 500 and exc.status != 408:
                if exc.status == 429:
                    self.retry_not_before = max(
                        self.retry_not_before,
                        time.time() + max(1.0, retry_after_seconds(exc)),
                    )
                raise  # Explicit rejection: no page was accepted.
            raise AmbiguousCreateError(str(exc)) from exc
        except Exception as exc:
            raise AmbiguousCreateError(str(exc)) from exc
