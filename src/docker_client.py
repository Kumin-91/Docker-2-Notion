import re
import ipaddress
from typing import Any, Iterator
from datetime import datetime
from zoneinfo import ZoneInfo
from docker import from_env
from docker.errors import NotFound
from config.settings import Settings
from src.models import DockerContainerInfo
from src.status import normalize_status
from src.logger import docker_logger


# ---------------------------------------------------------------------------
# 순수 파싱 함수 (Docker SDK 호출과 분리되어 단위 테스트가 용이)
# 입력은 container.attrs (inspect 결과) 딕셔너리입니다.
# ---------------------------------------------------------------------------

# 나노초(>6자리) 소수부를 마이크로초(6자리)로 절삭하기 위한 패턴
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def is_host_network(attrs: dict[str, Any]) -> bool:
    """컨테이너가 host 네트워크 모드인지 판별."""
    net_settings = attrs.get("NetworkSettings", {}) or {}
    networks = net_settings.get("Networks", {}) or {}
    if "host" in networks:
        return True
    host_config = attrs.get("HostConfig", {}) or {}
    return host_config.get("NetworkMode", "") == "host"


def _ip_sort_key(ip: str) -> tuple[int, object]:
    """IP를 숫자값 기준으로 정렬하기 위한 키. (10.9.x 가 10.20.x 보다 앞서도록)

    파싱 불가한 값은 뒤로 보내고 문자열 기준으로 정렬한다.
    """
    try:
        return (0, int(ipaddress.ip_address(ip)))
    except ValueError:
        return (1, ip)


def parse_ip(attrs: dict[str, Any]) -> str:
    """컨테이너 IP 정보를 문자열로 추출.

    - host 네트워크    -> "host"
    - 그 외            -> `ipv4: name` 멀티라인 (IP 숫자값 오름차순 정렬)
    - Networks 비어있음 -> 레거시 NetworkSettings.IPAddress 폴백
    """
    if is_host_network(attrs):
        return "host"

    net_settings = attrs.get("NetworkSettings", {}) or {}
    networks = net_settings.get("Networks", {}) or {}

    pairs = []  # (ip, network_name)
    for name, info in networks.items():
        ip = (info or {}).get("IPAddress") or ""
        if ip:
            pairs.append((ip, name))

    if pairs:
        pairs.sort(key=lambda pair: _ip_sort_key(pair[0]))
        return "\n".join(f"{ip}: {name}" for ip, name in pairs)

    # 레거시(기본 브리지) 폴백
    return net_settings.get("IPAddress") or ""


def parse_ports(attrs: dict[str, Any]) -> str:
    """포트 매핑 정보를 문자열로 추출. (IPv6 제외)

    - 호스트 미바인딩(EXPOSE만)      -> `{cport}/{proto}`
    - 0.0.0.0 / 미지정(전체 노출)     -> `{cport} → {hostPort}/{proto}`
    - 특정 IP 바인딩(접근 대역 제한)   -> `{cport} → {hostIp}:{hostPort}/{proto}`
    - host 네트워크 모드             -> "" (포트 매핑 표기 불가)
    """
    if is_host_network(attrs):
        return ""

    net_settings = attrs.get("NetworkSettings", {}) or {}
    ports_data = net_settings.get("Ports", {}) or {}

    entries: set[str] = set()
    for cport_proto, bindings in ports_data.items():
        cport, _, proto = cport_proto.partition("/")
        suffix = f"/{proto}" if proto else ""

        # 호스트에 바인딩되지 않고 노출만 된 포트
        if not bindings:
            entries.add(f"{cport}{suffix}")
            continue

        for binding in bindings:
            host_ip = binding.get("HostIp", "") or ""
            # IPv6 제외
            if ":" in host_ip:
                continue
            host_port = binding.get("HostPort") or ""
            if not host_port:
                continue

            if host_ip in ("", "0.0.0.0"):
                entries.add(f"{cport} → {host_port}{suffix}")
            else:
                entries.add(f"{cport} → {host_ip}:{host_port}{suffix}")

    return "\n".join(sorted(entries))


def parse_stack(labels: dict[str, str]) -> str:
    """컨테이너 라벨에서 docker-compose 프로젝트(스택) 이름을 추출.

    - docker-compose -> com.docker.compose.project
    - Swarm 스택      -> com.docker.stack.namespace (폴백)
    - 둘 다 없으면 빈 문자열 (단독 컨테이너)
    """
    return (
        labels.get("com.docker.compose.project")
        or labels.get("com.docker.stack.namespace")
        or ""
    )


def to_local_iso(timestamp: str, timezone: str) -> str:
    """Docker의 RFC3339 타임스탬프를 지정 타임존 기준 ISO 8601 문자열로 변환.

    - 빈 값 / Docker 영(zero) 타임스탬프 -> "" 반환
    - 나노초 정밀도는 마이크로초로 절삭하여 파싱 호환성 확보
    """
    raw = (timestamp or "").strip()
    if not raw or raw.startswith("0001-01-01"):
        return ""

    raw = raw.replace("Z", "+00:00")
    raw = _FRACTION_RE.sub(r"\1", raw)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    return dt.astimezone(ZoneInfo(timezone)).isoformat()


# ---------------------------------------------------------------------------
# Docker 데몬 연동 클라이언트
# ---------------------------------------------------------------------------


class DockerClient:
    def __init__(self, settings: Settings) -> None:
        """Docker 초기화 (설정 주입)."""
        self.settings = settings
        self.docker_api_url = settings.DOCKER_API_URL
        self.client = from_env(environment={"DOCKER_HOST": self.docker_api_url})

        docker_logger.info(f"Connecting to Docker daemon at {self.docker_api_url}...")

        # 연결 테스트
        if not self.client.ping():
            docker_logger.error(f"Unable to connect to Docker daemon at {self.docker_api_url}")
            raise ConnectionError(f"Unable to connect to Docker daemon at {self.docker_api_url}")

    def disconnect(self) -> None:
        """Docker 클라이언트 연결 종료."""
        docker_logger.info("Disconnecting from Docker daemon...")
        self.client.close()

    def ping(self) -> bool:
        """Docker 데몬 연결 상태 확인."""
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def reconnect(self) -> bool:
        """클라이언트를 재생성하여 데몬에 재연결. 성공 여부를 반환."""
        docker_logger.info("Reconnecting to Docker daemon...")
        try:
            self.client.close()
        except Exception:
            pass
        self.client = from_env(environment={"DOCKER_HOST": self.docker_api_url})
        return self.ping()

    def monitor_changes(self, filters: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Docker 이벤트 모니터링 생성기."""
        docker_logger.info("Starting to monitor Docker events...")
        return self.client.events(decode=True, filters=filters)

    def list_all_containers(self) -> list[DockerContainerInfo]:
        """모든 Docker 컨테이너 정보를 리스트로 반환."""
        docker_logger.info("Listing all Docker containers...")
        containers = []
        try:
            container_list = self.client.containers.list(all=True)

            for c in container_list:
                if not c.id:
                    continue
                info = self.get_container_info(str(c.id))
                if info:
                    containers.append(info)
                else:
                    docker_logger.error(f"Failed to get info for container {c.id}")
        except Exception as e:
            docker_logger.error(f"Error listing containers: {e}")

        return containers

    def get_container_info(self, container_id: str) -> DockerContainerInfo | None:
        """컨테이너 ID(또는 이름)로 상세 정보를 조회하여 DockerContainerInfo로 반환."""
        docker_logger.debug(f"Getting info for container: {container_id}")
        try:
            container = self.client.containers.get(container_id)
            attrs = container.attrs or {}

            # 태그(라벨) 추출
            labels = (attrs.get("Config", {}) or {}).get("Labels", {}) or {}
            d2n_enabled = labels.get("d2n.enabled", "FALSE").upper() == "TRUE"
            d2n_database = labels.get("d2n.database", "")

            image = (attrs.get("Config", {}) or {}).get("Image", "") or ""
            created = to_local_iso(attrs.get("Created", ""), self.settings.TIMEZONE)

            return DockerContainerInfo(
                container_id=str(container.id or ""),
                name=str(container.name or "").lstrip("/"),
                status=normalize_status(container.status),
                seen=datetime.now(ZoneInfo(self.settings.TIMEZONE)).isoformat(),
                ip=parse_ip(attrs),
                port=parse_ports(attrs),
                image=image,
                created=created,
                stack=parse_stack(labels),
                d2n_enabled=d2n_enabled,
                d2n_database=d2n_database,
            )
        except NotFound:
            return None
        except Exception as e:
            docker_logger.error(f"Error getting info for {container_id}: {e}")
            return None
