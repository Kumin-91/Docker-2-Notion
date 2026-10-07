# 🐋 Docker-2-Notion (D2N)

**"도커 컨테이너 관리가 귀찮아서 만든 노션 자동 동기화 도구"** > 터미널에서 `docker ps`를 치는 대신, 익숙한 Notion 대시보드에서 실시간으로 컨테이너 상태를 확인하세요.

[💡 주요 특징](#-주요-특징)

[🛠 Notion 사전 준비](#-notion-사전-준비)

[🚀 사용 방법 (Docker Label)](#-사용-방법-docker-label)

[🏗 프로젝트 구조](#-프로젝트-구조)

[💡 개발자 팁](#-개발자-팁)

[🗿 마일스톤](#-마일스톤)

## 💡 주요 특징

* **실시간 이벤트 모니터링:** `create` `start` `stop` `die` 등 도커 이벤트를 실시간으로 감지하여 노션에 즉시 반영합니다.

* **라벨 기반 필터링:** 모든 컨테이너가 아닌, `d2n.enabled=true` 라벨이 붙은 컨테이너만 골라서 관리합니다.

* **멀티 데이터베이스 지원:** 컨테이너마다 서로 다른 노션 DB로 상태를 보낼 수 있습니다.

* **DB별 캐싱:** 실제 DB ID와 컨테이너 이름을 기준으로 페이지 ID를 캐싱하여 다른 DB의 페이지를 잘못 갱신하지 않습니다.

* **컬러풀 로깅:** Docker, Notion, Cache 등 모듈별로 색상이 구분된 직관적인 로그를 제공합니다.

## 🛠 Notion 사전 준비

* Notion API 토큰을 발급받고, 데이터베이스가 있는 페이지에 권한을 부여합니다.

* Database에는 아래의 속성이 반드시 있어야 합니다.

    * **Name:** `title`

    * **Image:** `rich_text`

    * **Stacks:** `multi_select` (docker-compose 사용 시. 옵션은 자동 생성됨)

    * **Status:** `status`

      ![notion_page_status](./images/notion_page_status.png)

      | 노션 Status 값 | 관련 도커 이벤트/상태 | 의미 |
      | --- | --- | --- |
      | running | start | 컨테이너가 정상적으로 가동 중인 상태 |
      | exited | stop, die | 컨테이너 프로세스가 종료되어 멈춘 상태 |
      | removed | destroy | 컨테이너가 삭제되어 더 이상 존재하지 않는 상태 |
      | restarting | restarting | 컨테이너가 재시작 루프에 빠졌거나 다시 켜지는 중 |
      | created | create | 컨테이너가 생성되었으나 아직 실행 전인 상태 |
      | paused | paused | 컨테이너가 일시 정지된 상태 |

    * **IP:** `rich_text`

    * **Ports:** `rich_text`

    * **Created:** `date`

    * **Seen:** `date`

## 🚀 사용 방법 (Docker Label)

### 1. 대상 컨테이너 라벨 설정

* 모니터링을 원하는 컨테이너 실행 시 아래 라벨을 추가합니다.

* `d2n.enabled=true`: 해당 컨테이너를 노션 동기화 대상으로 지정합니다.

* `d2n.database=이름`: `config.yaml`에 정의한 데이터베이스 name을 입력합니다. (미지정 시 기본 DB 사용)

### 2. 호스트 설정 파일 준비

* 보안을 위해 `.env` 파일 대신 런타임 환경 변수를 사용합니다. 설정 파일과 로그는 호스트의 홈 디렉토리에서 관리합니다.

* `config/` 디렉토리에 있는 `config.yaml.example` 파일을 복사하여 실제 설정 파일을 작성하면 더 쉽습니다.

  ```Bash
  # 1. 홈 디렉토리에 설정 폴더 생성
  mkdir -p ~/d2n/config ~/d2n/logs ~/d2n/data

  # 2. Database 목록 설정 (config.yaml 작성)
  # ~/d2n/config/config.yaml 경로에 아래 내용 작성
  targets:
    default: "example"
    databases:
      - name: "example"
        database_id: "DATABASE_ID_HERE"
      - name: "Kanade"
        database_id: "KANADE_DATABASE_ID_HERE"
      - name: "Su"
        database_id: "SU_DATABASE_ID_HERE"
  ```

### 3. 프로그램 빌드 및 실행

* 민감한 API 키는 실행 시점에 `-e` 옵션으로 안전하게 주입합니다.

  | 환경 변수 | 설명 | 기본 값 | 사용 예시 |
  | --- | --- | --- | --- |
  | `NOTION_API_KEY` | Notion API 토큰 |  | `your_notion_api_key_here` | 
  | `DOCKER_API_URL` | Docker API URL |  | `tcp://host.docker.internal:2375` |
  | `LOG_LEVEL` | 로그 레벨 | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
  | `TZ` | 타임존 설정 | `Asia/Seoul` | `Asia/Japan`, `America/New_York` |

* 도커 이미지를 빌드하고, 필요한 볼륨과 환경 변수를 주입하여 컨테이너로 실행합니다.

  ```Bash
  # 1. 이미지 빌드 (v0.1.0-beta4)
  docker build . -t d2n:beta4

  # 2. 실행 (환경 변수 주입 및 볼륨 마운트)
  docker run -d \
    --name d2n-service \
    --restart unless-stopped \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v ~/d2n/config:/app/config \
    -v ~/d2n/logs:/app/logs \
    -v ~/d2n/data:/app/data \
    -e DOCKER_API_URL="unix:///var/run/docker.sock" \
    -e NOTION_API_KEY="your_notion_api_key_here" \
    -e LOG_LEVEL="INFO" \
    -e TZ="Asia/Seoul" \
    --label "d2n.enabled=true" \
    --label "d2n.database=Docker" \
    d2n:beta4
  ```

## 🏗 프로젝트 구조

* **main.py:** Docker 이벤트 수신 스레드와 단일 Notion 동기화 작업자를 실행합니다. 이벤트가 없는 동안에도 예약된 재시도를 실행하고, `SIGINT`·`SIGTERM` 수신 시 스트림을 닫고 대기 작업을 저장합니다.

* **config/settings.py:** 시스템 환경 변수를 최우선으로 참조하며 (필요시 `.env`), `config.yaml` 설정을 로드해 DB 매핑·타임존 등을 통합 관리·검증합니다. 전역 싱글톤 대신 `load_settings()` 팩토리로 명시적으로 로드하며, `resolve_db_id()`로 `d2n.database` 라벨(이름)을 실제 DB ID로 해석합니다.

* **src/models.py:** 도커에서 가공한 데이터 (상태, IP, 포트, 이미지, 생성 시각, 전용 라벨 등)를 프로그램 내부에서 일관되게 다루기 위한 표준 Dataclass를 정의합니다.

* **src/status.py:** Docker 상태 문자열을 Notion `Status` 옵션 값으로 정규화하는 매핑을 한 곳에 모읍니다.

* **src/docker_client.py:** 도커 데몬으로부터 실행 중인 컨테이너 정보를 수집합니다. 멀티 네트워크 IP, 포트 바인딩 IP(IPv4), host 네트워크, 동기화 전용 라벨(`d2n.enabled`, `d2n.database`)을 파싱하며, 파싱 로직은 SDK 호출과 분리된 순수 함수로 구현되어 단위 테스트가 가능합니다.

* **src/notion_client.py:** Notion 속성 변환과 API 호출을 담당합니다. 검색 실패와 검색 결과 없음을 구분하고, 생성 요청은 한 번만 전송하여 응답 유실 시 재검색으로 확인합니다.

* **src/cache_manager.py:** `(실제 DB ID, 컨테이너 이름)`별 페이지 ID를 `data/cache.v2.json`에 저장합니다. TTL은 300초이며 기존 `data/cache.json`은 소속 DB를 확정할 수 없어 재사용하지 않고 보존합니다.

* **src/logger.py:** 모듈 이름별로 다른 색상의 로그를 출력하여 디버깅 편의성을 높이고, 모든 로그를 파일로 기록합니다. 장기 실행 데몬을 고려해 자정마다 `YYYY-MM-DD.log`로 로테이션합니다.

## 💡 개발자 팁

* **IP 주소:** 컨테이너가 연결된 **모든 네트워크**의 IP를 `IP: 네트워크` 형태로, **IP 숫자값 오름차순**으로 정렬해 표시합니다. `--network host` 모드는 `host`로 표기합니다.

* **포트 정보:** IPv6를 제외하고, **어떤 호스트 IP에서 접근 가능한지**까지 보여줍니다.

    * 전체 노출(`0.0.0.0`/기본): `80 → 8080/tcp` (IP 생략)

    * 특정 IP 바인딩: `5432 → 127.0.0.1:5432/tcp`

    * 노출만 됨(EXPOSE, 호스트 미바인딩): `9000/tcp`

* **상태 정규화:** Docker의 `dead`/`removing` 같은 상태도 Notion 옵션(`exited`/`removed`)으로 자동 변환되어 업데이트 실패를 방지합니다.

* **스택 자동 태깅:** docker-compose 프로젝트(`com.docker.compose.project`, Swarm은 `com.docker.stack.namespace`) 이름을 `Stacks`(multi_select) 속성에 자동으로 채웁니다. 없던 옵션은 Notion이 자동 생성하며, 스택이 없는 단독 컨테이너는 해당 속성을 건드리지 않아 수동 입력값을 보존합니다.

* **자동 재연결:** 최초 연결 실패 및 재연결 예외도 1~30초 백오프로 재시도합니다. 스트림을 먼저 연 뒤 전체 상태를 조회하며, 저장한 관리 이력과 성공적인 전체 조회를 비교해 감시 중단 중 삭제된 컨테이너도 `removed`로 반영합니다. 조회 실패·부분 조회는 삭제 근거로 사용하지 않습니다.

* **캐시 무효화:** 페이지 갱신이 404로 실패하면 해당 DB의 캐시만 제거한 뒤 검색합니다. 현재 존재하는 컨테이너는 정상 검색 결과가 없을 때 생성하고, 이미 삭제된 컨테이너의 페이지는 새로 만들지 않습니다.

## 🗿 마일스톤

* [X] **Docker API 버전 업데이트 및 SDK 전환**

* [X] **객체 지향 기반 클래스 분리 (Docker, Notion, Cache)**

* [X] **라벨 기반 필터링 및 DB 라우팅 로직 구현**

* [X] **JSON 기반 5분 TTL 캐시 시스템 도입**

* [x] **destroy 이벤트 감지 및 removed 상태 처리 구현**

* [X] `Dockerfile`: **`.env` 제거, 환경 변수 기본값 설정, logs/config/data 볼륨 구성 완료**

* [X] `settings.py`: **시스템 환경 변수 우선순위 로직 적용**

* [X] **멀티 네트워크 IP / 포트 바인딩 IP / host 네트워크 표기 지원**

* [X] **Image·Created 필드 추가**

* [X] **Python 3.14 전환 및 문법 현대화**

* [X] **Notion 재시도/백오프·404 구분, Docker 이벤트 자동 재연결**

* [X] **설정 의존성 주입(DI) 및 pytest 단위 테스트 도입**

* [X] **Jenkinsfile 기반의 선언적 CI/CD 파이프라인 구축 (테스트 컨테이너 종료 코드 확인 포함)**

* [ ] **Webhook 연동을 통한 Git Push 기반 자동 배포 검증**

## 동기화 실패와 재시도

- 검색·갱신의 일시 오류(5xx, 타임아웃, 연결 오류)는 짧게 재시도한 뒤, 실패한 컨테이너를 30 → 60 → 120 → 240 → 최대 300초 간격으로 다시 처리합니다.
- 429는 Notion 작업자 전체에 대기 시간을 적용합니다. `Retry-After`가 있으면 그 시간 이전에 다시 요청하지 않습니다. 긴 대기 동안 Docker 이벤트 수신은 계속됩니다.
- 검색 실패는 페이지 생성을 허용하지 않습니다. 검색 결과가 둘 이상이면 임의로 한 페이지를 선택하지 않고 해당 작업을 중단합니다.
- 페이지 생성 전 결과 확인 중인 상태를 저장합니다. 생성 응답이 유실되거나 프로세스가 중단되면 재검색으로 기존 페이지를 찾아 연결합니다. 결과가 계속 없더라도 자동 재생성하지 않으며 `Creation result ... unknown` 로그를 남깁니다.
- 같은 대상의 이벤트는 최신 작업으로 합치고, 처리 직전 Docker 상태를 다시 조회합니다. 이전 컨테이너의 삭제 작업으로 같은 이름의 새 컨테이너 상태를 덮어쓰지 않습니다.
- 인증·권한·속성 구성 오류는 해당 작업을 중단합니다. 원인을 수정하고 D2N을 재시작하면 다시 평가합니다.
- `Seen`은 마지막 Docker 조회 시각이며 정기적인 생존 확인 값은 아닙니다.

## 상태 파일과 배포

| 파일 | 역할 |
| --- | --- |
| `data/cache.v2.json` | DB별 페이지 ID 캐시, TTL 300초 |
| `data/sync-state.v1.json` | 대기 작업·생성 결과 확인 상태·관리 이력, TTL 없음 |
| `data/cache.json` | 이전 버전 캐시, 새 버전에서는 읽거나 수정하지 않음 |

상태 파일은 임시 파일을 동기화한 뒤 원자적으로 교체합니다. `sync-state.v1.json`이 손상되거나 지원되지 않는 형식이면 자동으로 비우지 않고 시작을 중단합니다. 생성 결과 확인 상태를 잃으면 중복 생성 위험이 있기 때문입니다. 유효한 백업으로 복원해야 하며, 해당 파일을 임의로 삭제하지 마세요.

Notion 속성, 환경변수, DB 매핑, 컨테이너 라벨은 추가 변경 없이 사용할 수 있습니다. 기존 `data` 볼륨의 쓰기 권한과 영속성을 유지하고 이미지를 다시 빌드·배포하세요. 최초 배포에서는 기존 캐시를 재사용하지 않아 대상별 검색이 한 번씩 증가합니다. Dockerfile의 빌드 컨텍스트에서는 실제 설정·토큰·상태·로그 파일을 제외합니다.

같은 Docker 데몬과 Notion DB를 관리하는 D2N 인스턴스는 하나만 실행하세요. 여러 인스턴스 사이의 생성 작업 조정은 지원하지 않습니다. 기존 중복 페이지는 수동으로 정리한 뒤 재시작해야 합니다. DB 대상을 변경하면 이전 DB 페이지는 그대로 두고 새 DB에 연결합니다. 관리 이력 도입 전에 이미 삭제된 컨테이너는 자동으로 식별할 수 없습니다.

생성 결과가 계속 불명확한 작업은 Notion에서 페이지 존재 여부를 확인해야 합니다. 존재하면 정확한 `Name`과 대상 DB를 확인해 다음 재검색에서 연결되도록 합니다. 실제 생성되지 않았음을 확인하고 작업을 해제할 때는 D2N을 중지하고 상태 파일을 백업한 뒤, 해당 `(DB ID, 이름)` 작업의 `uncertain`만 `false`로 바꾸고 재시작합니다. 확인 없이 해제하면 중복이 생길 수 있습니다.

개발 검증은 `pip install -r requirements-dev.txt`, `pytest`, `mypy main.py src config`로 실행합니다. 테스트는 실제 Docker·Notion에 쓰지 않으며 실패·응답 유실·재시작·지연 재시도·삭제 보정·Jenkins 테스트 실패 전달을 검증합니다. 실제 Linux 이미지 빌드와 운영 연동은 Jenkins에서 확인합니다.
