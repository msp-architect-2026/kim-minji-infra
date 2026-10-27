#!/usr/bin/env bash
# 클러스터 없이 dev/full 프로파일을 정적으로 검증한다.
#   1) helm lint / helm template (차트 × 프로파일)
#   2) kubeconform 스키마 검증 (CRD는 datreeio CRDs-catalog)
#   3) yamllint (values 파일, raw 매니페스트)
# 사용법: scripts/validate.sh [dev|full ...]   (기본: dev full)
# 대상 목록: scripts/releases.txt (차트 릴리스 네임스페이스 [skip-schema]), scripts/manifests.txt (raw 매니페스트 파일·디렉터리)
set -uo pipefail

cd "$(dirname "$0")/.."
PROFILES=("$@")
[ $# -eq 0 ] && PROFILES=(dev full)
K8S_VERSION="${K8S_VERSION:-1.31.0}"   # 클러스터 k3s 버전 확정 후 맞춘다 (RUNBOOK 확정 필요)
OUT="${OUT:-$(mktemp -d)}"
CRD_CATALOG='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
YAMLLINT_CONF='{extends: relaxed, rules: {line-length: disable, trailing-spaces: disable, new-line-at-end-of-file: disable}}'

for bin in helm kubeconform yamllint; do
  command -v "$bin" >/dev/null || { echo "필요한 도구 없음: $bin" >&2; exit 2; }
done

failed=()
kubeconform_run() {
  kubeconform -kubernetes-version "$K8S_VERSION" -strict -summary -ignore-missing-schemas \
    -schema-location default -schema-location "$CRD_CATALOG" "$@"
}

while read -r chart release namespace opts; do
  case "$chart" in ''|\#*) continue ;; esac

  if grep -q '^dependencies:' "$chart/Chart.yaml" && ! ls "$chart"/charts/*.tgz >/dev/null 2>&1; then
    helm dependency build "$chart" >/dev/null || { failed+=("$chart: dependency build"); continue; }
  fi
  yamllint -d "$YAMLLINT_CONF" "$chart"/values*.yaml || failed+=("$chart: yamllint")

  for profile in "${PROFILES[@]}"; do
    args=(-f "$chart/values.yaml")
    [ -f "$chart/values-$profile.yaml" ] && args+=(-f "$chart/values-$profile.yaml")
    # ci/test-values.yaml: 검증용 더미 값 (SealedSecret 암호문 자리 등). 배포에는 쓰지 않는다.
    [ -f "$chart/ci/test-values.yaml" ] && args+=(-f "$chart/ci/test-values.yaml")
    rendered="$OUT/$profile/$release.yaml"
    mkdir -p "$OUT/$profile"

    echo "=== [$profile] $chart (release=$release, ns=$namespace)"
    helm lint "$chart" "${args[@]}" --quiet || failed+=("[$profile] $chart: helm lint")
    if helm template "$release" "$chart" -n "$namespace" "${args[@]}" > "$rendered"; then
      if [ "${opts:-}" = skip-schema ]; then
        echo "kubeconform 생략 (skip-schema: 부분 매니페스트)"
      else
        kubeconform_run "$rendered" || failed+=("[$profile] $chart: kubeconform")
      fi
    else
      failed+=("[$profile] $chart: helm template")
    fi
  done
done < scripts/releases.txt

if [ -f scripts/manifests.txt ]; then
  while read -r path; do
    case "$path" in ''|\#*) continue ;; esac
    echo "=== [raw] $path"
    yamllint -d "$YAMLLINT_CONF" "$path" || failed+=("$path: yamllint")
    kubeconform_run "$path" || failed+=("$path: kubeconform")
  done < scripts/manifests.txt
fi

echo
echo "렌더링 결과: $OUT"
if [ ${#failed[@]} -gt 0 ]; then
  echo "실패 ${#failed[@]}건:"
  printf '  - %s\n' "${failed[@]}"
  exit 1
fi
echo "전체 통과 (profiles: ${PROFILES[*]})"
