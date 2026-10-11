"""Docker 컨테이너 상태를 Notion Status 속성 옵션 값으로 정규화합니다.

Notion DB의 Status 옵션은 실행 시 아래 목록으로 맞춥니다.
매핑되지 않은 Docker 상태가 Notion에 그대로 전달되면
옵션이 없어 업데이트가 실패하므로 여기서 한 곳에 모아 변환합니다.
"""

from enum import StrEnum


class NotionStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    RESTARTING = "restarting"
    PAUSED = "paused"
    EXITED = "exited"
    REMOVED = "removed"
    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEAD = "dead"


# Option colors follow the group's requested color, except exited/removed (gray).
# Group definitions themselves are configured in the Notion UI.
STATUS_OPTIONS: tuple[dict[str, str], ...] = (
    {"name": "created", "color": "red", "group": "To-do"},
    {"name": "dead", "color": "red", "group": "To-do"},
    {"name": "starting", "color": "blue", "group": "In progress"},
    {"name": "restarting", "color": "blue", "group": "In progress"},
    {"name": "paused", "color": "blue", "group": "In progress"},
    {"name": "unhealthy", "color": "blue", "group": "In progress"},
    {"name": "running", "color": "green", "group": "Complete"},
    {"name": "healthy", "color": "green", "group": "Complete"},
    {"name": "exited", "color": "gray", "group": "Complete"},
    {"name": "removed", "color": "gray", "group": "Complete"},
)


# Docker 상태(container.status) -> Notion Status 옵션 값
_DOCKER_TO_NOTION: dict[str, str] = {
    "created": NotionStatus.CREATED,
    "running": NotionStatus.RUNNING,
    "restarting": NotionStatus.RESTARTING,
    "paused": NotionStatus.PAUSED,
    "exited": NotionStatus.EXITED,
    "dead": NotionStatus.DEAD,
    "removing": NotionStatus.REMOVED,  # 삭제 진행 중
    "removed": NotionStatus.REMOVED,
}


def normalize_status(docker_status: str, health_status: str = "") -> str:
    """Docker 상태 문자열을 Notion Status 옵션 값으로 정규화.

    매핑에 없는 값은 소문자 원본을 그대로 반환합니다(향후 신규 상태 대비).
    """
    key = (docker_status or "").lower()
    health = (health_status or "").lower()
    if key == "running" and health in (
        NotionStatus.STARTING, NotionStatus.HEALTHY, NotionStatus.UNHEALTHY,
    ):
        return health
    return _DOCKER_TO_NOTION.get(key, key)
