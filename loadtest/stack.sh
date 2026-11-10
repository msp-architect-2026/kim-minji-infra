#!/bin/sh
# 로컬 부하 테스트 스택 관리. 개선 전후를 같은 출발점에서 재기 위해 DB를 스냅샷으로 되돌리고 jar를 바꿔 끼운다
#   loadtest/stack.sh snapshot              시드를 넣고 디스크까지 다 쓰인 MySQL 데이터 디렉터리를 볼륨에 떠 둔다
#   loadtest/stack.sh restore <jar> [json]  스냅샷 DB + 빈 MinIO + 지정 jar(BACKEND_JAR 경로)로 시작한다
#   loadtest/stack.sh swap <jar> [json]     DB는 그대로 두고 backend jar만 바꾼다
# json은 SPRING_APPLICATION_JSON으로 넘겨 설정 하나의 기여도를 볼 때 쓴다
# 측정 직전에는 새 JVM을 낮은 부하로 데운다: bench.py --warmup 60s --warmup-rate 40
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
BACKEND_DIR=${BACKEND_DIR:-$HERE/../../kim-minji-backend}
PROJECT=${COMPOSE_PROJECT:-kim-minji-backend}
SNAPSHOT_VOLUME=${SNAPSHOT_VOLUME:-mes-loadtest-mysql-snapshot}
MYSQL=$PROJECT-mysql-1
MYSQL_IMAGE=mysql:8.0.46
ROOT_PW=localroot

dc() {
  (cd "$BACKEND_DIR" && AI_SERVER_URL=http://ai-stub:8000 docker compose -p "$PROJECT" \
    -f docker-compose.yml -f docker-compose.loadtest.yml --profile stub "$@")
}
sql() { docker exec -i "$MYSQL" mysql -uroot -p$ROOT_PW -N "$@" 2>/dev/null; }
wait_mysql() { until sql -e 'SELECT 1' >/dev/null; do sleep 1; done; }

backend() {  # backend <jar 경로> [json]
  json=${2:-}
  [ -z "$json" ] && json='{}'
  BACKEND_JAR=$1 SPRING_APPLICATION_JSON=$json dc up -d --force-recreate --no-deps backend >/dev/null 2>&1
  sleep 5
  until curl -sf localhost:8080/actuator/health >/dev/null; do sleep 2; done
  echo "backend: $1 ${2:-}"
}

fresh_minio() {
  dc up -d --force-recreate --no-deps minio >/dev/null 2>&1
  sleep 2
  dc up -d --force-recreate --no-deps minio-init >/dev/null 2>&1
  until [ "$(docker inspect -f '{{.State.Status}}' $PROJECT-minio-init-1)" = exited ]; do sleep 1; done
}

case "$1" in
  snapshot)
    : "${2:?jar 경로가 필요합니다 (Flyway 마이그레이션용)}"
    dc up -d mysql >/dev/null 2>&1
    wait_mysql
    docker stop $PROJECT-backend-1 >/dev/null 2>&1 || true
    sql -e "DROP DATABASE IF EXISTS appdb; CREATE DATABASE appdb; GRANT ALL ON appdb.* TO 'appuser'@'%'; RESET MASTER;"
    fresh_minio
    backend "$2"
    sql appdb < "$HERE/seed-mes.sql" >/dev/null
    until [ "$(sql -e "SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_pages_dirty'" | cut -f2)" -lt 20 ]; do sleep 5; done
    docker stop $PROJECT-backend-1 >/dev/null
    sql -e "SET GLOBAL innodb_fast_shutdown=0"
    docker stop -t 120 "$MYSQL" >/dev/null
    docker run --rm --volumes-from "$MYSQL" -v "$SNAPSHOT_VOLUME":/snap --entrypoint sh $MYSQL_IMAGE \
      -c 'rm -rf /snap/* && cp -a /var/lib/mysql/. /snap/ && du -sh /snap'
    docker start "$MYSQL" >/dev/null
    ;;
  restore)
    : "${2:?jar 경로가 필요합니다}"
    docker stop $PROJECT-backend-1 >/dev/null 2>&1 || true
    docker stop -t 60 "$MYSQL" >/dev/null
    docker run --rm --volumes-from "$MYSQL" -v "$SNAPSHOT_VOLUME":/snap --entrypoint sh $MYSQL_IMAGE \
      -c 'rm -rf /var/lib/mysql/* && cp -a /snap/. /var/lib/mysql/'
    docker start "$MYSQL" >/dev/null
    wait_mysql
    fresh_minio
    backend "$2" "${3:-}"
    ;;
  swap)
    : "${2:?jar 경로가 필요합니다}"
    backend "$2" "${3:-}"
    ;;
  *)
    sed -n '2,8p' "$0"
    exit 1
    ;;
esac
