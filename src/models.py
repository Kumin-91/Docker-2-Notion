from dataclasses import dataclass

"""
도커 컨테이너 정보를 저장하는 모델

Attributes:
    container_id (str): 도커 컨테이너 ID
    name (str): 도커 컨테이너 이름
    status (str): Notion 기준으로 정규화된 컨테이너 상태
    seen (str): 정보를 마지막으로 확인한 시간 (ISO 8601)
    ip (str): 컨테이너 IP 정보 (네트워크별 멀티라인 / host 네트워크는 "host")
    port (str): 컨테이너 포트 정보 (바인딩 IP 포함, 멀티라인)
    image (str): 컨테이너 이미지(name:tag)
    created (str): 컨테이너 생성 시각 (ISO 8601, 알 수 없으면 빈 문자열)
    stack (str): docker-compose 프로젝트(스택) 이름 (없으면 빈 문자열)
    d2n_enabled (bool): d2n.enabled 라벨
    d2n_database (str): d2n.database 라벨 (데이터베이스 "이름" 또는 빈 문자열)
"""


@dataclass(slots=True)
class DockerContainerInfo:
    container_id: str
    name: str
    status: str
    seen: str
    ip: str
    port: str
    image: str
    created: str
    stack: str
    d2n_enabled: bool
    d2n_database: str
