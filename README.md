<h1 align="center">kim-minji-infra</h1>

<p align="center">
  웨이퍼 결함 탐지 시스템의 <strong>k3s 클러스터 및 GitOps 인프라 구성 레포지토리</strong>입니다.
</p>

<br>

<p align="center">
<img src="https://img.shields.io/badge/k3s-FFC61C?style=for-the-badge&logo=kubernetes&logoColor=black"/>
<img src="https://img.shields.io/badge/ArgoCD-FE5D26?style=for-the-badge&logo=argo&logoColor=white"/>
<img src="https://img.shields.io/badge/Helm-0F1689?style=for-the-badge&logo=helm&logoColor=white"/>
<img src="https://img.shields.io/badge/Calico-FB8C00?style=for-the-badge&logo=linux&logoColor=white"/>
<img src="https://img.shields.io/badge/Prometheus-E6522C?style=for-the-badge&logo=prometheus&logoColor=white"/>
<img src="https://img.shields.io/badge/Grafana-F46800?style=for-the-badge&logo=grafana&logoColor=white"/>
<img src="https://img.shields.io/badge/Loki-2C3E50?style=for-the-badge&logo=grafana&logoColor=white"/>
</p>

---

## ▍개요

이 레포는 멀티노드 k3s 클러스터 위에서 웨이퍼 결함 탐지 서비스를 안정적으로 운영하기 위한 **인프라 구성 전체를 GitOps 방식으로 관리**합니다.

Traefik Ingress, MetalLB, cert-manager, Longhorn, Sealed Secrets, Prometheus+Grafana 모니터링 스택, Loki 로깅, 백업 CronJob, Calico NetworkPolicy 등 클러스터 공통 인프라가 모두 이 레포의 Helm Chart로 정의되어 있으며, ArgoCD가 이를 감지해 자동 배포합니다.

---


## ▍관련 레포지토리

| Repository | 설명 |
|------------|------|
| [kim-minji-wiki](https://github.com/msp-architect-2026/kim-minji-wiki) | 프로젝트 메인 (Wiki, 칸반보드) |
| [kim-minji-helm](https://github.com/msp-architect-2026/kim-minji-helm) | 애플리케이션 Helm Chart |
| [kim-minji-backend](https://github.com/msp-architect-2026/kim-minji-backend) | Spring Boot API 서버 |
| [kim-minji-frontend](https://github.com/msp-architect-2026/kim-minji-frontend) | React 웹 대시보드 |
| [kim-minji-ai](https://github.com/msp-architect-2026/kim-minji-ai) | FastAPI AI 추론 서비스 |

---

## ▍노드 구성

| 노드 | IP | 역할 | 주요 워크로드 |
|------|-----|------|----------------|
| k3s-cp1 | 192.168.0.57 | Control Plane | API Server, ArgoCD, Traefik |
| k3s-w1 | 192.168.0.157 | Worker (RAM 12G) | frontend, backend, mysql, minio, GitLab CE |
| k3s-ai2 | 192.168.0.240 | AI 전용 Worker | ai-serving (HPA 포함) |

> AI 워크로드가 다른 서비스에 영향을 주지 않도록 전용 노드를 분리했습니다.

---

## ▍레포 구조

```
kim-minji-infra/
├── sealed-secrets/   # Sealed Secrets controller (kube-system, 기존 키 인계)
├── metallb/          # MetalLB L2 + IPAddressPool
├── cert-manager/     # cert-manager + 자체 서명 루트 CA ClusterIssuer(wafer-ca)
├── longhorn/         # Longhorn 블록 스토리지 (dev 복제 1 / full 2, cp1·w1 디스크)
├── traefik/          # Traefik Ingress Controller (ingress-nginx 대체)
├── ingress/          # 서비스별 Ingress 규칙 (class: traefik)
├── monitoring/       # kube-prometheus-stack (Prometheus + Grafana)
├── loki/             # Loki + Promtail 로깅 스택
├── argocd-notifications/ # ArgoCD sync 실패·Degraded → Slack
├── backup/           # 백업 Helm 차트: MySQL·MinIO 백업(클러스터 내 + 외부 S3), 주간 복원 검증
├── network-policy/   # Calico NetworkPolicy (네임스페이스 간 접근 제어)
├── coredns/          # CoreDNS replica 2 + 노드 분산 배치 (k3s 기본 Deployment에 SSA 패치)
├── node-local-dns/   # NodeLocal DNSCache DaemonSet
├── argocd/
│   ├── bootstrap/    # root App of Apps, in-cluster 클러스터 Secret(env 라벨) — 최초 1회 수동 적용
│   └── apps/         # ApplicationSet: platform(이 레포 차트), apps(kim-minji-helm 차트)
├── scripts/          # validate.sh(정적 검증), resource-summary.py(requests 합계)
└── docs/RUNBOOK.md   # 클러스터 작업 절차
```

---

## ▍프로파일 (dev / full)

| 프로파일 | 용도 | 주요 차이 |
|----------|------|-----------|
| `dev` (기본) | 노드 1~2대만 켠 개발 환경 | CoreDNS replica 1·ScheduleAnyway, Prometheus 보존 2d·resource 최소, Loki resource 최소, Traefik 1, Longhorn 복제 1·CSI 1 |
| `full` | 3노드 To-Be | CoreDNS replica 2·DoNotSchedule, Prometheus 보존 7d, Traefik 2·PDB·노드 분산, Longhorn 복제 2·CSI 2 |

- 차트마다 `values.yaml`(공통) + `values-dev.yaml` / `values-full.yaml`(있을 때만)을 겹쳐 씁니다.
- ApplicationSet은 in-cluster 클러스터 Secret의 `env` 라벨로 프로파일을 고릅니다. Secret이나 라벨이 없으면 `dev`입니다.
- 전환 절차와 프로파일별 resource 합계는 [`docs/RUNBOOK.md`](docs/RUNBOOK.md) 4.3, 8장을 참고합니다.

---

## ▍네임스페이스 구성

| 네임스페이스 | 워크로드 |
|-------------|---------|
| `application` | frontend (React), backend (Spring Boot) |
| `ai-serving` | ai-serving (FastAPI), HPA |
| `storage` | MySQL, MinIO |
| `traefik` | Traefik Ingress Controller |
| `metallb-system` / `cert-manager` / `longhorn-system` | MetalLB / cert-manager / Longhorn |
| `gitops` | ArgoCD |
| `monitoring` | Prometheus, Grafana, Loki, Promtail |

---

## ▍Ingress 라우팅

```
맥북 브라우저
└─ *.wafer.local:32088
   └─ /etc/hosts → 192.168.0.57 (또는 MetalLB가 준 Traefik LB IP, 포트 80)
      └─ Traefik (NodePort 32088 / LoadBalancer)
         ├─ app.wafer.local      → application/frontend:80
         ├─ api.wafer.local      → application/backend:8080
         ├─ ai.wafer.local       → ai-serving/ai-serving:8000
         └─ grafana.wafer.local  → monitoring/grafana:80
```

---

## ▍NetworkPolicy 설계

Calico NetworkPolicy로 네임스페이스 간 접근 제어를 구현했습니다.

| 대상 | 인바운드 허용 출처 |
|------|------------------|
| frontend | traefik |
| backend | traefik, frontend, monitoring (Prometheus) |
| ai-serving | traefik, backend, monitoring (Prometheus) |
| mysql | backend, storage (백업 CronJob) |
| minio | backend, ai-serving, storage (백업 CronJob) |

---

## ▍모니터링 스택

```
monitoring 네임스페이스
├─ Prometheus       : 메트릭 수집 (backend, ai-serving ServiceMonitor 30s interval), 알림 규칙(wafer-rules)
├─ Alertmanager     : Slack Incoming Webhook으로 알림 (dev는 꺼 둔 노드 관련 알림 음소거)
├─ Grafana          : 대시보드 시각화 (grafana.wafer.local, anonymous 접근, iframe 임베드)
├─ node-exporter    : VM 레벨 메트릭 (CPU, 메모리, 디스크)
├─ kube-state-metrics: Pod / Deployment 상태
├─ Loki             : 로그 저장
└─ Promtail         : 각 노드 로그 수집 (DaemonSet)
```

**Grafana 대시보드**
- Kubernetes Compute Resources (기본 제공)
- Backend 요청 처리율 / 평균 응답시간 / 에러율
- AI 추론 요청 처리율 / 평균 응답시간
- 로그 대시보드 (Loki 연동)

---

## ▍백업 CronJob

`backup/` Helm 차트 (platform ApplicationSet, `storage` 네임스페이스)

| 대상 | 스케줄 | 방식 | 저장 위치 |
|------|--------|------|-----------|
| MySQL | 매일 02:00 | `mysqldump --single-transaction` + gzip | 클러스터 내 MinIO `wafer-backup/mysql/`(7일) + 외부 S3 `{prefix}/mysql/`(14일, `external.enabled` 시) |
| MinIO | 매일 03:00 | `mc mirror --overwrite` (삭제는 반영 안 함) | `wafer-backup/{bucket}` + 외부 S3 `{prefix}/minio/{bucket}` (`external.enabled` 시) |
| 복원 검증 | 매주 일 04:00 | 최신 덤프를 임시 DB(`restore_check`)에 복원 → 테이블·행 수 확인 → 삭제 | — |

- 계정은 `mysql-secret`, `minio-secret`, `backup-s3`(외부 S3) SealedSecret에서 읽고, 명령행에 노출하지 않습니다 (`MYSQL_PWD`, `MC_HOST_*`).
- 실패하면 Job이 실패로 끝납니다 (`set -euo pipefail`, `backoffLimit`). 외부 S3 주소·계정은 RUNBOOK C16, 5.3.
- Longhorn 볼륨은 매일 스냅샷(3개 보관), 백업 대상(외부 S3)을 정하면 매일 백업(7개 보관).
- 커스텀 이미지: `debian:bookworm-slim` + `mysql-client` + `mc` (ARM64)

---

## ▍ArgoCD syncPolicy

```yaml
# platform(이 레포 차트), apps(kim-minji-helm 차트) ApplicationSet 공통
syncPolicy:
  automated:
    prune: true       # Git에서 지운 리소스 자동 삭제
    selfHeal: true    # 수동 변경 감지 시 자동 복구
  syncOptions:
    - CreateNamespace=true
    - ServerSideApply=true  # coredns(부분 패치), CRD가 큰 차트 — templatePatch로 적용
  retry: { limit: 5, backoff: { duration: 10s, factor: 2, maxDuration: 3m } }
```

- 앱 사이 배포 순서는 강제하지 않고 retry로 수렴합니다 (기대 순서와 확인 방법은 RUNBOOK 4장).
- 유지보수 중에는 Application별로 자동 sync를 잠시 끌 수 있습니다 (`ignoreApplicationDifferences`, RUNBOOK 4.12).

---

## ▍CNI: Calico

```
Pod IP 대역:  10.42.0.0/16   (노드 IP 192.168.0.x와 완전 분리)
vxlanMode:   Always          (노드 간 Pod 통신 VXLAN 터널 encapsulation)
natOutgoing: true            (Pod → 외부 NAT)
```

---

## ▍CoreDNS Custom 설정

```
gitlab.local:53 {
  hosts {
    192.168.0.157 gitlab.local
    fallthrough
  }
}
```

클러스터 내부에서 `gitlab.local → 192.168.0.157`로 DNS 해석하여 gitlab-runner 및 각 Pod의 Registry 이미지 pull을 지원합니다.

---

## ▍DNS 구성

```
Pod (dnsConfig ndots:2)
└─ 10.43.0.10 (kube-dns Service IP)
   └─ 같은 노드의 NodeLocal DNSCache (hostNetwork, iptables로 가로챔)
      ├─ 캐시 hit → 바로 응답
      └─ 캐시 miss → kube-dns-upstream → CoreDNS (replica 2, 노드 분산)
```

| 구성 | 설정 | 이유 |
|------|------|------|
| Pod `dnsConfig` | `ndots: 2` (kim-minji-helm backend, ai-serving) | `*.svc.cluster.local` FQDN 조회 시 search domain 확장 질의 제거 |
| NodeLocal DNSCache | DaemonSet, `169.254.20.10` + `10.43.0.10` listen, CPU limit 1000m | 노드 단위 캐싱으로 CoreDNS 부하·조회 지연 감소. limit이 낮으면 throttling으로 캐시가 병목 |
| NodeLocal 외부 이름(`.`) | CoreDNS로 forward | CoreDNS custom 설정(`gitlab.local`)을 그대로 따름 |
| CoreDNS | replica 2, `topologySpreadConstraints` (hostname, `DoNotSchedule`) | 단일 노드 장애 시에도 다른 노드의 replica가 응답 |

- NodeLocal DNSCache는 kube-proxy **iptables 모드** 기준입니다. kubelet `--cluster-dns` 변경은 필요 없습니다.
- CoreDNS 본체는 k3s가 관리합니다. `coredns/` Application은 Server-Side Apply로 `replicas`와 `topologySpreadConstraints`만 덧씌우고, `Prune=false,Delete=false`로 Application 삭제 시에도 CoreDNS를 지우지 않습니다.
- `coredns/` Application을 지워도 replicas는 2로 남습니다. 되돌리려면 `kubectl -n kube-system scale deploy coredns --replicas=1`을 직접 실행합니다.
- 배포 순서: `coredns` → CoreDNS 2개 Ready·노드 분산 확인 → `node-local-dns`.
- NodeLocal 롤백: `kubectl -n kube-system delete ds node-local-dns`. node-cache가 종료 시 iptables 규칙과 `nodelocaldns` 인터페이스를 정리해 kube-proxy → CoreDNS 경로로 돌아갑니다.

---

## ▍정적 검증 / 클러스터 작업

```bash
scripts/validate.sh          # dev, full 모두
scripts/validate.sh dev      # 특정 프로파일만
```

- 클러스터 접속 없이 `helm lint`, `helm template`, `kubeconform`(CRD는 datreeio CRDs-catalog), `yamllint`를 실행합니다.
- 대상 차트는 `scripts/releases.txt`, raw 매니페스트는 `scripts/manifests.txt`에 등록합니다.
- 필요 도구: `helm`, `kubeconform`, `yamllint`
- 코드 머지 후 클러스터에서 수행할 작업은 [`docs/RUNBOOK.md`](docs/RUNBOOK.md)에 순서대로 정리합니다.
