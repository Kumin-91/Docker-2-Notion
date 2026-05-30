from enum import StrEnum

"""
Docker 컨테이너 상태를 Notion Status 속성 옵션 값으로 정규화합니다.

Notion DB의 Status 옵션은 README 기준 아래 6종을 사용합니다.
매핑되지 않은 Docker 상태(예: dead, removing)가 Notion에 그대로 전달되면
옵션이 없어 업데이트가 실패하므로 여기서 한 곳에 모아 변환합니다.
"""


class NotionStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    RESTARTING = "restarting"
    PAUSED = "paused"
    EXITED = "exited"
    REMOVED = "removed"


# Docker 상태(container.status) -> Notion Status 옵션 값
_DOCKER_TO_NOTION: dict[str, str] = {
    "created": NotionStatus.CREATED,
    "running": NotionStatus.RUNNING,
    "restarting": NotionStatus.RESTARTING,
    "paused": NotionStatus.PAUSED,
    "exited": NotionStatus.EXITED,
    "dead": NotionStatus.EXITED,       # 비정상 종료도 exited로 취급
    "removing": NotionStatus.REMOVED,  # 삭제 진행 중
    "removed": NotionStatus.REMOVED,
}


def normalize_status(docker_status: str) -> str:
    """Docker 상태 문자열을 Notion Status 옵션 값으로 정규화.

    매핑에 없는 값은 소문자 원본을 그대로 반환합니다(향후 신규 상태 대비).
    """
    key = (docker_status or "").lower()
    return _DOCKER_TO_NOTION.get(key, key)
