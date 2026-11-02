#!/usr/bin/env bash
# 장애 주입 토글 (RUNBOOK 4.15): ai-serving Pod 하나만 /predict가 503을 반환하게 한다 (/health는 그대로 200).
# Service를 거치면 어느 Pod가 받을지 모르므로 kubectl exec로 그 Pod 안에서 직접 호출한다.
#   loadtest/fault.sh status
#   loadtest/fault.sh on  [pod] [delay_seconds]   (pod 생략 시 이름순 첫 번째)
#   loadtest/fault.sh off [pod]
set -euo pipefail

: "${KUBECONFIG:?k3s용 KUBECONFIG 파일을 지정한다 (맥 기본 컨텍스트는 EKS)}"
ctx=$(kubectl config current-context)
case "$ctx" in
  *eks*|arn:aws:*) echo "EKS 컨텍스트($ctx)에서는 실행하지 않는다" >&2; exit 2 ;;
esac

NS=ai-serving
# Running 중 종료 대기(deletionTimestamp 있음)인 Pod는 뺀다 (롤아웃 직후 곧 사라질 Pod를 고르지 않게)
pods() {
  kubectl -n "$NS" get pods -l app.kubernetes.io/name=ai-serving --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.deletionTimestamp}{"\n"}{end}' | awk 'NF == 1 {print $1}' | sort
}
# $1: Pod, $2: JSON 본문(비우면 GET)
call() {
  kubectl -n "$NS" exec "$1" -c ai-serving -- python -c '
import sys, urllib.request as u
body = sys.argv[1].encode() or None
req = u.Request("http://127.0.0.1:8000/admin/fault", data=body, headers={"Content-Type": "application/json"})
print(u.urlopen(req).read().decode())' "$2"
}
now() { python3 -c 'import datetime; print(datetime.datetime.now().astimezone().isoformat(timespec="milliseconds"))'; }

cmd="${1:-status}"
case "$cmd" in
  # 호출 결과는 변수에 받는다 (echo 인자 안의 $(...)는 실패해도 set -e가 멈추지 않는다)
  status)
    for p in $(pods); do out=$(call "$p" ''); echo "$p $out"; done ;;
  on)
    pod="${2:-$(pods | head -1)}"
    out=$(call "$pod" "{\"enabled\": true, \"delay_seconds\": ${3:-0}}")
    echo "$(now) fault ON  $pod $out" ;;
  off)
    pod="${2:-$(pods | head -1)}"
    out=$(call "$pod" '{"enabled": false}')
    echo "$(now) fault OFF $pod $out" ;;
  *)
    echo "사용법: $0 status | on [pod] [delay_seconds] | off [pod]" >&2; exit 1 ;;
esac
