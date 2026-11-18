# 테스트 구성과 정리 결과

2026-11-17 기준. 프로덕션 코드와 의존성 파일은 변경하지 않았다.
테스트 케이스 수는 매개변수화된 입력을 각각 세는 pytest 수집 기준이다.

| 영역 / 파일 | 변경 전 | 변경 후 | 검증 목적 |
| --- | ---: | ---: | --- |
| `test_cache_manager.py` | 13 | 9 | 캐시 저장·복원, DB별 격리, TTL, UUID 정규화, 원자적 저장 실패 |
| `test_network.py` | 24 | 20 | IP 숫자 정렬, 포트 바인딩, IPv6 제외, host 모드, 날짜·스택 변환 |
| `test_notion_convert.py` | 8 | 4 | Notion 요청 속성, 날짜 생략, 수동 스택 보존, 빈 네트워크 속성 삭제 |
| `test_notion_requests.py` | 32 | 32 | 검색 실패와 빈 결과 구분, 생성 단일 전송, 재시도·429·종료 처리 |
| `test_status.py` | 18 | 18 | 상태 정규화, dead 분리, 실행 상태와 Healthcheck 우선순위 |
| `test_status_schema.py` | 22 | 17 | 옵션 초기화, ID 보존, 백업·검증 실패 시 변경 차단, 실제 SDK 요청 |
| `test_sync_schema.py` | 0 | 5 | 스키마 초기화 순서, DB별 장애 격리, 재시작 복구·429·저장 실패 |
| `test_sync_service.py` | 43 | 43 | 중복 생성 방지, 영속 작업, 이벤트 병합, 라우팅, 삭제·스냅샷 경합 |
| `test_sync_integration.py` | 2 | 2 | 실제 Notion SDK와 스케줄러를 연결한 생성 응답 유실·검색 장애 회귀 |
| `test_sync_state.py` | 13 | 13 | 작업·관리 이력 복원, 손상·잘못된 필드에서 복구 표식 보존 |
| `test_docker_lifecycle.py` | 16 | 16 | Docker 연결·조회 실패, 스냅샷 신뢰성, 이벤트 순서, 스레드 종료 |
| `test_health.py` | 24 | 24 | 루프 정지, Docker 장애 유예·복구, 손상 상태, 읽기 전용 탐색, CLI 격리 |
| `test_main_lifecycle.py` | 2 | 2 | 종료 과정의 예외에도 연결 정리와 상태 저장 |
| `test_settings.py` | 5 | 5 | 설정 로드, DB 매핑·폴백, 필수 환경 변수 누락 |
| `test_deploy_script.py` | 3 | 3 | 수동 배포의 기본·빈 값·명시 네트워크 및 소켓 ro 인자 |
| `test_jenkins_pipeline.py` | 4 | 4 | 테스트 컨테이너 종료 코드 전파, 네트워크·소켓 ro 인자 |
| **합계** | **229** | **217** | |

테스트 파일은 15개에서 16개로 늘었다(`conftest.py` 제외).
스키마 API와 스케줄러의 책임을 나누기 위해 기존 테스트 5개를 이동했다.
폐기된 기능을 검증하거나 현재 구현과 불일치하는 테스트는 발견하지 않았다.
구형 캐시를 거부하는 테스트는 호환성·데이터 보호 검증이므로 유지했다.

## 통합한 검증

- 캐시: `test_set_and_get`, `test_get_missing_returns_none`,
  `test_persistence_across_instances`를 저장·재시작 복원 테스트로 통합했다.
  `test_remove`는 DB 격리 테스트에 포함하고 삭제 결과와 재로드까지 확인한다.
  `test_ttl_expiry`, `test_expiry_isolated_to_database`는 가상 시계로 TTL 경계와
  DB별 만료를 검증한다. 내부 `cache_data`를 직접 수정하지 않는다.
- Notion 속성: `_rich_text` 직접 검증 2개와 기본 필드·날짜·스택의 정상 검증을
  공개 `create_page` 메서드가 보내는 전체 요청 속성 검증으로 모았다.
  빈 날짜 생략, 수동 스택 보존, 빈 IP·포트 삭제는 별도 테스트로 유지했다.
- 네트워크: 단순한 다중 IP 정렬은 숫자 정렬 회귀 테스트가 함께 검증한다.
  전체 인터페이스·특정 IP·미바인딩 포트의 단독 정상 검증은
  정렬된 혼합 포트 출력 테스트로 모았다. IPv6 바인딩 제외 조건도 유지했다.
- Healthcheck: 대기 중·이벤트 수신 시의 하트비트 검증은 두 매개변수 케이스로
  정리했다. 실행 케이스 수는 그대로이며 실패한 tick은 별도 검증한다.

## 설정과 검증 방식

- `mock_notion`: 생성자를 우회하지 않고 외부 SDK 생성만 대체한다.
  요청·스키마·속성 테스트가 같은 초기화 경로를 사용한다.
- `notion_transport`: 실제 고정 버전 SDK와 `httpx.MockTransport`를 연결한다.
  불필요한 기본 HTTP 클라이언트 생성 후 교체를 없애고 종료 처리를 공유한다.
- 이벤트 필터는 상수 자체가 아닌 실제 `monitor_changes` 호출 인자로 확인한다.
- HTTP 날짜 재시도는 고정 시계로 정확한 값을 확인한다.
- 손상된 작업 필드는 예외뿐 아니라 기존 파일을 덮어쓰지 않는지도 확인한다.
- 실제 파일 I/O, 영속 상태 복원, HTTP 전송·셸 경계 검증은 유지했다.
  검색/스케줄러/SDK 테스트의 유사한 실패는 서로 다른 계층의 계약이므로 제거하지 않았다.
  Jenkins의 종료 코드 1과 2도 테스트 실패·수집 오류를 구분하는 회귀 사례로 유지했다.

## 실행 결과

동일한 임시 가상환경에서 Python 3.15.2, pytest 8.4.2, coverage 7.17.3으로 측정했다.
프로젝트의 런타임·개발 의존성 지정은 그대로 설치했다.

| 항목 | 변경 전 | 변경 후 |
| --- | ---: | ---: |
| pytest 결과 | 229 passed | 217 passed |
| 일반 실행 3회(초, JUnit suite time) | 1.562 / 1.587 / 1.490 | 1.480 / 1.505 / 1.424 |
| 중앙값 | 1.562초 | 1.480초 |
| 문장 커버리지 | 965/1040 (92.79%) | 966/1040 (92.88%) |
| 분기 커버리지 | 265/314 (84.39%) | 266/314 (84.71%) |
| 문장·분기 합산 | 90.84% | 90.99% |

커버리지는 `src`, `config`, `main`을 대상으로 분기를 포함해 측정했다.
로그 디렉터리가 없는 같은 초기 조건에서 비교했으며,
기존에 실행되던 모든 코드 줄과 분기가 변경 후에도 실행되는지 JSON 결과로 확인했다.
일반 실행은 커버리지 계측 없이 각각 3회 측정했다. 약 5% 개선은 로컬 관측치이며
CI 성능을 보장하지 않는다. 셸·별도 프로세스를 쓰는 배포 검증이 실행 시간의 큰 부분이다.
Skip·xfail 추가나 검증 조건 완화는 없다.

재실행 예시(검증 환경에 `requirements-dev.txt`와 `coverage` 설치 후):

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python -m coverage run --branch --source=src,config,main --data-file=/tmp/d2n-tests.coverage -m pytest -p no:cacheprovider
python -m coverage report --data-file=/tmp/d2n-tests.coverage
```

## 추가 검토 범위

- 실제 Docker·Notion·Jenkins 서버는 호출하지 않았다. SDK 전송은 메모리 HTTP,
  배포는 가짜 Docker CLI로 검증한다. Jenkins 셸 추출 테스트는 Groovy 전체 실행을 대신하지 않는다.
- 현재 호스트에는 운영 이미지의 Python 3.14가 없다. 이번 비교는 Python 3.15.2이며
  운영 버전·Linux 이미지 검증은 Jenkins에서 별도로 확인해야 한다.
- 설정 오류 경로(잘못된 TZ·YAML·기본 DB)와 스키마의 잘못된 그룹/옵션 참조,
  Notion SDK의 실제 404 응답을 `PageNotFoundError`로 변환하는 경계에 미검증 경로가 남아 있다.
  이번 정리에서 기존 검증을 삭제한 영역은 아니며, 후속 보강 후보이다.
- CLI Healthcheck는 별도 프로세스로 실제 실행해 검증하지만 그 프로세스는
  이번 부모 프로세스 커버리지에 포함되지 않는다.
