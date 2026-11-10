#!/usr/bin/env python3
"""bench.py 결과 JSON들을 마크다운 표로 모은다.

  python3 loadtest/report.py results/baseline/*.json
"""
import argparse
import json
from pathlib import Path


def load(paths):
    return [json.loads(Path(p).read_text()) for p in paths if not p.endswith((".k6.json", ".lots.json", ".warmup.json"))]


def row(r):
    eps = r["endpoints"]
    per = r["per_request"]
    locks = r["locks"]
    cpu = r["cpu_percent"]
    violations = sum(v for v in r["integrity"].values())
    for name, e in eps.items():
        ok = e["status"].get("200", 0)
        yield (f"| {r['label']} | {name} | {r['rate']:g} | {e['rps']} | {round(ok / r['duration_s'], 1)} | {e['p95']} | {e['p99']} "
               f"| {per['statements']} | {per['commit']} | {per['log_fsyncs']} "
               f"| {locks['row_lock_waits']} / {locks['row_lock_avg_ms']} | {locks['lot_lock_avg_ms']} / {round(locks['lot_lock_query_max_s'] * 1000)} "
               f"| {locks['hikari_pending_max']:g} "
               f"| {cpu.get('mysql', {}).get('avg', '-')} / {cpu.get('mysql', {}).get('max', '-')} "
               f"| {cpu.get('backend', {}).get('avg', '-')} / {cpu.get('backend', {}).get('max', '-')} "
               f"| {r['dropped_iterations']} | {violations} |")


HEADER = ("| 실행 | 엔드포인트 | 목표 rps | 처리 rps | 성공 TPS | p95 ms | p99 ms | SQL/요청 | commit/요청 | fsync/요청 "
          "| 행 잠금 대기 횟수 / 평균 ms | Lot 잠금 쿼리 평균 / 최대 ms | Hikari 대기 최대 | MySQL CPU % 평균 / 최대 "
          "| backend CPU % 평균 / 최대 | 누락 반복 | 정합성 위반 |\n"
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    print(HEADER)
    for r in load(args.files):
        for line in row(r):
            print(line)
    for r in load(args.files):
        violations = {k: v for k, v in r["integrity"].items() if v}
        if violations:
            print(f"\n- {r['label']} 정합성 위반: {violations} (Hold 누락 Lot: {r['auto_hold_missed_lots']})")


if __name__ == "__main__":
    main()
