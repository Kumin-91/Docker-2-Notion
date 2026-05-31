#!/bin/sh
# Docker-2-Notion 수동 빌드 & 배포 스크립트 (Jenkins 대체)
#
# 사용법: 프로젝트 루트(Dockerfile 있는 곳)에서 실행
#   ./deploy.sh              # 태그 beta-manual 로 빌드 & 배포
#   ./deploy.sh beta-9       # 태그 직접 지정
#
# 사전 준비:
#   - ${HOST_DIR}/config/config.yaml  (DB 매핑, config.yaml.example 참고)
#   - NOTION_API_KEY 환경변수 또는 ${HOST_DIR}/config/.env 에 키 작성
#
# 덮어쓸 수 있는 환경변수:
#   HOST_DIR(기본 /docker/d2n), LOG_LEVEL(INFO), TZ(Asia/Seoul),
#   D2N_DATABASE(Jenkins), NETWORK(net_outbound, 빈 값이면 기본 브리지 사용)
set -eu

PROJECT_NAME="d2n"
IMAGE_TAG="${1:-beta-manual}"             # 첫 인자로 태그 지정 (기본 beta-manual)
HOST_DIR="${HOST_DIR:-/docker/d2n}"       # config/logs/data 가 보존되는 호스트 경로
LOG_LEVEL="${LOG_LEVEL:-INFO}"
TZ="${TZ:-Asia/Seoul}"
D2N_DATABASE="${D2N_DATABASE:-Jenkins}"   # 이 컨테이너를 기록할 Notion DB 이름
NETWORK="${NETWORK:-net_outbound}"        # 외부(Notion API) 통신용 네트워크

echo "==> 1/4 호스트 디렉터리 확인 (${HOST_DIR})"
mkdir -p "${HOST_DIR}/config" "${HOST_DIR}/logs" "${HOST_DIR}/data"
if [ ! -f "${HOST_DIR}/config/config.yaml" ]; then
  echo "    [오류] ${HOST_DIR}/config/config.yaml 이 없습니다. config.yaml.example 을 참고해 작성하세요." >&2
  exit 1
fi

echo "==> 2/4 도커 이미지 빌드 (${PROJECT_NAME}:${IMAGE_TAG})"
docker build -t "${PROJECT_NAME}:${IMAGE_TAG}" .
docker tag "${PROJECT_NAME}:${IMAGE_TAG}" "${PROJECT_NAME}:latest"

echo "==> 3/4 기존 컨테이너 정리"
docker stop "${PROJECT_NAME}" 2>/dev/null || true
docker rm   "${PROJECT_NAME}" 2>/dev/null || true

# 선택 항목은 값이 있을 때만 플래그로 주입 (아래 docker run 에서 의도적으로 따옴표 없이 분할)
NOTION_ENV=""
if [ -n "${NOTION_API_KEY:-}" ]; then
  NOTION_ENV="-e NOTION_API_KEY=${NOTION_API_KEY}"
fi

NETWORK_ARG=""
if [ -n "${NETWORK}" ]; then
  NETWORK_ARG="--network ${NETWORK}"
fi

echo "==> 4/4 새 컨테이너 실행"
# shellcheck disable=SC2086  # NOTION_ENV/NETWORK_ARG 는 빈 값일 때 인자에서 빠지도록 의도적으로 분할
docker run -d \
  --name "${PROJECT_NAME}" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${HOST_DIR}/config:/app/config" \
  -v "${HOST_DIR}/logs:/app/logs" \
  -v "${HOST_DIR}/data:/app/data" \
  -e DOCKER_API_URL="unix:///var/run/docker.sock" \
  -e LOG_LEVEL="${LOG_LEVEL}" \
  -e TZ="${TZ}" \
  ${NOTION_ENV} \
  ${NETWORK_ARG} \
  --label "d2n.enabled=true" \
  --label "d2n.database=${D2N_DATABASE}" \
  --label "com.centurylinklabs.watchtower.enable=false" \
  --restart unless-stopped \
  "${PROJECT_NAME}:latest"

echo "==> dangling 이미지 정리"
docker image prune -f >/dev/null 2>&1 || true

echo "==> 완료! 로그 보기: docker logs -f ${PROJECT_NAME}"
