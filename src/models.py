from dataclasses import dataclass

"""
도커 컨테이너 정보를 저장하는 모델

Attributes:
    container_id (str): 도커 컨테이너 ID
    name (str): 도커 컨테이너 이름
    status (str): 도커 컨테이너 상태
    seen (str): 도커 컨테이너가 마지막으로 확인된 시간
    ip (str): 도커 컨테이너의 IP 주소
    port (str): 도커 컨테이너의 포트 정보
"""

@dataclass
class DockerContainerInfo:
    container_id: str
    name: str
    status: str
    seen: str
    ip: str
    port: str
    d2n_enabled: bool
    d2n_database: str