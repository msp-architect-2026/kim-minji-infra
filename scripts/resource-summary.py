#!/usr/bin/env python3
"""렌더링된 매니페스트에서 워크로드별 resource requests 합계를 Markdown 표로 출력한다.

사용법:
  scripts/resource-summary.py --nodes 1 <렌더링 디렉터리 또는 파일> ...
  예) OUT=/tmp/r scripts/validate.sh dev && scripts/resource-summary.py --nodes 1 /tmp/r/dev

계산 규칙:
  - Deployment/StatefulSet/Rollout: replicas × 컨테이너 requests 합 (HPA·KEDA ScaledObject가 있으면 최소, 최대치는 별도 열)
  - DaemonSet: --nodes(켜져 있는 노드 수) × 컨테이너 requests 합
  - Prometheus·Alertmanager(CR): spec.replicas(기본 1) × spec.resources.requests
  - requests가 없는 컨테이너는 0으로 세고 "미설정"으로 표시한다
필요: PyYAML (pip install pyyaml)
"""
import argparse
import pathlib
import sys

import yaml

POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Rollout"}


def cpu_m(v):
    if v is None:
        return 0
    s = str(v)
    return int(float(s[:-1])) if s.endswith("m") else int(float(s) * 1000)


def mem_mi(v):
    if v is None:
        return 0
    s = str(v)
    units = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024 * 1024,
             "K": 1000 / 1024 / 1024, "M": 1000 * 1000 / 1024 / 1024, "G": 1000 ** 3 / 1024 / 1024}
    for u in sorted(units, key=len, reverse=True):
        if s.endswith(u):
            return float(s[: -len(u)]) * units[u]
    return float(s) / 1024 / 1024


def load_docs(paths):
    for p in paths:
        p = pathlib.Path(p)
        files = sorted(p.rglob("*.yaml")) if p.is_dir() else [p]
        for f in files:
            for doc in yaml.safe_load_all(f.read_text()):
                if isinstance(doc, dict) and doc.get("kind"):
                    yield f.stem, doc


def requests_of(containers):
    cpu = mem = 0
    unset = False
    for c in containers or []:
        req = (c.get("resources") or {}).get("requests") or {}
        if not req:
            unset = True
        cpu += cpu_m(req.get("cpu"))
        mem += mem_mi(req.get("memory"))
    return cpu, mem, unset


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nodes", type=int, required=True, help="DaemonSet 배수(켜져 있는 노드 수)")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()

    docs = list(load_docs(args.paths))
    hpa = {}
    for _, d in docs:
        if d["kind"] == "HorizontalPodAutoscaler":
            ref = d["spec"]["scaleTargetRef"]
            # Helm 차트는 namespace를 비워 두는 경우가 많아 (kind, name)으로 맞춘다
            hpa[(ref["kind"], ref["name"])] = d["spec"]
        elif d["kind"] == "ScaledObject":
            ref, spec = d["spec"]["scaleTargetRef"], d["spec"]
            hpa[(ref.get("kind", "Deployment"), ref["name"])] = {
                "minReplicas": spec.get("minReplicaCount", 0), "maxReplicas": spec.get("maxReplicaCount", 100)}

    rows = []
    for release, d in docs:
        kind, meta, spec = d["kind"], d["metadata"], d.get("spec") or {}
        if kind in POD_KINDS:
            cpu, mem, unset = requests_of(spec["template"]["spec"].get("containers"))
            if kind == "DaemonSet":
                n, n_max = args.nodes, args.nodes
            else:
                n = spec.get("replicas", 1)
                n_max = n
                h = hpa.get((kind, meta["name"]))
                if h:
                    n, n_max = h.get("minReplicas", 1), h["maxReplicas"]
        elif kind in ("Prometheus", "Alertmanager"):
            cpu, mem, unset = requests_of([{"resources": spec.get("resources")}])
            n = n_max = spec.get("replicas", 1)
        else:
            continue
        rows.append((release, kind, meta["name"], n, n_max, cpu, mem, unset))

    rows.sort()
    print("| 릴리스 | 종류 | 이름 | Pod 수 (최대) | requests/Pod (CPU · 메모리) | 합계 CPU | 합계 메모리 |")
    print("|---|---|---|---|---|---|---|")
    tot_cpu = tot_mem = max_cpu = max_mem = 0
    for release, kind, name, n, n_max, cpu, mem, unset in rows:
        note = " (미설정 포함)" if unset else ""
        per = f"{cpu}m · {mem:.0f}Mi{note}"
        cnt = f"{n}" if n == n_max else f"{n} ({n_max})"
        print(f"| {release} | {kind} | {name} | {cnt} | {per} | {cpu * n}m | {mem * n:.0f}Mi |")
        tot_cpu += cpu * n
        tot_mem += mem * n
        max_cpu += cpu * n_max
        max_mem += mem * n_max
    print(f"| **합계** | | | | | **{tot_cpu}m** | **{tot_mem / 1024:.2f}Gi** |")
    if (max_cpu, max_mem) != (tot_cpu, tot_mem):
        print(f"| **HPA 최대치 기준** | | | | | **{max_cpu}m** | **{max_mem / 1024:.2f}Gi** |")


if __name__ == "__main__":
    sys.exit(main())
