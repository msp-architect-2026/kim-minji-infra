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

Nginx Ingress, Prometheus+Grafana 모니터링 스택, Loki 로깅, 백업 CronJob, Calico NetworkPolicy 등 클러스터 공통 인프라가 모두 이 레포의 Helm Chart로 정의되어 있으며, ArgoCD가 이를 감지해 자동 배포합니다.

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
| k3s-cp1 | 192.168.0.57 | Control Plane | API Server, ArgoCD, Nginx Ingress |
| k3s-w1 | 192.168.0.157 | Worker (RAM 12G) | frontend, backend, mysql, minio, GitLab CE |
| k3s-ai2 | 192.168.0.240 | AI 전용 Worker | ai-serving (HPA 포함) |

> AI 워크로드가 다른 서비스에 영향을 주지 않도록 전용 노드를 분리했습니다.

---

## ▍레포 구조

```
kim-minji-infra/
├── ingress/          # Nginx Ingress Controller 설정
├── monitoring/       # kube-prometheus-stack (Prometheus + Grafana)
├── loki/             # Loki + Promtail 로깅 스택
├── backup/           # MySQL / MinIO 백업 CronJob
├── network-policy/   # Calico NetworkPolicy (네임스페이스 간 접근 제어)
├── coredns/          # CoreDNS replica 2 + 노드 분산 배치 (k3s 기본 Deployment에 SSA 패치)
├── node-local-dns/   # NodeLocal DNSCache DaemonSet
└── argocd/           # ArgoCD Application 매니페스트
```

---

## ▍네임스페이스 구성

| 네임스페이스 | 워크로드 |
|-------------|---------|
| `application` | frontend (React), backend (Spring Boot) |
| `ai-serving` | ai-serving (FastAPI), HPA |
| `storage` | MySQL, MinIO |
| `infra` | Nginx Ingress Controller |
| `gitops` | ArgoCD |
| `monitoring` | Prometheus, Grafana, Loki, Promtail |

---

## ▍Ingress 라우팅

```
맥북 브라우저
└─ *.wafer.local:32088
   └─ /etc/hosts → 192.168.0.57
      └─ Nginx Ingress (NodePort 32088)
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
| frontend | ingress-nginx |
| backend | ingress-nginx, frontend, monitoring (Prometheus) |
| ai-serving | ingress-nginx, backend, monitoring (Prometheus) |
| mysql | backend, storage (백업 CronJob) |
| minio | backend, ai-serving, storage (백업 CronJob) |

---

## ▍모니터링 스택

```
monitoring 네임스페이스
├─ Prometheus       : 메트릭 수집 (backend, ai-serving ServiceMonitor 30s interval)
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

| 대상 | 스케줄 | 방식 | 저장 위치 |
|------|--------|------|-----------|
| MySQL | 매일 02:00 | `mysqldump` 전체 덤프 | MinIO `wafer-backup` 버킷 |
| MinIO | 매일 03:00 | `mc mirror` 증분 백업 | MinIO `wafer-backup` 버킷 |

- 커스텀 이미지: `debian:bookworm-slim` + `mysql-client` + `mc` (ARM64)
- `successfulJobsHistoryLimit: 3`, `failedJobsHistoryLimit: 1`

---

## ▍ArgoCD syncPolicy

```yaml
# ingress, monitoring, loki, backup, network-policy
syncPolicy:
  automated:
    prune: true       # 불필요한 리소스 자동 삭제
    selfHeal: true    # 수동 변경 감지 시 자동 복구
  syncOptions:
    - CreateNamespace=true
    - ServerSideApply=true  # monitoring: CRD 크기 문제 대응
```

인프라 리소스는 Git 상태를 더 엄격하게 강제합니다.

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
