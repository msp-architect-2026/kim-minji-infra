#!/usr/bin/env python3
"""개선 전후 결과를 조건별 중앙값으로 비교한다. 라벨은 <jar>-<조건>-<반복 번호> 형식이어야 한다.

  python3 loadtest/compare.py results/cmp --a before --b sql-opt
"""
import argparse
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

METRICS = [
    ("성공 TPS", lambda r, e: e["status"].get("200", 0) / r["duration_s"]),
    ("p95 ms", lambda r, e: e["p95"]),
    ("p99 ms", lambda r, e: e["p99"]),
    ("누락 반복", lambda r, e: r["dropped_iterations"]),
    ("SQL/요청", lambda r, e: r["per_request"]["statements"]),
    ("commit/요청", lambda r, e: r["per_request"]["commit"]),
    ("fsync/요청", lambda r, e: r["per_request"]["log_fsyncs"]),
    ("binlog B/요청", lambda r, e: r["per_request"]["binlog_bytes"]),
    ("MySQL CPU % 평균", lambda r, e: r["cpu_percent"].get("mysql", {}).get("avg", 0)),
    ("backend CPU % 평균", lambda r, e: r["cpu_percent"].get("backend", {}).get("avg", 0)),
    ("행 잠금 대기 횟수", lambda r, e: r["locks"]["row_lock_waits"]),
    ("Lot 잠금 쿼리 평균 ms", lambda r, e: r["locks"]["lot_lock_avg_ms"]),
    ("Hikari 대기 최대", lambda r, e: r["locks"]["hikari_pending_max"]),
    ("정합성 위반", lambda r, e: sum(r["integrity"].values())),
]


def load(directory, jar):
    groups = defaultdict(list)
    for path in Path(directory).glob(f"{jar}-*.json"):
        if path.name.endswith((".k6.json", ".lots.json", ".warmup.json")):
            continue
        match = re.fullmatch(rf"{re.escape(jar)}-(.+)-(\d+)\.json", path.name)
        if match:
            groups[match.group(1)].append(json.loads(path.read_text()))
    return groups


def median(results, endpoint, extract):
    values = [extract(r, r["endpoints"][endpoint]) for r in results if endpoint in r["endpoints"]]
    return statistics.median(values) if values else None


def fmt(value):
    if value is None:
        return "-"
    return f"{value:,.0f}" if abs(value) >= 100 else f"{value:,.2f}".rstrip("0").rstrip(".")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--a", default="before")
    parser.add_argument("--b", default="sql-opt")
    args = parser.parse_args()
    a, b = load(args.directory, args.a), load(args.directory, args.b)
    for config in sorted(set(a) & set(b)):
        endpoints = sorted(a[config][0]["endpoints"])
        for endpoint in endpoints:
            print(f"\n#### {config} — {endpoint} (각 {len(a[config])}회 / {len(b[config])}회 중앙값)\n")
            print(f"| 지표 | {args.a} | {args.b} | 변화 |\n|---|---|---|---|")
            for name, extract in METRICS:
                va, vb = median(a[config], endpoint, extract), median(b[config], endpoint, extract)
                change = "-"
                if va not in (None, 0) and vb is not None:
                    change = f"{(vb - va) / va * 100:+.0f}%"
                print(f"| {name} | {fmt(va)} | {fmt(vb)} | {change} |")


if __name__ == "__main__":
    main()
