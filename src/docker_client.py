from typing import Any, Dict, Optional, Iterator
from docker import from_env
from docker.errors import NotFound
from datetime import datetime
from zoneinfo import ZoneInfo
from config.settings import settings
from src.models import DockerContainerInfo
from src.logger import docker_logger

class DockerClient:
    def __init__(self, docker_api_url: str) -> None:
        """Docker 초기화"""
        self.docker_api_url = docker_api_url
        self.client = from_env(environment={"DOCKER_HOST": self.docker_api_url})

        docker_logger.info(f"Connecting to Docker daemon at {self.docker_api_url}...")

        # 연결 테스트
        if not self.client.ping():
            docker_logger.error(f"Unable to connect to Docker daemon at {self.docker_api_url}")
            raise ConnectionError(f"Unable to connect to Docker daemon at {self.docker_api_url}")

    def disconnect(self) -> None:
        """Docker 클라이언트 연결 종료"""
        docker_logger.info("Disconnecting from Docker daemon...")
        self.client.close()

    def monitor_changes(self, filters: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Docker 이벤트 모니터링 생성기"""
        docker_logger.info("Starting to monitor Docker events...")
        return self.client.events(decode=True, filters=filters)
    
    def list_all_containers(self) -> list[DockerContainerInfo]:
        """모든 Docker 컨테이너 정보를 리스트로 반환"""
        docker_logger.info("Listing all Docker containers...")
        containers = []
        try:
            container_list = self.client.containers.list(all=True)
            
            for c in container_list:
                if not c.id: continue
                info = self.get_container_info(str(c.id))
                if info:
                    containers.append(info)
                else:
                    docker_logger.error(f"Failed to get info for container {c.id}")
        except Exception as e:
            docker_logger.error(f"Error listing containers: {e}")
            
        return containers
    
    def get_container_info(self, container_name: str) -> Optional[DockerContainerInfo]:
        """컨테이너 이름으로 상세 정보를 조회하여 DockerContainerInfo 객체로 반환"""
        docker_logger.debug(f"Getting info for container: {container_name}")
        try:
            container = self.client.containers.get(container_name)
            
            # IP 주소 추출
            ip_address = container.attrs.get('NetworkSettings', {}).get('IPAddress', '')
            if not ip_address:
                networks = container.attrs.get('NetworkSettings', {}).get('Networks', {})
                for net_info in networks.values():
                    if net_info.get('IPAddress'):
                        ip_address = net_info.get('IPAddress')
                        break
            
            # 포트 정보 추출 (IPv6 제외)
            ports_data = container.attrs.get('NetworkSettings', {}).get('Ports', {}) or {}
            ports_set = set()
            
            for container_port_proto, host_bindings in ports_data.items():
                # 포트와 프로토콜 분리
                port, proto = container_port_proto.split('/')
                
                if host_bindings:
                    for binding in host_bindings:
                        # IPv6 제외
                        host_ip = binding.get('HostIp', '')
                        if ':' in host_ip:
                            continue

                        host_port = binding.get('HostPort')
                        if host_port:
                            ports_set.add(f"{port}→{host_port}/{proto}")
                else:
                    # 매핑 없이 노출만 된 경우
                    ports_set.add(f"{port}/{proto}")
                    
            ports_str = "\n".join(sorted(list(ports_set)))

            # 태그 추출
            labels = container.attrs.get('Config', {}).get('Labels', {}) or {}
            d2n_enabled = labels.get('d2n.enabled', 'FALSE').upper() == 'TRUE'
            d2n_database = labels.get('d2n.database', settings.DEFAULT_DB_ID)

            # DockerContainerInfo 객체 반환
            return DockerContainerInfo(
                container_id=str(container.id or ""),
                name=str(container.name or "").lstrip('/'),
                status=container.status,
                seen=datetime.now(ZoneInfo(settings.TIMEZONE)).isoformat(),
                ip=ip_address,
                port=ports_str,
                d2n_enabled=d2n_enabled,
                d2n_database=d2n_database
            )
        except NotFound:
            return None
        except Exception as e:
            docker_logger.error(f"Error getting info for {container_name}: {e}")
            return None