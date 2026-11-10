# loadtest

| 파일 | 용도 |
|---|---|
| `mes-load.js` | MES API k6 부하 (대시보드·SPC·Lot 조회·검사·TrackIn/Out·커넥션 풀 고갈) |
| `bench.py` | DB 경로 측정 1회 자동화: 워밍업 → Lot 사전 생성 → 카운터 스냅샷 → 샘플링 → k6 → 증가분 → 정합성 검사 |
| `mes-invariants.sql` | MES 정합성 불변식 (위반 건수가 모두 0이어야 함) |
| `report.py` | `bench.py` 결과 JSON을 마크다운 표로 |
| `stack.sh` | 측정 출발점 맞추기: DB 스냅샷 생성·복원, backend jar 교체 |
| `seed-mes.sql` | Lot 8,000 / 웨이퍼 200,000 / 이력·이벤트 각 800,000행 |
| `ai-predict.js`, `fault.sh` | ai-serving 부하·장애 주입 (클러스터, RUNBOOK 4.15) |

## 로컬 DB 경로 측정

backend 레포의 `docker-compose.loadtest.yml`로 운영 limit과 같은 자원 제한(backend 1CPU/1g, MySQL 1CPU/1g)을 건다. AI는 고정 응답 stub으로 바꿔 AI CPU 포화가 DB 병목을 가리지 않게 한다.

```bash
# backend 레포에서 비교할 jar를 빌드해 홈 아래에 둔다 (colima는 홈 디렉터리만 마운트한다)
./gradlew bootJar && cp build/libs/*-SNAPSHOT.jar ~/loadtest-jars/before.jar

# 이 레포에서
loadtest/stack.sh snapshot ~/loadtest-jars/before.jar     # 한 번만: 시드 + 디스크 반영 완료 상태를 스냅샷으로
loadtest/stack.sh restore ~/loadtest-jars/before.jar      # 매 측정 전: 스냅샷 DB, 빈 MinIO, 새 JVM
python3 loadtest/bench.py --label before-r120 --scenario inspect --rate 120 --duration 60s \
  --warmup 60s --warmup-rate 40 --env HOT_LOTS=50
python3 loadtest/report.py loadtest/results/*.json
```

- `HOT_LOTS`: 동시에 검사를 받는 Lot 수. 1이면 연속 요청이 한 Lot에 몰려 Lot 잠금 경합이 최대가 된다
- `pool` 시나리오: 한 Lot에 몰린 검사(`RATE`)와 다른 Lot의 저부하 검사(`CALM_RATE`)를 함께 보내 커넥션 풀 고갈이 무관한 Lot까지 번지는지 본다
- 개선 전후 비교는 같은 stub·시드에서 jar만 `BACKEND_JAR`로 바꿔 같은 명령을 다시 실행한다. 설정 하나의 기여도는 `SPRING_APPLICATION_JSON`으로 덮어써 본다
- 측정 중에는 같은 VM에서 다른 무거운 작업(테스트, 빌드)을 돌리지 않는다. Testcontainers 테스트와 겹치면 p95가 수십 배 튄다

### 측정 출발점을 맞춰야 하는 이유 (실측)

| 함정 | 증상 | 대응 |
|---|---|---|
| 측정이 쌓여 MySQL 컨테이너가 메모리 한도(1GiB)에 닿음 | 실행할수록 COMMIT 평균 0.9ms → 9.6ms, 같은 jar의 p95가 수십 배. `memory.events max` 2.5만 회, direct reclaim 239만 회 | 매 측정 전 `stack.sh restore` |
| 시드 직후 측정 | 더티 페이지 기록과 겹쳐 p95 수 초 | 스냅샷은 더티 페이지가 비워진 뒤 뜬다 |
| 막 띄운 JVM(1CPU)에 바로 목표 부하 | JIT 컴파일과 겹쳐 backend CPU 99%, p95 5초 | `--warmup-rate`로 낮은 부하 예열 |
| 호스트(Mac)의 다른 앱 부하 | 같은 조건에서도 실행 간 편차 | 개선 전후를 번갈아 3회씩 재고 중앙값 비교. SQL·commit·fsync 수처럼 환경에 무관한 지표를 먼저 본다 |

## 결과 JSON 항목

| 항목 | 출처 |
|---|---|
| `endpoints` | k6: 처리량, p50/p95/p99/max, 상태 코드 (Hold된 Lot의 409는 실패로 세지 않음) |
| `per_request` | statement digest 합계, `Com_*`·`Questions`·`Innodb_os_log_fsyncs`·binlog 크기 증가분 ÷ 요청 수 |
| `locks` | `Innodb_row_lock_*` 증가분, `INNODB_METRICS`(데드락·잠금 타임아웃), `data_lock_waits` 0.25초 샘플, Lot 잠금 쿼리 호출 시간(`spring_data_repository_invocations`), Hikari 대기 수 |
| `cpu_percent` | `docker stats` 컨테이너별 평균·최대 |
| `integrity` | `mes-invariants.sql` + 자동 Hold 누락(이항검정 재계산) + k6가 수락한 요청과 DB 대조 |
