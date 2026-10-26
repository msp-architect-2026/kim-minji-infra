# 클러스터 작업 RUNBOOK

코드(kim-minji-helm, kim-minji-infra)가 모두 머지된 뒤 클러스터에서 직접 수행할 작업을 순서대로 정리한다.
레포의 코드는 클러스터에 접속하지 않고 작성·검증했다(`scripts/validate.sh`: helm lint / helm template / kubeconform / yamllint, dev·full).

> 이 문서는 항목별 PR이 머지될 때마다 갱신한다. `작성 예정 (항목 N)`은 해당 항목 PR에서 채운다.

## 0. 전제

| 항목 | 내용 |
|---|---|
| 노드 | 맥북(RAM 32GB) UTM VM 3대. **k3s server 1대(k3s-cp1) + agent 2대(k3s-w1, k3s-ai2)** |
| 최소 기동 | dev 프로파일은 **k3s-cp1 1대만 켜도** 동작해야 한다 (예외는 4장 체크리스트에 명시) |
| ai-serving | full: k3s-ai2 고정(nodeSelector). dev: k3s-ai2 선호(preferred affinity), ai2가 꺼져 있으면 다른 노드에 뜬다 |
| GitLab CE | 클러스터 밖(맥 호스트 컨테이너 또는 별도 VM)으로 이전 |
| 프로파일 | `dev`(기본) / `full`. ArgoCD ApplicationSet이 in-cluster 클러스터 Secret의 `env` 라벨로 values 파일을 고른다 |
| 컨트롤 플레인 HA | 범위 밖 (server 1대). cp1이 꺼지면 API·스케줄링이 멈춘다 |

## 1. 확정 필요 항목

코드에는 placeholder로 들어가 있다. 클러스터에서 값을 확인한 뒤 해당 파일을 고쳐 커밋한다.

| # | 항목 | 현재 값 (placeholder) | 위치 | 확인 방법 |
|---|---|---|---|---|
| C1 | k3s(Kubernetes) 버전 | `1.31.0` | `scripts/validate.sh` `K8S_VERSION` (helm, infra) | `kubectl version` |
| C2 | ai-serving dev 메모리로 모델 로딩 가능 여부 | requests 250m/512Mi, limits 1000m/1Gi, HPA 1~2 | `kim-minji-helm/ai-serving/values-dev.yaml` | dev 배포 후 OOMKilled·readiness 실패 여부, `kubectl top pod -n ai-serving`. 부족하면 limit을 올리고 8장 표 갱신 |
| C3 | GitLab 이전 후 호스트 IP | repoURL은 `http://gitlab.local:8929/...` 유지, IP만 변경 | CoreDNS custom 설정(`gitlab.local` hosts, 현재 `192.168.0.157`) — 레포 밖 ConfigMap | 이전한 GitLab의 IP. 맥 호스트 컨테이너면 UTM 브리지에서 보이는 맥 IP |
| C5 | 기존 ArgoCD Application 이름 | ApplicationSet이 차트명(`frontend`, `backend`, `ai-serving`, `mysql`, `minio`, `ingress`, `network-policy`, `coredns`, `node-local-dns`, `monitoring`, `loki`)으로 생성 | `argocd/apps/*.yaml` | `kubectl -n gitops get applications`. 이름이 다르면 4.1 절차에서 non-cascade 삭제 |
| C6 | ArgoCD 버전 | `templatePatch`(v2.10+), `ignoreMissingValueFiles` 사용 | `argocd/apps/platform.yaml` | `argocd version` 또는 argocd-server 이미지 태그 |
| C7 | VM 메모리 배분 | 8장 가정: cp1 8GB / w1 7GB / ai2 7GB | UTM 설정 | 8장 표와 `kubectl describe node` Allocatable |
| C8 | 레포 밖 구성요소 실제 사용량 | 8장 추정치 | — | `kubectl top pod -A` (k3s 내장 coredns·metrics-server·local-path, ArgoCD, sealed-secrets, ingress-nginx) |
| C4 | Sealed Secrets controller 이름·네임스페이스 | `kube-system/sealed-secrets-controller` (가정) | `kim-minji-helm/README.md` kubeseal 명령 | `kubectl get deploy -A \| grep sealed` |

## 2. 사전 백업

작성 예정 (항목 4). 최소한 아래는 전환 작업 전에 수행한다.

- MySQL: `mysqldump` 전체 덤프를 클러스터 밖(맥 호스트)으로 복사
- MinIO: `mc mirror`로 `wafer-images`, `wafer-model` 버킷을 클러스터 밖으로 복사
- 현재 Secret 값 확인 (SealedSecret 재암호화에 필요): `mysql-secret`, `minio-secret`, `backend-secret`
- k3s 상태: cp1의 `/var/lib/rancher/k3s/server` (현재 단일 server라 datastore가 SQLite일 가능성이 높다 — `ls /var/lib/rancher/k3s/server/db`로 확인)

## 3. k3s 구성: 기존 클러스터 유지 vs 새로 설치

현재도 cp1(server) + w1·ai2(agent) 구성이므로, 노드 역할 자체는 바뀌지 않는다.

| 선택지 | 방법 | 장점 | 단점 |
|---|---|---|---|
| A. 기존 클러스터 유지 (권장) | 플랫폼 구성요소를 ArgoCD로 추가하고, 스토리지만 local-path → Longhorn으로 데이터 이전 | 다운타임 최소, 기존 SealedSecret 키 유지 | 설정 잔재(수동 설치한 ingress 등) 정리 필요 |
| B. 새로 설치 | 3대 모두 k3s 재설치 → 처음부터 GitOps로 구성 | 깨끗한 상태, 설치 순서 검증 가능 | Sealed Secrets 키가 바뀌어 **모든 SealedSecret 재암호화** 필요(기존 키를 백업해 복원하면 회피 가능), 데이터 복원 필요 |

세부 절차: 작성 예정 (항목 2, 10).

## 4. 설치·배포 순서

작성 예정 (항목 2, 7). 큰 순서:

1. k3s 설정 (내장 traefik/servicelb 사용 여부, ai2 taint — 4.2)
2. Sealed Secrets controller → MetalLB → cert-manager → Longhorn → ingress-nginx
3. ArgoCD + ApplicationSet (클러스터 Secret에 `env` 라벨, 기본 dev)
4. 인프라 앱 (DNS → NetworkPolicy → monitoring/loki → backup)
5. 애플리케이션 (mysql/minio → backend → ai-serving → frontend)

### 4.1 ApplicationSet 전환 (항목 1)

기존 개별 Application(coredns, loki, monitoring, node-local-dns 및 클러스터에만 있는 앱 Application)을 ApplicationSet이 만드는 Application으로 바꾼다. 리소스는 지우지 않는다.

1. 기존 Application 목록 확인 (C5): `kubectl -n gitops get applications`
2. 기존 Application을 **리소스는 남기고** 삭제 (finalizer 제거 후 삭제 = non-cascade)
   ```bash
   for app in <기존 앱 이름들>; do
     kubectl -n gitops patch application $app --type merge -p '{"metadata":{"finalizers":null}}'
     kubectl -n gitops delete application $app
   done
   ```
3. 클러스터 Secret(프로파일 라벨) 적용: `kubectl apply -f argocd/bootstrap/cluster-in-cluster.yaml` (기본 `env: dev`)
4. 루트 앱 적용: `kubectl apply -f argocd/bootstrap/root.yaml` → `root` → `platform`, `apps` ApplicationSet → 차트별 Application 생성
5. 확인: `kubectl -n gitops get applicationsets`, 모든 Application `Synced/Healthy`
6. `backup/argocd-application.yaml`(backup 앱)은 프로파일과 무관해 그대로 둔다

### 4.2 k3s-ai2 taint

ai-serving은 `dedicated=ai-serving:PreferNoSchedule`을 tolerate한다. 다른 워크로드가 ai2를 되도록 피하게 한다 (soft라 dev에서 ai2만 켜져 있어도 배치는 된다).

```bash
kubectl taint nodes k3s-ai2 dedicated=ai-serving:PreferNoSchedule
```

### 4.3 프로파일 전환 (dev ↔ full)

```bash
# full로: 3노드를 모두 켠 뒤
kubectl -n gitops label secret in-cluster env=full --overwrite
# dev로: 노드를 끄기 전에 먼저
kubectl -n gitops label secret in-cluster env=dev --overwrite
```

- 라벨을 바꾸면 ApplicationSet이 모든 Application의 valueFiles를 `values-<env>.yaml`로 바꾸고 auto-sync한다.
- **full → dev 순서 주의**: 노드를 먼저 끄면 full의 DoNotSchedule·replica 2 Pod가 Pending/축출된다. 라벨을 먼저 바꾸고 sync 완료 후 노드를 끈다.
- Git의 `argocd/bootstrap/cluster-in-cluster.yaml`은 초기값(dev)이다. 라벨은 클러스터에서만 바꾼다.

## 5. 채워야 할 시크릿

모든 값은 `kubeseal`로 암호화해 해당 values 파일의 placeholder 자리에 커밋한다. 평문 커밋 금지.

| 시크릿 | 네임스페이스 | 위치 | 상태 |
|---|---|---|---|
| `mysql-secret` (4개 키) | storage | `kim-minji-helm/mysql/values.yaml` `sealedSecret.encryptedData` | helm#3 |
| 그 밖의 항목 | | | 작성 예정 (항목 3, 4, 6) |

## 6. 배포 후 확인 체크리스트

작성 예정 (항목 2~9에서 계속 추가). 노드 1대(cp1만) / 3대 켠 상태로 나눠 확인한다.

### 6.1 dev, cp1 1대만 켠 상태

- [ ] `kubectl get pods -A --field-selector=status.phase=Pending` 결과 없음. **예외(현재 알려진 것)**:
  - mysql, minio: local-path PV가 k3s-w1에 있으면 Pending (Longhorn 전환 전까지 — 항목 2/4)
- [ ] coredns replica 1 Running
- [ ] ai-serving이 cp1에 Running (ai2 선호지만 ai2가 꺼져 있으므로), OOMKilled 없음 (C2)
- [ ] Prometheus retention 2d: `kubectl -n monitoring get prometheus -o jsonpath='{.items[0].spec.retention}'`

### 6.2 full, 3대 모두 켠 상태

- [ ] `env=full` 라벨 적용 후 모든 Application Synced/Healthy
- [ ] coredns 2개가 서로 다른 노드: `kubectl -n kube-system get pod -l k8s-app=kube-dns -o wide`
- [ ] frontend, backend 각 2개 Running
- [ ] ai-serving이 k3s-ai2에만 배치: `kubectl -n ai-serving get pod -o wide`

## 7. 롤백

- ApplicationSet 전환 롤백: `root` Application 삭제(ApplicationSet은 `preserveResourcesOnDeletion: true`라 리소스 유지) → 기존 Application 매니페스트(Git 이력의 `*/application.yaml`) 재적용

작성 예정 (항목 1~9). 원칙:

- 코드 롤백은 Git revert → ArgoCD sync
- 프로파일 전환 롤백은 클러스터 Secret의 `env` 라벨을 이전 값으로 되돌린다
- 데이터 롤백은 2장의 사전 백업에서 복원한다

## 8. 프로파일별 resource requests 합계

`scripts/resource-summary.py`로 렌더링 결과(helm + infra)에서 계산했다. DaemonSet은 dev 1노드, full 3노드 기준. HPA는 minReplicas로 세고 최대치는 별도 행.

```bash
OUT=/tmp/r-infra scripts/validate.sh            # kim-minji-infra
OUT=/tmp/r-helm  ../kim-minji-helm/scripts/validate.sh
scripts/resource-summary.py --nodes 1 /tmp/r-infra/dev /tmp/r-helm/dev
scripts/resource-summary.py --nodes 3 /tmp/r-infra/full /tmp/r-helm/full
```

### 8.1 요약 (항목 1 기준, 2026-10-26)

| 구분 | dev (cp1 1대) CPU | dev 메모리 | full (3대) CPU | full 메모리 |
|---|---|---|---|---|
| 레포 워크로드 requests | 1415m | 2.66Gi | 2530m | 4.66Gi |
| 레포 워크로드 (HPA 최대치) | 1665m | 3.16Gi | 4030m | 7.66Gi |
| 레포 밖 구성요소 (추정, C8) | ~0.3 core | ~1.1Gi | ~0.4 core | ~1.2Gi |
| k3s 프로세스·OS (추정) | — | ~1.0Gi | — | ~2.2Gi (server 1.0 + agent 0.6×2) |
| **합계 (HPA 최소)** | **~1.7 core** | **~4.8Gi** | **~2.9 core** | **~8.1Gi** |
| **합계 (HPA 최대)** | **~2.0 core** | **~5.3Gi** | **~4.4 core** | **~11.1Gi** |

레포 밖 구성요소 추정(실제 사용량 기준, requests 아님): ArgoCD ~800Mi, ingress-nginx ~100Mi(100m), sealed-secrets ~50Mi, k3s 내장 coredns 70Mi(100m)·metrics-server 70Mi(100m)·local-path 20Mi. full은 coredns 2개분.

### 8.2 32GB 안에서의 배분 (가정, C7)

| 항목 | 메모리 |
|---|---|
| 맥 호스트 OS·앱 | ~6GB |
| GitLab CE (클러스터 밖) | ~4GB (권장 최소) |
| **VM 3대에 쓸 수 있는 양** | **~22GB** |
| 제안 배분 | cp1 8GB / w1 7GB / ai2 7GB |

- dev: 모든 Pod가 cp1에 몰린다. ~5.3Gi(HPA 최대) < cp1 8GB → 여유 ~2.7GB.
- full: 합계 ~8.1Gi(HPA 최소) / ~11.1Gi(최대) < 22GB.
- **주의 (full)**: ai-serving은 ai2 고정이라 HPA 4개면 ai2에 requests 4Gi, **limits 12Gi(3Gi×4)** 가 몰린다. ai2 7GB에서는 limits 기준 과다 할당이다. 항목 8(AI 서빙)에서 maxReplicas 또는 limit 조정을 검토한다.

### 8.3 워크로드별 상세

dev (`--nodes 1`):

| 릴리스 | 종류 | 이름 | Pod 수 (최대) | requests/Pod (CPU · 메모리) | 합계 CPU | 합계 메모리 |
|---|---|---|---|---|---|---|
| ai-serving | Deployment | ai-serving-ai-serving | 1 (2) | 250m · 512Mi | 250m | 512Mi |
| backend | Deployment | backend-backend | 1 | 250m · 512Mi | 250m | 512Mi |
| coredns | Deployment | coredns | 1 | 0m · 0Mi (k3s 관리, 실제 100m · 70Mi) | 0m | 0Mi |
| frontend | Deployment | frontend-frontend | 1 | 100m · 128Mi | 100m | 128Mi |
| loki | DaemonSet | loki-promtail | 1 | 25m · 64Mi | 25m | 64Mi |
| loki | StatefulSet | loki | 1 | 50m · 128Mi | 50m | 128Mi |
| minio | StatefulSet | minio-minio | 1 | 200m · 256Mi | 200m | 256Mi |
| monitoring | DaemonSet | monitoring-prometheus-node-exporter | 1 | 20m · 32Mi | 20m | 32Mi |
| monitoring | Deployment | monitoring-grafana | 1 | 50m · 128Mi (sidecar 미설정) | 50m | 128Mi |
| monitoring | Deployment | monitoring-kube-prometheus-operator | 1 | 50m · 64Mi | 50m | 64Mi |
| monitoring | Deployment | monitoring-kube-state-metrics | 1 | 20m · 64Mi | 20m | 64Mi |
| monitoring | Prometheus | monitoring-kube-prometheus-prometheus | 1 | 100m · 256Mi | 100m | 256Mi |
| mysql | StatefulSet | mysql-mysql | 1 | 200m · 512Mi | 200m | 512Mi |
| node-local-dns | DaemonSet | node-local-dns | 1 | 100m · 64Mi | 100m | 64Mi |
| **합계** | | | | | **1415m** | **2.66Gi** |
| **HPA 최대치 기준** | | | | | **1665m** | **3.16Gi** |

full (`--nodes 3`):

| 릴리스 | 종류 | 이름 | Pod 수 (최대) | requests/Pod (CPU · 메모리) | 합계 CPU | 합계 메모리 |
|---|---|---|---|---|---|---|
| ai-serving | Deployment | ai-serving-ai-serving | 1 (4) | 500m · 1024Mi | 500m | 1024Mi |
| backend | Deployment | backend-backend | 2 | 250m · 512Mi | 500m | 1024Mi |
| coredns | Deployment | coredns | 2 | 0m · 0Mi (k3s 관리, 실제 100m · 70Mi) | 0m | 0Mi |
| frontend | Deployment | frontend-frontend | 2 | 100m · 128Mi | 200m | 256Mi |
| loki | DaemonSet | loki-promtail | 3 | 50m · 128Mi | 150m | 384Mi |
| loki | StatefulSet | loki | 1 | 100m · 256Mi | 100m | 256Mi |
| minio | StatefulSet | minio-minio | 1 | 200m · 256Mi | 200m | 256Mi |
| monitoring | DaemonSet | monitoring-prometheus-node-exporter | 3 | 20m · 32Mi | 60m | 96Mi |
| monitoring | Deployment | monitoring-grafana | 1 | 50m · 128Mi (sidecar 미설정) | 50m | 128Mi |
| monitoring | Deployment | monitoring-kube-prometheus-operator | 1 | 50m · 64Mi | 50m | 64Mi |
| monitoring | Deployment | monitoring-kube-state-metrics | 1 | 20m · 64Mi | 20m | 64Mi |
| monitoring | Prometheus | monitoring-kube-prometheus-prometheus | 1 | 200m · 512Mi | 200m | 512Mi |
| mysql | StatefulSet | mysql-mysql | 1 | 200m · 512Mi | 200m | 512Mi |
| node-local-dns | DaemonSet | node-local-dns | 3 | 100m · 64Mi | 300m | 192Mi |
| **합계** | | | | | **2530m** | **4.66Gi** |
| **HPA 최대치 기준** | | | | | **4030m** | **7.66Gi** |

> 이후 항목(Longhorn, MetalLB, cert-manager, Alertmanager, KEDA, Tempo/OTel 등)이 추가될 때마다 이 표를 갱신한다.
