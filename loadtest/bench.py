#!/usr/bin/env python3
"""MES DB 경로 측정 한 회를 실행하고 결과를 JSON 한 개로 남긴다.

  python3 loadtest/bench.py --label base-inspect-k50-r20 --scenario inspect --rate 20 --duration 60s \
      --env HOT_LOTS=50 --env DEFECT_RATIO=0.08

순서: 워밍업(별도 RUN_ID) → Lot 사전 생성 → 카운터 스냅샷 → 샘플러 시작 → k6 측정 → 카운터 증가분 → 정합성 검사

수집 항목
- k6: 엔드포인트별 p50/p95/p99/max, 처리량, 상태 코드
- MySQL: 요청당 문장 수(statement digest), Com_*/Questions, commit·fsync, binlog 증가량,
         Innodb_row_lock_waits/time, 데드락·잠금 타임아웃(INNODB_METRICS), 잠금 대기 동시 건수(data_lock_waits 샘플)
- 앱: Lot 잠금 쿼리(findByIdForUpdate) 호출 시간, Hikari 커넥션 대기 수(샘플)
- docker stats: 컨테이너별 CPU 평균·최대
- 정합성: mes-invariants.sql + 자동 Hold 누락(이항검정) + k6가 수락한 요청과 DB 대조

전제: docker-compose.loadtest.yml 스택이 떠 있고(backend 8080), 시드가 들어가 있다.
"""
import argparse
import json
import math
import os
import re
import statistics
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")
PROJECT = os.environ.get("COMPOSE_PROJECT", "kim-minji-backend")
MYSQL = f"{PROJECT}-mysql-1"
NETWORK = f"{PROJECT}_default"
MYSQL_IMAGE = os.environ.get("MYSQL_IMAGE", "mysql:8.0.46")
ROOT_PW = os.environ.get("MYSQL_ROOT_PASSWORD", "localroot")
SAMPLER = "bench-lock-sampler"
CONTAINERS = ["mysql", "backend", "ai", "ai-stub", "minio"]

STATUS_VARS = [
    "Questions", "Com_select", "Com_insert", "Com_update", "Com_delete", "Com_commit", "Com_rollback", "Com_set_option",
    "Innodb_row_lock_waits", "Innodb_row_lock_time", "Innodb_os_log_fsyncs", "Innodb_data_fsyncs",
    "Innodb_rows_inserted", "Innodb_rows_updated", "Innodb_rows_read",
]
LOCK_METRICS = ["lock_deadlocks", "lock_timeouts"]

# 자동 Hold 규칙 (application.properties의 lot.hold.*와 같아야 한다)
HOLD_MIN_JUDGED = 5
HOLD_BASELINE = 0.08
HOLD_SIGNIFICANCE = 0.001


def sql(query):
    out = subprocess.run(
        ["docker", "exec", "-i", MYSQL, "mysql", "-uroot", f"-p{ROOT_PW}", "-N", "-B", "appdb"],
        input=query, capture_output=True, text=True, check=True,
    ).stdout
    return [line.split("\t") for line in out.splitlines() if line]


def status_snapshot():
    names = ",".join(f"'{v}'" for v in STATUS_VARS)
    snap = {k: float(v) for k, v in sql(f"SHOW GLOBAL STATUS WHERE Variable_name IN ({names});")}
    metrics = ",".join(f"'{m}'" for m in LOCK_METRICS)
    snap.update({k: float(v) for k, v in sql(
        f"SELECT NAME, COUNT FROM information_schema.INNODB_METRICS WHERE NAME IN ({metrics});")})
    snap["binlog_bytes"] = sum(float(r[1]) for r in sql("SHOW BINARY LOGS;"))
    return snap


def prometheus():
    text = urllib.request.urlopen(f"{BASE_URL}/actuator/prometheus", timeout=5).read().decode()
    values = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.rpartition(" ")
            try:
                values[key] = float(value)
            except ValueError:
                pass
    return values


def lot_lock_stats(prom):
    count = total = 0.0
    for key, value in prom.items():
        if 'method="findByIdForUpdate"' in key:
            if key.startswith("spring_data_repository_invocations_seconds_count"):
                count += value
            elif key.startswith("spring_data_repository_invocations_seconds_sum"):
                total += value
    return count, total


class Sampler:
    """측정 중 1초(잠금 대기는 0.25초) 간격으로 값을 모은다."""

    def __init__(self):
        self.cpu = {}
        self.lock_waits = []
        self.pending = []
        self.lot_lock_max = []
        self.stop = threading.Event()
        self.procs = []

    def start(self):
        stats = subprocess.Popen(
            ["docker", "stats", "--format", "{{.Name}} {{.CPUPerc}}"], stdout=subprocess.PIPE, text=True)
        query = ("SELECT (SELECT COUNT(*) FROM performance_schema.data_lock_waits),"
                 " (SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE info LIKE 'select % from lot % for update');")
        waits = subprocess.Popen(
            ["docker", "run", "--rm", "--name", SAMPLER, "--network", NETWORK, MYSQL_IMAGE, "sh", "-c",
             f"while :; do mysql -hmysql -uroot -p{ROOT_PW} -N -B -e \"{query}\" 2>/dev/null; sleep 0.25; done"],
            stdout=subprocess.PIPE, text=True)
        self.procs = [stats, waits]
        threading.Thread(target=self._read_stats, args=(stats,), daemon=True).start()
        threading.Thread(target=self._read_waits, args=(waits,), daemon=True).start()
        threading.Thread(target=self._poll_app, daemon=True).start()

    def _read_stats(self, proc):
        for line in proc.stdout:
            match = re.search(rf"{PROJECT}-([\w-]+)-1 ([\d.]+)%", line)
            if match and not self.stop.is_set():
                self.cpu.setdefault(match.group(1), []).append(float(match.group(2)))

    def _read_waits(self, proc):
        for line in proc.stdout:
            parts = line.split()
            if len(parts) == 2 and not self.stop.is_set():
                self.lock_waits.append((int(parts[0]), int(parts[1])))

    def _poll_app(self):
        while not self.stop.is_set():
            try:
                prom = prometheus()
                self.pending.append(sum(v for k, v in prom.items() if k.startswith("hikaricp_connections_pending")))
                self.lot_lock_max.append(max([v for k, v in prom.items()
                                              if k.startswith("spring_data_repository_invocations_seconds_max")
                                              and 'method="findByIdForUpdate"' in k] or [0]))
            except OSError:
                pass
            time.sleep(1)

    def finish(self):
        self.stop.set()
        for proc in self.procs:
            proc.terminate()
        subprocess.run(["docker", "rm", "-f", SAMPLER], capture_output=True)
        cpu = {name: {"avg": round(statistics.mean(v), 1), "max": round(max(v), 1)}
               for name, v in self.cpu.items() if name in CONTAINERS and v}
        row_waits = [w for w, _ in self.lock_waits]
        for_update = [f for _, f in self.lock_waits]
        return {
            "cpu_percent": cpu,
            "lock_waits_concurrent": {"avg": round(statistics.mean(row_waits), 2) if row_waits else 0,
                                      "max": max(row_waits, default=0)},
            "for_update_in_flight_max": max(for_update, default=0),
            "hikari_pending_max": max(self.pending, default=0),
            "lot_lock_query_max_s": round(max(self.lot_lock_max, default=0), 3),
        }


def post_json(path, body):
    req = urllib.request.Request(f"{BASE_URL}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read())


def create_lots(prefix, run_id, count, wafers):
    return [post_json("/lots", {"lotNo": f"{prefix}-{run_id}-{i}", "product": "LOAD", "waferCount": wafers})["id"]
            for i in range(count)]


def preset_lots(scenario, rate, seconds, env, run_id):
    """mes-load.js setup()과 같은 수의 Lot을 미리 만든다."""
    wafers = int(env.get("WAFERS", 25))

    def lots_for(r, hot):
        return math.ceil(r * seconds / (wafers * hot)) * hot + hot

    if scenario == "inspect":
        return {"lots": create_lots("INS", run_id, lots_for(rate, int(env.get("HOT_LOTS", 1))), wafers)}
    if scenario == "track":
        return {"lots": create_lots("TRK", run_id, math.ceil(rate * seconds / 2) + 1, wafers)}
    if scenario == "pool":
        return {"hot": create_lots("HOT", run_id, lots_for(rate, 1), wafers),
                "calm": create_lots("CALM", run_id, lots_for(float(env.get("CALM_RATE", 2)), 50), wafers)}
    return None


def k6(scenario, rate, duration, env, run_id, summary, lots_file=None):
    cmd = ["k6", "run", "-q", "--summary-export", str(summary),
           "-e", f"SCENARIO={scenario}", "-e", f"RATE={rate}", "-e", f"DURATION={duration}",
           "-e", f"RUN_ID={run_id}", "-e", f"BASE_URL={BASE_URL}"]
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    if lots_file:
        cmd += ["-e", f"LOTS_FILE={lots_file}"]
    cmd.append(str(HERE / "mes-load.js"))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    accepted = dict(m.groups() for m in re.finditer(r"ACCEPTED (\S+) (\d+)", proc.stderr))
    errors = [l for l in proc.stderr.splitlines() if "level=error" in l or "level=warn" in l]
    return accepted, errors


def k6_summary(path):
    metrics = json.loads(Path(path).read_text())["metrics"]
    endpoints = {}
    for key, value in metrics.items():
        match = re.fullmatch(r"http_req_duration\{name:(.+)\}", key)
        if match:
            name = match.group(1)
            reqs = metrics.get(f"http_reqs{{name:{name}}}", {})
            codes = {}
            for code in ["200", "409", "4xx", "5xx", "0"]:
                count = metrics.get(f"status_codes{{name:{name},status:{code}}}", {}).get("count", 0)
                if count:
                    codes[code] = count
            endpoints[name] = {
                "count": reqs.get("count", 0), "rps": round(reqs.get("rate", 0), 2),
                "p50": round(value["p(50)"], 1), "p95": round(value["p(95)"], 1),
                "p99": round(value["p(99)"], 1), "max": round(value["max"], 1), "status": codes,
            }
    dropped = metrics.get("dropped_iterations", {}).get("count", 0)
    return endpoints, dropped


def binomial_tail(n, d, p):
    pmf, below = (1 - p) ** n, 0.0
    for k in range(d):
        below += pmf
        pmf = pmf * (n - k) / (k + 1) * p / (1 - p)
    return max(0.0, 1 - below)


def integrity(run_id, accepted):
    prelude = f"SET @run = '%-{run_id}-%';\n"
    checks = {name: int(count) for name, count in sql(prelude + (HERE / "mes-invariants.sql").read_text())}
    lots = sql(prelude + """
        SELECT l.id, l.status, SUM(w.status IN ('PASS','DEFECT')), SUM(w.status = 'DEFECT'),
               EXISTS (SELECT 1 FROM lot_hold h WHERE h.lot_id = l.id AND h.source = 'AUTO')
        FROM lot l JOIN wafer w ON w.lot_id = l.id
        WHERE l.lot_no LIKE @run GROUP BY l.id, l.status;""")
    missed = [int(lot_id) for lot_id, status, judged, defects, held in lots
              if status == "COMPLETED" and held == "0" and int(judged) >= HOLD_MIN_JUDGED
              and binomial_tail(int(judged), int(defects), HOLD_BASELINE) < HOLD_SIGNIFICANCE]
    checks["auto_hold_missed"] = len(missed)
    stored = {key: inspection_id for key, inspection_id in sql(
        f"SELECT idempotency_key, id FROM inspection WHERE idempotency_key LIKE '{run_id}-%';")}
    checks["accepted_not_stored"] = sum(1 for key, inspection_id in accepted.items() if stored.get(key) != inspection_id)
    statuses = {}
    for status, count in sql(prelude + "SELECT status, COUNT(*) FROM lot WHERE lot_no LIKE @run GROUP BY status;"):
        statuses[status] = int(count)
    return checks, statuses, missed[:20]


def digests(top=15):
    rows = sql("""SELECT COUNT_STAR, ROUND(SUM_TIMER_WAIT/1e9, 1), ROUND(SUM_LOCK_TIME/1e9, 1),
                         LEFT(REPLACE(DIGEST_TEXT, '`', ''), 140)
                  FROM performance_schema.events_statements_summary_by_digest
                  WHERE SCHEMA_NAME = 'appdb' ORDER BY COUNT_STAR DESC;""")
    total = sum(int(r[0]) for r in rows)
    return total, [{"count": int(c), "total_ms": float(t), "lock_ms": float(l), "sql": s} for c, t, l, s in rows[:top]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--rate", type=float, required=True)
    parser.add_argument("--duration", default="60s")
    parser.add_argument("--warmup", default="15s")
    # backend를 막 띄웠다면 JIT가 덜 된 JVM이 목표 부하에서 포화된다. 낮은 부하로 충분히 데운 뒤 측정한다
    parser.add_argument("--warmup-rate", type=float, help="워밍업 도착률 (기본: --rate)")
    parser.add_argument("--env", action="append", default=[], help="k6에 넘길 KEY=VALUE")
    parser.add_argument("--out", default=str(HERE / "results"))
    args = parser.parse_args()

    env = dict(item.split("=", 1) for item in args.env)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seconds = int(args.duration.rstrip("s"))
    run_id = f"{args.label}-{int(time.time())}"

    if args.warmup != "0s":
        k6(args.scenario, args.warmup_rate or args.rate, args.warmup, env, f"{run_id}w", out / f"{args.label}.warmup.json")

    lots = preset_lots(args.scenario, args.rate, seconds, env, run_id)
    lots_file = None
    if lots is not None:
        lots_file = out / f"{args.label}.lots.json"
        lots_file.write_text(json.dumps(lots))

    sql("TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;")
    before, prom_before = status_snapshot(), prometheus()
    sampler = Sampler()
    sampler.start()
    started = time.time()
    accepted, k6_errors = k6(args.scenario, args.rate, args.duration, env, run_id, out / f"{args.label}.k6.json", lots_file)
    elapsed = time.time() - started
    samples = sampler.finish()
    time.sleep(2)
    after, prom_after = status_snapshot(), prometheus()

    endpoints, dropped = k6_summary(out / f"{args.label}.k6.json")
    requests = sum(e["count"] for e in endpoints.values()) or 1
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in after}
    statements, top = digests()
    lock_count, lock_sum = (a - b for a, b in zip(lot_lock_stats(prom_after), lot_lock_stats(prom_before)))
    checks, lot_status, missed = integrity(run_id, accepted)

    result = {
        "label": args.label, "scenario": args.scenario, "rate": args.rate, "duration_s": seconds, "env": env,
        "run_id": run_id, "elapsed_s": round(elapsed, 1),
        "endpoints": endpoints, "dropped_iterations": dropped,
        "per_request": {
            "statements": round(statements / requests, 1),
            "questions": round(delta["Questions"] / requests, 1),
            "select": round(delta["Com_select"] / requests, 1),
            "insert": round(delta["Com_insert"] / requests, 1),
            "update": round(delta["Com_update"] / requests, 1),
            "commit": round(delta["Com_commit"] / requests, 2),
            "set_option": round(delta["Com_set_option"] / requests, 1),
            "log_fsyncs": round(delta["Innodb_os_log_fsyncs"] / requests, 2),
            "binlog_bytes": round(delta["binlog_bytes"] / requests),
            "rows_inserted": round(delta["Innodb_rows_inserted"] / requests, 1),
        },
        "locks": {
            "row_lock_waits": int(delta["Innodb_row_lock_waits"]),
            "row_lock_time_ms": int(delta["Innodb_row_lock_time"]),
            "row_lock_avg_ms": round(delta["Innodb_row_lock_time"] / delta["Innodb_row_lock_waits"], 1)
            if delta["Innodb_row_lock_waits"] else 0,
            "deadlocks": int(delta.get("lock_deadlocks", 0)),
            "lock_timeouts": int(delta.get("lock_timeouts", 0)),
            "lot_lock_calls": int(lock_count),
            "lot_lock_avg_ms": round(lock_sum / lock_count * 1000, 2) if lock_count else 0,
            **{k: v for k, v in samples.items() if k != "cpu_percent"},
        },
        "cpu_percent": samples["cpu_percent"],
        "integrity": checks, "lot_status": lot_status, "auto_hold_missed_lots": missed,
        "accepted": len(accepted), "top_statements": top, "k6_errors": k6_errors[:10],
    }
    (out / f"{args.label}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))

    print(f"== {args.label}  ({args.scenario} @ {args.rate}/s, {seconds}s, dropped={dropped})")
    for name, e in endpoints.items():
        print(f"  {name:22} n={e['count']:<6} {e['rps']:>7}/s  p50={e['p50']:>8} p95={e['p95']:>8} "
              f"p99={e['p99']:>8} max={e['max']:>8}  {e['status']}")
    print(f"  per request: {result['per_request']}")
    print(f"  locks: {result['locks']}")
    print(f"  cpu: {result['cpu_percent']}")
    violations = {k: v for k, v in checks.items() if v}
    print(f"  integrity: {'OK' if not violations else violations}  lots={lot_status}")


if __name__ == "__main__":
    main()
