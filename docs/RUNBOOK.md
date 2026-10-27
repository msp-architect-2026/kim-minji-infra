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
| C1 | k3s(Kubernetes) 버전 | `1.31.0` | `scripts/validate.sh` `K8S_VERSION` (helm, infra) | `kubectl version`. **Traefik 차트 42.x는 1.30 이상 필요**, Longhorn 1.12는 1.25 이상 |
| C2 | ai-serving dev 메모리로 모델 로딩 가능 여부 | requests 250m/512Mi, limits 1000m/1Gi, HPA 1~2 | `kim-minji-helm/ai-serving/values-dev.yaml` | dev 배포 후 OOMKilled·readiness 실패 여부, `kubectl top pod -n ai-serving`. 부족하면 limit을 올리고 8장 표 갱신 |
| C3 | GitLab 이전 후 호스트 IP | repoURL은 `http://gitlab.local:8929/...` 유지, IP만 변경 | CoreDNS custom 설정(`gitlab.local` hosts, 현재 `192.168.0.157`) — 레포 밖 ConfigMap | 이전한 GitLab의 IP. 맥 호스트 컨테이너면 UTM 브리지에서 보이는 맥 IP |
| C5 | 기존 ArgoCD Application 이름 (**ai-serving 주의**: backend values의 `AI_SERVER_URL`이 `kim-minji-ai-serving-ai-serving...`이라 현재 릴리스명이 `kim-minji-ai-serving`일 가능성이 높다. ApplicationSet은 `ai-serving` 릴리스로 Service `ai-serving-ai-serving`을 만들므로, 이름이 다르면 `AI_SERVER_URL`을 맞추거나 `releaseName`을 지정해야 backend→AI 호출이 된다. `kubectl -n ai-serving get svc`로 확인) | ApplicationSet이 차트명(`frontend`, `backend`, `ai-serving`, `mysql`, `minio`, `ingress`, `network-policy`, `coredns`, `node-local-dns`, `monitoring`, `loki`)으로 생성 | `argocd/apps/*.yaml` | `kubectl -n gitops get applications`. 이름이 다르면 4.1 절차에서 non-cascade 삭제. **앱 차트는 이름이 곧 Helm 릴리스명**이라, 다르면 리소스 이름·StatefulSet PVC가 새로 생겨 빈 DB/버킷으로 뜬다 → 해당 원소에 기존 릴리스명을 `releaseName`으로 지정하거나 2장 백업으로 데이터 이전 |
| C6 | ArgoCD 버전 | `templatePatch`(v2.10+), `ignoreMissingValueFiles` 사용 | `argocd/apps/platform.yaml` | `argocd version` 또는 argocd-server 이미지 태그 |
| C7 | VM 메모리 배분 | 8장 가정: cp1 8GB / w1 7GB / ai2 7GB | UTM 설정 | 8장 표와 `kubectl describe node` Allocatable |
| C4 | 기존 Sealed Secrets controller 설치 방식·이름·네임스페이스 | `kube-system/sealed-secrets-controller` (가정) | `sealed-secrets/values.yaml` `fullnameOverride`, 릴리스 네임스페이스(`argocd/apps/platform.yaml`) | `kubectl get deploy -A \| grep sealed`, `helm list -A`. 다르면 값을 맞춘다 (키 Secret이 같은 네임스페이스에 있어야 재사용됨). 기존 Deployment selector가 `app.kubernetes.io/name: sealed-secrets, app.kubernetes.io/instance: sealed-secrets`가 아니면(예: controller.yaml로 설치 → `name: sealed-secrets-controller`) selector 불변 오류가 나므로 4.1 전에 `kubectl -n kube-system delete deploy sealed-secrets-controller` (키 Secret은 남는다). 기존 Helm 릴리스가 있으면 리소스를 지우지 않도록 릴리스 메타데이터 Secret(`sh.helm.release.v1.<릴리스>.*`)만 삭제하고 `helm uninstall`은 하지 않는다 |
| C8 | 레포 밖 구성요소 실제 사용량 | 8장 추정치 | — | `kubectl top pod -A` (ArgoCD, k3s 내장 coredns·metrics-server·local-path, Longhorn instance-manager·CSI) |
| C9 | **(4.1 전 필수)** MetalLB IP 대역 / Traefik LB IP | `PLACEHOLDER-IP-RANGE` | `metallb/values.yaml` `ipAddressPool.addresses` | 노드 IP(.57/.157/.240)·공유기 DHCP 범위와 겹치지 않는 대역(예: `192.168.0.200-192.168.0.209`). 할당된 Traefik IP는 `kubectl -n traefik get svc traefik`. UTM 브리지가 맥 Wi-Fi면 L2 광고(ARP)가 맥 외부로 안 나갈 수 있다 → 맥에서 `curl http://<LB IP>`로 확인, 안 되면 NodePort 32088 경로를 계속 쓴다 |
| C10 | 기존 ingress-nginx 설치 방식·네임스페이스 | NetworkPolicy가 참조하던 `ingress-nginx` 네임스페이스 | — | `helm list -A \| grep ingress`, `kubectl get ns`. 4.4 전환 시 삭제 대상 |
| C11 | k3s 내장 traefik·servicelb 사용 여부 | 둘 다 꺼야 함 | cp1 `/etc/rancher/k3s/config.yaml` | `kubectl -n kube-system get helmchart`(`traefik`, `traefik-crd` 없어야 함), `kubectl -n kube-system get ds \| grep svclb` |
| C13 | 외부 접속 방식 (HTTPS) | LB IP 443 기준: Traefik 리다이렉트 `to: websecure`(→:443), frontend `https://api.wafer.local`, `https://grafana.wafer.local` | `traefik/values.yaml` `ports.web.http.redirections.entryPoint.to`, GitLab frontend CI 변수 `VITE_API_URL`·`VITE_GRAFANA_URL`(5.1), `kim-minji-frontend/.env` | C9의 MetalLB IP가 맥에서 접속되면 그대로. **NodePort로만 접속 가능하면** 4.7의 NodePort 리다이렉트 설정으로 바꾸고, frontend URL·Grafana `root_url`을 `https://*.wafer.local:32443`으로 바꾼다 (backend CORS는 두 경우 모두 허용) |
| C14 | 앱 네임스페이스의 다른 Pod·외부 통신 | default-deny 대상: application, ai-serving, storage | `network-policy/templates/egress.yaml` | `kubectl get pod -n application -n ai-serving -n storage`로 frontend·backend·ai-serving·mysql·minio·backup Job 외의 Pod가 있는지, backend·ai-serving이 클러스터 밖(외부 API 등)을 호출하는지 확인. 있으면 egress 규칙 추가 |
| C15 | GitLab Runner 인터넷 접근 (Trivy) | `aquasec/trivy:0.75.0` 이미지 pull, 취약점 DB(ghcr.io) 다운로드 | 앱 레포 `.gitlab-ci.yml` `image_scan` | Runner에서 `ghcr.io`, Docker Hub 접근 가능 여부. 막혀 있으면 job이 실패(allow_failure라 배포는 진행) |
| C16 | 외부 S3 호환 백업 저장소 | `external.enabled: false`(클러스터 내 백업만), `https://PLACEHOLDER-S3-ENDPOINT`, `PLACEHOLDER-BUCKET`, Longhorn `backupTarget: ~` | `backup/values.yaml` `external.*`(확정 후 `enabled: true`), `longhorn/values.yaml` `longhorn.defaultBackupStore.backupTarget`·`recurringJobs.backup.enabled`, `network-policy/values.yaml` `backupExternalEgress.ports` | AWS S3·B2·R2·NAS·맥 호스트 MinIO 중 결정. **같은 맥의 디스크면 맥 고장 시 함께 사라진다**는 점을 고려. 엔드포인트 포트가 443·9000이 아니면 egress 포트 추가. Longhorn 형식: `s3://<bucket>@<region>/longhorn` |
| C17 | k3s 데이터스토어 종류 | SQLite 가정 (server 1대) | RUNBOOK 4.10 | `ls /var/lib/rancher/k3s/server/db` — `state.db`면 SQLite, `etcd/`면 embedded etcd(`k3s etcd-snapshot` 사용) |
| C18 | ArgoCD Notifications 컨트롤러·기존 설정 | 설치 시 함께 깔렸다고 가정, `argocd-notifications-cm`/`-secret`을 이어받음 | `argocd-notifications/` 차트 | `kubectl -n gitops get deploy argocd-notifications-controller`. 없으면 ArgoCD 설치 매니페스트(설치 버전과 같은 것)로 컨트롤러만 추가. 기존 cm에 직접 넣은 설정이 있으면 차트 values로 옮긴다 |
| C19 | 백업 이미지 태그 | `mysql-backup:latest` + `imagePullPolicy: Always` (infra CI는 이제 커밋 SHA 태그로만 빌드) | `backup/values.yaml` `image`, `imagePullPolicy` | `backup/Dockerfile`을 바꿔 infra 파이프라인이 `mysql-backup:<sha>`를 push하면 그 SHA로 바꾸고 `IfNotPresent`로 커밋. 그전까지는 기존 `latest` 이미지를 쓴다 |
| C20 | GitLab `HELM_REPO_TOKEN` 권한 | ai·backend·frontend CI 변수 | 각 레포 `update-helm.sh` | kim-minji-helm에 push + MR 생성이 가능해야 한다: Personal/Project Access Token 범위 `api`, `write_repository` (기존 토큰이 `write_repository`만이면 MR 생성 단계에서 401/403) |
| C12 | VM vCPU 수 | dev는 cp1 1대에 CPU requests ~2.0~2.3 core + instance-manager 예약(5%)이 몰린다 → **cp1 3 vCPU 이상** 권장. full instance-manager는 노드마다 8% | UTM 설정 | `kubectl describe node` Capacity·Allocated resources |

## 2. 사전 백업

전환 작업(특히 4.8 스토리지 이전) 전에 클러스터 **밖**(맥 호스트)으로 받아 둔다. 아래 명령은 kubectl이 되는 곳(cp1 또는 맥)에서 실행한다.

```bash
mkdir -p ~/wafer-backup && cd ~/wafer-backup
# MySQL 전체 덤프
kubectl -n storage exec mysql-mysql-0 -- sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --set-gtid-purged=OFF --databases "$MYSQL_DATABASE"' | gzip > mysql-$(date +%F).sql.gz
# MinIO 버킷 (포트포워딩 + mc)
kubectl -n storage port-forward svc/minio-minio 9000:9000 &
mc alias set wafer http://127.0.0.1:9000 "$(kubectl -n storage get secret minio-secret -o jsonpath='{.data.accessKey}' | base64 -d)" "$(kubectl -n storage get secret minio-secret -o jsonpath='{.data.secretKey}' | base64 -d)"
mc mirror wafer/wafer-images ./minio/wafer-images && mc mirror wafer/wafer-model ./minio/wafer-model
kill %1
```

- 덤프 확인: `gunzip -c mysql-*.sql.gz | grep -c 'INSERT INTO'`
- 현재 Secret 값 확인 (SealedSecret 재암호화에 필요): `mysql-secret`, `minio-secret`, `backend-secret`
- k3s 데이터스토어: 4.10 절차로 한 번 받아 둔다 (C17)
- **Sealed Secrets 암호화 키** (잃으면 모든 SealedSecret을 다시 암호화해야 한다):
  ```bash
  kubectl -n kube-system get secret -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > sealed-secrets-key-backup.yaml
  ```
  이 파일은 평문 개인키다. Git에 올리지 말고 맥 호스트의 안전한 위치에 보관한다.

## 3. k3s 구성: 기존 클러스터 유지 vs 새로 설치

현재도 cp1(server) + w1·ai2(agent) 구성이므로, 노드 역할 자체는 바뀌지 않는다.

| 선택지 | 방법 | 장점 | 단점 |
|---|---|---|---|
| A. 기존 클러스터 유지 (권장) | 플랫폼 구성요소를 ArgoCD로 추가하고, 스토리지만 local-path → Longhorn으로 데이터 이전 | 다운타임 최소, 기존 SealedSecret 키 유지 | 설정 잔재(수동 설치한 ingress 등) 정리 필요 |
| B. 새로 설치 | 3대 모두 k3s 재설치 → 처음부터 GitOps로 구성 | 깨끗한 상태, 설치 순서 검증 가능 | Sealed Secrets 키가 바뀌어 **모든 SealedSecret 재암호화** 필요(기존 키를 백업해 복원하면 회피 가능), 데이터 복원 필요 |

A안 절차: 4.5 노드 준비 → C9 확정·커밋 → 4.4 ingress-nginx 제거 → 4.1 ApplicationSet 전환(Traefik 포함) → (항목 4) 스토리지 이전. B안 세부 절차는 항목 10에서 정리한다.

## 4. 설치·배포 순서

큰 순서 (sync wave로 자동화하는 부분은 항목 7에서 보완):

1. 노드 준비·k3s 설정 (4.5) — 내장 traefik·servicelb 비활성화, Longhorn 선행 패키지, 디스크 노드 라벨, ai2 taint(4.2)
2. **C9(MetalLB IP 대역)를 확정해 커밋** — placeholder 상태로 4.1을 하면 metallb sync가 webhook에서 거부되고, Traefik LoadBalancer에 IP가 없어 traefik·ingress·monitoring 앱이 계속 Progressing으로 남는다
3. ingress-nginx 제거 (4.4) — **여기서부터 외부 접속 중단**
4. ArgoCD ApplicationSet 전환 (4.1) — 플랫폼(sealed-secrets, metallb, cert-manager, longhorn, traefik)과 기존 인프라·앱이 한 번에 생성된다. Traefik이 Ready가 되면 **접속 복구**
5. 스토리지 이전 (4.8) — mysql·minio를 Longhorn으로. helm 레포의 `storageClassName: longhorn` 변경이 머지된 상태에서 mysql·minio Application은 StatefulSet 불변 필드 때문에 sync가 실패한 채 기다린다(기존 Pod는 계속 동작) → 4.8 절차로 옮긴다
6. 데이터스토어 백업 등록 (4.10), 외부 백업(C16 확정 후 `external.enabled: true`) 확인 (6.1)

**앱 사이 순서**: ApplicationSet은 Application을 한꺼번에 만들므로 앱 사이 순서는 강제하지 않는다. 대신 모든 Application에 sync `retry`(최대 5회, 최대 3분 간격)가 있어 앞 단계가 준비되면 뒤 단계가 따라 성공한다. 앱 안의 순서(CRD → CR)는 sync-wave와 `SkipDryRunOnMissingResource`로 잡혀 있다.
기대 수렴 순서와 확인 방법:

| 순서 | 앱 | 준비 확인 | 앞 단계가 안 됐을 때 증상 |
|---|---|---|---|
| 1 | sealed-secrets | `kubectl -n kube-system get deploy sealed-secrets-controller` | 다른 앱의 SealedSecret이 Secret으로 안 풀림 → Pod `CreateContainerConfigError` |
| 2 | metallb, cert-manager, longhorn | 각 네임스페이스 Pod Ready, `kubectl get clusterissuer wafer-ca` | IPAddressPool·ClusterIssuer·RecurringJob이 webhook/CRD 없음으로 실패 → retry |
| 3 | traefik | `kubectl -n traefik get svc traefik` EXTERNAL-IP | Ingress가 Progressing |
| 4 | coredns, node-local-dns, network-policy | DNS 조회 (`kubectl run -it --rm dns --image=busybox -- nslookup kubernetes.default`) | — |
| 5 | monitoring, loki, argocd-notifications | Prometheus·Alertmanager Ready | — |
| 6 | mysql, minio, backup | StatefulSet Ready, PVC Bound(longhorn) | backend가 DB 연결 실패로 재시작 반복 (정상화 후 자동 복구) |
| 7 | backend, ai-serving, frontend | Pod Ready, 6.1 체크리스트 | — |

5회 retry 후에도 실패로 남은 앱은 앞 단계를 확인한 뒤 `argocd app sync <앱>`(또는 UI의 Sync)으로 다시 돌린다.

### 4.1 ApplicationSet 전환 (항목 1)

기존 개별 Application(coredns, loki, monitoring, node-local-dns 및 클러스터에만 있는 앱 Application)을 ApplicationSet이 만드는 Application으로 바꾼다. 리소스는 지우지 않는다.

**선행 조건**: 4.5 완료, C9 커밋 완료, 4.4(ingress-nginx 제거) 완료. ApplicationSet이 traefik·ingress·network-policy를 동시에 만들기 때문에, ingress-nginx가 남아 있으면 NodePort 32088 충돌로 traefik sync가 실패하고 Ingress·NetworkPolicy만 traefik 기준으로 바뀌어 접속이 끊긴다.

1. 기존 Application 목록 확인 (C5): `kubectl -n gitops get applications`
2. 기존 Application을 **리소스는 남기고** 삭제 (finalizer 제거 후 삭제 = non-cascade). 대상에 기존 `backup` Application도 포함한다 (platform ApplicationSet의 `backup` 차트가 같은 이름의 CronJob을 이어받는다)
   ```bash
   for app in <기존 앱 이름들>; do
     kubectl -n gitops patch application $app --type merge -p '{"metadata":{"finalizers":null}}'
     kubectl -n gitops delete application $app
   done
   ```
3. 클러스터 Secret 적용: `kubectl apply -f argocd/bootstrap/cluster-in-cluster.yaml` (`env` 라벨 없음 = dev. 다시 apply해도 클러스터에서 붙인 라벨은 유지된다)
4. 루트 앱 적용: `kubectl apply -f argocd/bootstrap/root.yaml` → `root` → `platform`, `apps` ApplicationSet → 차트별 Application 생성
5. 확인: `kubectl -n gitops get applicationsets`, 모든 Application `Synced/Healthy`. 첫 설치 때 webhook CA 주입 지연으로 cert-manager·metallb가 한 번 실패할 수 있으나 sync `retry`(최대 5회)로 다시 시도한다. 계속 실패하면 `argocd app sync <앱>`
6. 접속 확인 (4.4의 5단계)
7. (항목 4부터) `backup`은 platform ApplicationSet의 Helm 차트로 관리한다. `backup/argocd-application.yaml`은 삭제됐다

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
# sync 완료 후, 끌 노드를 drain한 다음 전원을 끈다
kubectl drain k3s-w1 --ignore-daemonsets --delete-emptydir-data
kubectl drain k3s-ai2 --ignore-daemonsets --delete-emptydir-data
# 다시 켠 뒤
kubectl uncordon k3s-w1 k3s-ai2
```

- 라벨을 바꾸면 ApplicationSet이 모든 Application의 valueFiles를 `values-<env>.yaml`로 바꾸고 auto-sync한다.
- **full → dev 순서 주의**: 노드를 먼저 끄면 full의 DoNotSchedule·replica 2 Pod가 Pending/축출된다. 라벨을 먼저 바꾸고 sync 완료 후 노드를 끈다.
- **drain 없이 끄지 않는다**: 꺼진 노드의 Deployment Pod는 약 5분 뒤에야 재생성되고, StatefulSet Pod(loki, prometheus, 레포 밖의 argocd-application-controller 등)는 Terminating에 걸려 재생성되지 않는다. ArgoCD 컨트롤러가 걸리면 아무것도 동기화되지 않는다.
- Git의 `argocd/bootstrap/cluster-in-cluster.yaml`에는 `env` 라벨이 없다(= dev). 라벨은 클러스터에서만 바꾼다.
- **Longhorn 볼륨 복제 수**: StorageClass 복제 수(dev 1 / full 2)는 새 볼륨에만 적용된다. 기존 볼륨은 직접 바꾼다.
  ```bash
  # dev → full: 3노드를 켜고 env=full sync 후 복제 수 2로
  for v in $(kubectl -n longhorn-system get volumes.longhorn.io -o name); do
    kubectl -n longhorn-system patch $v --type merge -p '{"spec":{"numberOfReplicas":2}}'
  done
  ```
  - **full → dev 순서** (순서를 바꾸면 cp1 복제본이 지워지고 w1 복제본만 남을 수 있다):
    1. `env=dev` 라벨 → sync
    2. `kubectl drain k3s-w1 ...` → mysql·minio Pod가 cp1에서 Running이 될 때까지 대기
    3. 각 볼륨의 cp1 복제본이 healthy인지 확인: `kubectl -n longhorn-system get replicas.longhorn.io -o wide` (NODE=k3s-cp1, 상태 running)
    4. 그다음 복제 수를 1로 낮춘다 (위 명령에서 2 → 1). 낮추지 않아도 된다: 복제 수 2인 볼륨은 cp1만 켜진 상태에서 Degraded로 표시되지만 정상 사용 가능하다
    5. w1·ai2 전원 끄기

### 4.4 ingress-nginx → Traefik 전환 (항목 2)

ingress-nginx는 2026년 3월 유지보수가 끝나 Traefik으로 교체했다. NodePort 32088을 Traefik이 이어받으므로 **ingress-nginx 제거부터 Traefik Ready까지 접속이 끊긴다** (4.1의 플랫폼 설치 시간만큼, 수 분~십여 분).

**4.1보다 먼저** 1~2단계를 수행한다.

1. k3s 내장 traefik이 꺼져 있는지 확인 (C11, 4.5): `kubectl -n kube-system get helmchart`에 `traefik`, `traefik-crd`가 없어야 한다. 남아 있으면 IngressClass `traefik` 이름이 겹친다
2. 기존 ingress-nginx 제거 (C10): `helm -n ingress-nginx uninstall <릴리스>` 또는 설치 매니페스트로 삭제 → NodePort 32088 해제
3. 4.1 진행 → `kubectl -n traefik get svc traefik`에서 NodePort 32088·EXTERNAL-IP(C9 대역) 확인
4. Ingress class 확인: `kubectl get ingress -A` (모두 `traefik`)
5. 맥에서 `curl -H 'Host: app.wafer.local' http://192.168.0.57:32088/` 응답 확인 (항목 3 이후에는 308 리다이렉트가 정상. HTTPS 확인은 `curl -k --resolve app.wafer.local:32443:192.168.0.57 https://app.wafer.local:32443/`). LB IP를 쓰려면 `/etc/hosts`의 `*.wafer.local`을 Traefik EXTERNAL-IP로 바꾸고 포트 없이 접속 (UTM 브리지가 Wi-Fi면 L2 ARP가 맥 밖으로는 안 나갈 수 있다 — C9)
6. 기존 ingress-nginx의 `nginx.ingress.kubernetes.io/rewrite-target: /`는 경로 `/`에 대해 실질적으로 동작이 없어 제거했다. 경로 재작성이 필요한 화면이 있으면 Traefik Middleware로 추가
7. Grafana Ingress는 monitoring 차트 것만 남겼다 (ingress 차트의 `wafer-grafana-ingress`는 중복이라 제거 → ArgoCD prune으로 삭제됨)
8. (항목 3) **순서 주의**: 리다이렉트가 켜지면 기존 frontend 이미지(http 주소 하드코딩)는 API 호출·Grafana iframe이 혼합 콘텐츠로 막힌다. GitLab CI 변수(5.1)를 먼저 바꾸고 frontend 파이프라인을 다시 돌려 새 이미지가 배포된 뒤 traefik을 sync한다.
9. (항목 3) HTTP 요청은 HTTPS로 리다이렉트된다. 인증서는 cert-manager가 `wafer-ca`로 발급한다 (`kubectl get certificate -A` Ready). 맥에 CA를 신뢰 등록(4.6)하지 않으면 브라우저 경고가 뜬다. 리다이렉트 대상 포트는 C13

### 4.5 노드 준비·k3s 설정 (항목 2)

```bash
# cp1: 내장 traefik·servicelb 비활성화 (MetalLB·ArgoCD Traefik과 충돌)
sudo tee -a /etc/rancher/k3s/config.yaml <<'CFG'
disable:
  - traefik
  - servicelb
CFG
sudo systemctl restart k3s

# 모든 노드: Longhorn 선행 패키지 (Ubuntu/Debian 기준)
sudo apt-get install -y open-iscsi nfs-common
sudo systemctl enable --now iscsid
echo iscsi_tcp | sudo tee /etc/modules-load.d/iscsi_tcp.conf && sudo modprobe iscsi_tcp
# Ubuntu cloud image: multipathd가 Longhorn 장치를 잡아 "already mounted or busy"가 나므로 끈다
sudo systemctl disable --now multipathd.socket multipathd 2>/dev/null || true
# (선택) 사전 점검: longhornctl check preflight

# Longhorn 디스크를 만들 노드(cp1, w1)에만 라벨. ai2에는 붙이지 않는다 → 복제본이 ai2에 생기지 않음
kubectl label node k3s-cp1 k3s-w1 node.longhorn.io/create-default-disk=true
```

- 기존 `/etc/rancher/k3s/config.yaml`에 `disable:` 항목이 이미 있으면 목록에 합친다.
- servicelb를 끄면 기존 `svclb-*` DaemonSet이 사라진다. LoadBalancer 타입 Service는 MetalLB가 IP를 줄 때까지 `<pending>`이다.

### 4.6 사설 CA 신뢰 등록 (맥)

cert-manager가 만든 루트 CA(`cert-manager/wafer-root-ca`)를 맥에 신뢰 등록하면 이후 `*.wafer.local` TLS(항목 3) 경고가 사라진다.

```bash
kubectl -n cert-manager get secret wafer-root-ca -o jsonpath='{.data.ca\.crt}' | base64 -d > wafer-root-ca.crt
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain wafer-root-ca.crt
```

### 4.7 NodePort 접속용 HTTPS 리다이렉트 (C13)

Traefik 차트는 리다이렉트 대상에 포트 번호를 받지 않는다(`to: ":32443"`은 렌더링 실패). MetalLB IP로 접속할 수 없어 NodePort만 쓴다면 `traefik/values.yaml`을 아래처럼 바꾼다.

```yaml
traefik:
  ports:
    web:
      nodePort: 32088
      http:
        redirections: null   # 기존 to: websecure 블록 제거
  additionalArguments:
    - --entryPoints.web.http.redirections.entryPoint.to=:32443
    - --entryPoints.web.http.redirections.entryPoint.scheme=https
    - --entryPoints.web.http.redirections.entryPoint.permanent=true
```

함께 바꿀 것: GitLab CI 변수(5.1)를 `:32443` 주소로, `monitoring/values.yaml`의 Grafana `root_url`을 `https://grafana.wafer.local:32443/`로.

### 4.8 스토리지 이전: local-path → Longhorn (항목 4)

StatefulSet의 `volumeClaimTemplates`는 바꿀 수 없어 **삭제 후 재생성 + 데이터 복원**으로 옮긴다. mysql, minio 순서로 하나씩 한다. **다운타임이 생긴다.**

선행: 4.5(Longhorn 노드 준비), Longhorn Application Healthy, 2장 사전 백업 완료.

0. `backend`, `mysql`, `minio` Application의 자동 sync를 끈다 (4.12). 끄지 않으면 selfHeal이 아래 수동 scale·삭제를 곧바로 되돌린다
1. 쓰기 중지: `kubectl -n application scale deploy backend-backend --replicas=0`
2. 마지막 덤프를 2장 명령으로 다시 받는다
3. 기존 PV를 보존 정책으로 바꿔 둔다 (만일을 위한 원본 보관):
   ```bash
   PV=$(kubectl -n storage get pvc data-mysql-mysql-0 -o jsonpath='{.spec.volumeName}')
   kubectl patch pv $PV -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'
   ```
4. StatefulSet·PVC 삭제: `kubectl -n storage delete sts mysql-mysql && kubectl -n storage delete pvc data-mysql-mysql-0`
5. `argocd app sync mysql`(자동 sync를 끈 상태라 수동) → StatefulSet이 `storageClassName: longhorn` PVC로 다시 생성되고, 빈 데이터 디렉터리를 Secret 값으로 초기화한다
6. 복원:
   ```bash
   gunzip -c ~/wafer-backup/mysql-<날짜>.sql.gz | kubectl -n storage exec -i mysql-mysql-0 -- sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot'
   ```
7. minio도 같은 방식 (`data-minio-minio-0`), 복원은 2장의 port-forward + `mc mirror ./minio/wafer-images wafer/wafer-images` (버킷이 없으면 `mc mb` 먼저)
8. 쓰기 재개: 세 Application의 자동 sync를 다시 켠다 (4.12) → selfHeal이 backend replica를 values 값으로 되돌린다
9. 확인 후 보존해 둔 local-path PV는 `kubectl delete pv $PV` (노드의 실제 디렉터리는 local-path가 지우지 않으므로 w1의 `/var/lib/rancher/k3s/storage/`에서 정리)

### 4.9 MySQL 수동 failover (full 프로파일)

primary(`mysql-mysql-0`)가 복구되지 않을 때 replica를 승격한다. **Service 전환은 Git으로 한다** — kubectl로 Service를 바꾸면 selfHeal·다음 sync가 되돌리고, 복제 설정 Job이 다시 돌아 혼란이 생긴다. (자동 sync를 잠시 끄는 방법은 4.12)

1. replica 승격 (bash에서):
   ```bash
   kubectl -n storage exec mysql-mysql-replica-0 -- sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -e "STOP REPLICA; RESET REPLICA ALL; SET PERSIST super_read_only=OFF; SET PERSIST read_only=OFF;"'
   ```
2. kim-minji-helm `mysql/values-full.yaml`에 `failover.promoted: true`를 커밋 → sync
   - primary Service(`mysql-mysql`)가 replica Pod를 가리킨다 (backend 설정 변경 없음)
   - 복제 설정 Job이 만들어지지 않는다
   - NetworkPolicy는 backend → mysql-replica 경로를 이미 허용한다
3. `kubectl -n application rollout restart deploy backend-backend` (커넥션 풀 재연결)

- 승격 전 마지막 GTID 위치 확인: `SHOW REPLICA STATUS\G`의 `Retrieved_Gtid_Set` / `Executed_Gtid_Set`
- 승격된 replica에서는 설정 Job이 다시 돌아도 clone하지 않는다 (사용자 데이터가 있으면 Job이 멈추고 실패)
- 원래 구성으로 되돌리기 (데이터는 승격된 replica가 최신):
  1. 4.12로 backend·mysql 자동 sync 끄기 → 쓰기 중지(backend replica 0) → 승격된 서버에서 덤프 (2장 명령을 `mysql-mysql-replica-0`에 실행)
  2. 옛 primary PVC(`data-mysql-mysql-0`) 삭제 → `failover.promoted: false` 커밋 → sync로 빈 primary 생성 → 덤프 복원
  3. replica PVC(`data-mysql-mysql-replica-0`)와 StatefulSet Pod 삭제 → sync → 설정 Job이 새 primary에서 다시 clone
  4. 자동 sync 다시 켜기 (4.12) → backend 재개

### 4.10 k3s 데이터스토어 백업 (C17)

매니페스트는 Git(GitOps)에 있으므로, 클러스터 자체에서 따로 받을 것은 데이터스토어·토큰·Sealed Secrets 키뿐이다. cp1에서 실행한다.

```bash
# SQLite (server 1대 기본)
sudo apt-get install -y sqlite3
sudo mkdir -p /var/backups/k3s
sudo sqlite3 /var/lib/rancher/k3s/server/db/state.db ".backup '/var/backups/k3s/state-$(date +%F).db'"
sudo cp /var/lib/rancher/k3s/server/token /var/backups/k3s/token
# 클러스터 밖으로 복사 (맥 또는 외부 S3)
scp cp1:/var/backups/k3s/state-*.db ~/wafer-backup/k3s/
```

- 매일 돌리려면 cp1의 root crontab에 `.backup` 줄을 등록하고 7일 지난 파일은 `find /var/backups/k3s -mtime +7 -delete`
- 복원: `sudo systemctl stop k3s` → 기존 `db/` 디렉터리를 통째로 다른 이름으로 보관 → 새 `db/`에 백업본을 `state.db`로 복사 (**`state.db-wal`, `state.db-shm`을 남기지 않는다** — WAL이 남으면 옛 내용이 복원본에 다시 적용될 수 있다. `.backup` 결과물은 그 자체로 완결됨) → `sudo systemctl start k3s`
- embedded etcd라면(C17) `sudo k3s etcd-snapshot save` (S3로 바로 올리려면 `--etcd-s3 --etcd-s3-endpoint ... --etcd-s3-bucket ...`)

### 4.11 백업 CronJob 덤프로 복원

백업 이미지의 `mysqldump`는 MariaDB 클라이언트라, 버전에 따라 덤프 첫 줄에 `/*M!999999\- enable the sandbox mode */`가 들어간다. MySQL 8 클라이언트(`mysql-mysql-0` 안)는 이 줄에서 멈추므로 지우고 넣는다. 덤프에는 `CREATE DATABASE`가 없으므로 대상 DB를 지정한다.

```bash
# 맥에서: 외부 S3(또는 port-forward한 클러스터 MinIO)에서 받아 복원
mc cat ext/<bucket>/wafer/mysql/backup_<시각>.sql.gz | gunzip | sed '1{/999999/d;}' \
  | kubectl -n storage exec -i mysql-mysql-0 -- sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot "$MYSQL_DATABASE"'
```

- 주간 복원 검증 CronJob은 같은 MariaDB 클라이언트로 복원하므로, 이 MySQL 클라이언트 경로는 6.1에서 한 번 직접 확인한다.

### 4.12 유지보수 중 자동 sync 끄기·켜기 (항목 7)

모든 Application은 prune·selfHeal이 켜져 있어 kubectl로 바꾼 것은 곧 Git 상태로 되돌아간다. 데이터 이전처럼 잠시 수동 조작이 필요하면 해당 Application만 자동 sync를 끈다. ApplicationSet은 `spec.syncPolicy.automated`만은 되돌리지 않게 설정돼 있다(`ignoreApplicationDifferences`).

**항목 7 적용 시 1회**: 항목 1 때 만들어진 앱 Application(mysql, minio, backend, ai-serving, frontend)은 `automated: {}`(prune·selfHeal 없음)로 남아 있고, ApplicationSet은 이 필드를 다시 덮어쓰지 않는다. 직접 켠다.

```bash
# 1) prune으로 지워질 리소스부터 확인 (예: dev인데 남아 있는 mysql-mysql-replica StatefulSet)
for app in mysql minio backend ai-serving frontend; do argocd app diff $app; done
# 2) prune·selfHeal 켜기
for app in mysql minio backend ai-serving frontend; do
  kubectl -n gitops patch application $app --type merge -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
done
```

유지보수 때 끄고 켜는 방법 (대상 앱 목록은 작업에 맞게 바꾼다):

```bash
# 끄기 (이미 꺼져 있어도 오류 없음)
for app in backend mysql minio; do
  kubectl -n gitops patch application $app --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'
done
# 켜기 (작업 후 반드시)
for app in backend mysql minio; do
  kubectl -n gitops patch application $app --type merge -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
done
```

- 끈 상태를 잊지 않도록 작업 시작·끝을 Slack에 남긴다. `argocd app list`의 SYNC POLICY 열로 확인 (`<none>`이면 꺼짐)
- ApplicationSet 템플릿의 `automated` 값을 Git에서 바꿔도 이미 만들어진 Application에는 반영되지 않는다 (같은 이유). 바꿨다면 위 '켜기' 명령으로 맞춘다
- (항목 7 적용 시 1회) ai-serving Deployment에서 `replicas` 필드가 빠지면서 첫 sync 때 Pod 수가 잠깐 1로 돌아갔다가 HPA가 다시 조정한다

## 5. 채워야 할 시크릿

모든 값은 `kubeseal`로 암호화해 해당 values 파일의 placeholder 자리에 커밋한다. 평문 커밋 금지.

| 시크릿 | 네임스페이스 | 위치 | 상태 |
|---|---|---|---|
| `mysql-secret` (4개 키) | storage | `kim-minji-helm/mysql/values.yaml` `sealedSecret.encryptedData` | helm#3. **현재 클러스터 값 그대로** 암호화 (kim-minji-helm README) |
| `minio-secret` (`accessKey`, `secretKey`) | storage | `kim-minji-helm/minio/values.yaml` `sealedSecret.encryptedData` | **현재 클러스터 값 그대로** 암호화, 기존 Secret annotate (kim-minji-helm README) |
| `grafana-admin` (`admin-user`, `admin-password`) | monitoring | `kim-minji-infra/monitoring/values.yaml` `grafanaAdmin.sealedSecret.encryptedData` | 새 비밀번호로 생성 (아래 5.2) |
| `mysql-secret`의 `MYSQL_REPLICATION_PASSWORD` | storage | 위와 같은 위치에 키 추가 | full(replica)에서만 필요. 새 값 (kim-minji-helm README) |
| `backup-s3` (`accessKey`, `secretKey`) | storage | `kim-minji-infra/backup/values.yaml` `external.sealedSecret.encryptedData` | 외부 S3 계정 (5.3). `external.enabled: true`일 때만 필요 (기본 false → 클러스터 내 백업만). 켰는데 비어 있거나 엔드포인트가 placeholder면 렌더링 실패 |
| `longhorn-backup-s3` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINTS`) | longhorn-system | `kim-minji-infra/longhorn/values.yaml` `backupS3.sealedSecret.encryptedData` | 선택 (5.3). 비어 있으면 만들지 않음 → 스냅샷만 동작 |
| `alertmanager-slack` (`webhook-url`) | monitoring | `kim-minji-infra/monitoring/values.yaml` `alertmanagerSlack.sealedSecret.encryptedData` | Slack Incoming Webhook URL (5.4). 비어 있으면 monitoring 앱 렌더링 실패 |
| `argocd-notifications-secret` (`slack-webhook-url`) | gitops | `kim-minji-infra/argocd-notifications/values.yaml` `sealedSecret.encryptedData` | 같은 Webhook URL (5.4). 기존 Secret annotate 필요 |
| GitLab CI 변수 `SLACK_WEBHOOK_URL` (masked, protected 해제) | ai·backend·frontend·infra 프로젝트 | GitLab 프로젝트(또는 그룹) Settings → CI/CD → Variables | 같은 Webhook URL. 없으면 실패 알림 job이 건너뛴다 |

- mysql·minio·grafana-admin은 값이 비어 있으면 **렌더링이 실패**한다 (평문 배포 방지). ArgoCD 앱이 `ComparisonError`로 멈추고 기존 리소스는 그대로 남는다. 4.1 전에 모두 채워 커밋한다.
- 백업 CronJob은 `mysql-secret`, `minio-secret`을 그대로 참조하므로 별도 시크릿이 없다.

### 5.1 GitLab CI 변수 (frontend 프로젝트, 시크릿 아님)

| 변수 | 값 (C13) |
|---|---|
| `VITE_API_URL` | `https://api.wafer.local` (NodePort만 가능하면 `https://api.wafer.local:32443`) |
| `VITE_GRAFANA_URL` | `https://grafana.wafer.local` (NodePort만 가능하면 `https://grafana.wafer.local:32443`) |

값을 바꾼 뒤 frontend 파이프라인을 다시 돌려야 이미지에 반영된다.

### 5.2 grafana-admin 생성

bash에서 실행한다 (zsh의 `read -p`는 동작이 다르다).

```bash
NS=monitoring; NAME=grafana-admin
read -r -s -p "새 Grafana admin 비밀번호: " PW; echo
for kv in "admin-user=admin" "admin-password=$PW"; do
  k=${kv%%=*}; v=${kv#*=}
  printf '      %s: %s\n' "$k" "$(printf '%s' "$v" | kubeseal --raw --namespace $NS --name $NAME --from-file=/dev/stdin)"
done
```

출력된 2줄을 `monitoring/values.yaml`의 `grafanaAdmin.sealedSecret.encryptedData: {}`에서 `{}`를 지우고 붙여 넣는다. Grafana는 영속 볼륨이 없어(재시작마다 초기화) 새 Secret 값이 바로 admin 비밀번호가 된다.

### 5.3 외부 S3 백업 계정 (C16)

bash에서 실행한다. 버킷 쓰기·목록·삭제 권한만 가진 전용 키를 만든다.

```bash
read -r -p "S3 access key: " AK; read -r -s -p "S3 secret key: " SK; echo
seal() { printf '%s' "$1" | kubeseal --raw --namespace "$2" --name "$3" --from-file=/dev/stdin; }
# backup/values.yaml → external.sealedSecret.encryptedData (6칸 들여쓰기)
printf '      accessKey: %s\n      secretKey: %s\n' "$(seal "$AK" storage backup-s3)" "$(seal "$SK" storage backup-s3)"
# longhorn/values.yaml → backupS3.sealedSecret.encryptedData (6칸 들여쓰기). AWS가 아니면 AWS_ENDPOINTS에 엔드포인트 URL
EP=https://PLACEHOLDER-S3-ENDPOINT
printf '      AWS_ACCESS_KEY_ID: %s\n      AWS_SECRET_ACCESS_KEY: %s\n      AWS_ENDPOINTS: %s\n' \
  "$(seal "$AK" longhorn-system longhorn-backup-s3)" "$(seal "$SK" longhorn-system longhorn-backup-s3)" "$(seal "$EP" longhorn-system longhorn-backup-s3)"
```

그다음 커밋한다.

- `backup/values.yaml`: `external.enabled: true`, `endpoint`, `bucket` (C16)
- `longhorn/values.yaml`: `longhorn.defaultBackupStore.backupTarget`, `recurringJobs.backup.enabled: true`
- Longhorn 설치 후에 backupTarget을 바꾸면 기본 설정 ConfigMap 변경이 반영되지 않을 수 있다. `kubectl -n longhorn-system get backuptargets.longhorn.io default -o yaml`의 `spec.backupTargetURL`이 비어 있으면 직접 맞춘다:
  `kubectl -n longhorn-system patch backuptargets.longhorn.io default --type merge -p '{"spec":{"backupTargetURL":"s3://<bucket>@<region>/longhorn","credentialSecret":"longhorn-backup-s3"}}'`
- 외부 S3의 MinIO 미러(`{prefix}/minio/`)는 지우지 않고 쌓인다 (`mc mirror --overwrite`, 삭제 반영 안 함). 용량이 걱정되면 버킷 수명주기 규칙을 둔다

### 5.4 Slack Incoming Webhook (항목 6)

Slack 앱(Incoming Webhooks)에서 알림 채널용 Webhook URL 하나를 만든다. 같은 URL을 세 곳에 쓴다. bash에서 실행한다.

```bash
read -r -s -p "Slack Webhook URL: " URL; echo
seal() { printf '%s' "$1" | kubeseal --raw --namespace "$2" --name "$3" --from-file=/dev/stdin; }
# monitoring/values.yaml → alertmanagerSlack.sealedSecret.encryptedData (6칸)
printf '      webhook-url: %s\n' "$(seal "$URL" monitoring alertmanager-slack)"
# argocd-notifications/values.yaml → sealedSecret.encryptedData (4칸)
printf '    slack-webhook-url: %s\n' "$(seal "$URL" gitops argocd-notifications-secret)"
```

- ArgoCD 설치가 만든 `argocd-notifications-secret`은 controller가 덮어쓰지 않으므로 먼저 소유권을 넘긴다: `kubectl -n gitops annotate secret argocd-notifications-secret sealedsecrets.bitnami.com/managed=true`
- GitLab: 4개 프로젝트에 `SLACK_WEBHOOK_URL` 변수(Masked). 실패 알림 job은 develop·main(infra는 전 브랜치)에서 돈다

## 6. 배포 후 확인 체크리스트

작성 예정 (항목 2~9에서 계속 추가). 노드 1대(cp1만) / 3대 켠 상태로 나눠 확인한다.

### 6.1 dev, cp1 1대만 켠 상태

- [ ] Running/Completed가 아닌 Pod 없음 (Pending뿐 아니라 Terminating·Unknown 포함): `kubectl get pods -A | grep -vE 'Running|Completed'`. **예외(현재 알려진 것)**:
  - 없음 (4.8 스토리지 이전 후). 이전 전이라면 mysql·minio는 local-path PV가 있는 k3s-w1이 꺼져 있으면 Pending
- [ ] coredns replica 1 Running
- [ ] ai-serving이 cp1에 Running (ai2 선호지만 ai2가 꺼져 있으므로), OOMKilled 없음 (C2)
- [ ] (항목 6) dev에서 w1·ai2를 꺼도 Slack에 노드 다운·TargetDown 알림이 오지 않음 (cp1 관련·서비스·백업 알림은 온다)
- [ ] Prometheus retention 2d: `kubectl -n monitoring get prometheus -o jsonpath='{.items[0].spec.retention}'`
- [ ] Traefik 1개 Running, `curl -H 'Host: app.wafer.local' http://<cp1 IP>:32088/` 200
- [ ] MetalLB: `kubectl -n traefik get svc traefik`에 EXTERNAL-IP 할당 (C9 대역)
- [ ] cert-manager: `kubectl get clusterissuer wafer-ca` Ready=True
- [ ] Longhorn: `kubectl -n longhorn-system get nodes.longhorn.io` cp1 Ready·Schedulable, 볼륨이 있으면 복제본이 cp1에 있음
- [ ] Sealed Secrets: 기존 SealedSecret(backend-secret 등) `kubectl get sealedsecret -A` 모두 Synced (키 인계 확인)
- [ ] (항목 3) `mysql-secret`, `minio-secret`, `grafana-admin` SealedSecret Synced, mysql·minio Pod 재시작 후에도 정상 (값이 기존과 같아야 함)
- [ ] (항목 3) HTTPS: `curl -I http://app.wafer.local` → 308 → `https://...`, `curl --cacert wafer-root-ca.crt https://app.wafer.local` 200. frontend Monitoring 페이지의 Grafana iframe 3개 표시
- [ ] (항목 3) Grafana: 비로그인으로 대시보드 조회 가능, Explore 메뉴 없음, 새 admin 비밀번호로 로그인
- [ ] (항목 3) NetworkPolicy: 웨이퍼 이미지 업로드 → 추론 → 결과 저장 E2E 성공 (frontend→backend→ai-serving, backend→mysql/minio, ai-serving→minio). 막힌 경로 테스트: `kubectl -n application run np-test --rm -it --image=busybox -- wget -T3 -O- http://minio-minio.storage:9000/minio/health/live` → **timeout**이어야 정상 (정책이 없으면 200이 온다. `/`는 403이라 정책과 무관하게 실패하므로 쓰지 않는다)
- [ ] (항목 3) 백업 Job 수동 실행 성공: `kubectl -n storage create job --from=cronjob/mysql-backup mysql-backup-manual` → Completed, 로그에 "백업 완료"
- [ ] (항목 4) mysql·minio PVC가 `longhorn`: `kubectl -n storage get pvc`, 볼륨 복제본이 cp1에 있음
- [ ] (항목 4) 외부 S3에 덤프·미러 존재: `mc ls ext/<bucket>/wafer/mysql/`, `mc ls ext/<bucket>/wafer/minio/` (맥에서 `mc alias set ext ...`)
- [ ] (항목 4) MySQL 클라이언트 복원 경로(4.11)를 임시 DB로 한 번 확인: `mysql -uroot -e 'CREATE DATABASE dr_test'` 후 4.11 명령의 대상 DB를 `dr_test`로 → 행 수 확인 → `DROP DATABASE dr_test`
- [ ] (항목 4) 복원 검증 수동 실행: `kubectl -n storage create job --from=cronjob/mysql-restore-check restore-check-manual` → 로그 "복원 검증 성공", 테이블별 행 수가 운영 DB와 비슷
- [ ] (항목 4) MySQL binlog·GTID: `SELECT @@gtid_mode, @@log_bin` → `ON`, `1`
- [ ] (항목 4) Longhorn 정기 스냅샷: `kubectl -n longhorn-system get recurringjobs`, 다음 날 `snapshots.longhorn.io` 생성 확인

### 6.2 full, 3대 모두 켠 상태

- [ ] `env=full` 라벨 적용 후 모든 Application Synced/Healthy
- [ ] coredns 2개가 서로 다른 노드: `kubectl -n kube-system get pod -l k8s-app=kube-dns -o wide`
- [ ] frontend, backend 각 2개 Running
- [ ] ai-serving이 k3s-ai2에만 배치: `kubectl -n ai-serving get pod -o wide`
- [ ] Traefik 2개가 서로 다른 노드, PDB 존재: `kubectl -n traefik get pod -o wide`, `kubectl -n traefik get pdb`
- [ ] Longhorn 볼륨 복제본 2개가 cp1·w1에 분산 (4.3 복제 수 변경 후), ai2에는 복제본 없음
- [ ] (항목 4) MySQL replica: `mysql-mysql-replication-setup` Job Completed, `kubectl -n storage exec mysql-mysql-replica-0 -- sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -e "SHOW REPLICA STATUS\G"'`에서 `Replica_IO_Running: Yes`, `Replica_SQL_Running: Yes`, primary와 replica가 서로 다른 노드
- [ ] (항목 6) Alertmanager Slack 수신 테스트: `kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-alertmanager 9093` 후
  `amtool alert add RunbookTest severity=warning namespace=test --annotation=summary="알림 테스트" --alertmanager.url=http://127.0.0.1:9093` → Slack 채널 수신, 5분 뒤 resolved
- [ ] (항목 6) 규칙 로드: Prometheus UI(port-forward 9090) → Alerts에 `wafer.backend`·`wafer.ai-serving`·`wafer.backup` 그룹, k3s 컴포넌트(KubeControllerManagerDown 등) 알림 없음
- [ ] (항목 6) ArgoCD Notifications: `kubectl -n gitops exec deploy/argocd-notifications-controller -- argocd admin notifications template notify app-sync-failed frontend --recipient slack-webhook` → Slack 수신
- [ ] (항목 6) GitLab CI 실패 알림: 아무 브랜치에서 실패하는 커밋(예: infra의 Dockerfile 오타)을 푸시하거나 파이프라인을 수동 실패시켜 Slack 수신 확인
- [ ] (항목 7) 이미지 태그 MR: 앱 레포 main 파이프라인 후 kim-minji-helm에 `ci/<chart>-<sha>` MR 생성 → 머지 → ArgoCD Synced, 새 이미지로 롤아웃. 레지스트리에 `latest` 태그가 새로 생기지 않음
- [ ] (항목 7) selfHeal (autoscaling을 끈 차트에서만): `kubectl -n application scale deploy frontend-frontend --replicas=3` → 잠시 후 values 값으로 되돌아감
- [ ] (항목 7) `argocd app list`에서 모든 앱의 SYNC POLICY가 `Auto-Prune` (4.12의 1회 절차 적용 확인)
- [ ] (항목 5) PDB: `kubectl get pdb -A` → frontend·backend(minAvailable 1), ai-serving(maxUnavailable 1), coredns, traefik
- [ ] (항목 5) frontend·backend·traefik 2개가 cp1·w1에 하나씩, ai2에는 없음: `kubectl -n application get pod -o wide`, `kubectl -n traefik get pod -o wide` (ai2는 nodeAffinity로 제외)
- [ ] (항목 5) drain 무중단 확인 (위 항목에서 w1에 frontend·backend가 있는 것을 먼저 확인): 맥에서 `while true; do curl -sk -o /dev/null -w '%{http_code}\n' https://app.wafer.local/; sleep 1; done`을 띄운 채 `kubectl drain k3s-w1 --ignore-daemonsets --delete-emptydir-data` → 200이 계속 나오는지, drain이 PDB 때문에 멈추지 않는지 → `kubectl uncordon k3s-w1`
- [ ] (항목 4) 복제 확인: primary에 기록한 레코드가 replica에서 조회됨, replica에 쓰기 시도 시 `super-read-only` 오류

## 7. 롤백

- ApplicationSet 전환 롤백 (워크로드는 유지된다):
  1. `kubectl -n gitops delete application root` (`root`에는 finalizer가 없어 ApplicationSet은 남는다)
  2. `kubectl -n gitops delete applicationset platform apps` (`preserveResourcesOnDeletion: true`라 Application이 지워져도 리소스는 유지)
  3. 기존 Application 매니페스트(Git 이력의 `*/application.yaml`) 재적용

- Traefik 전환 롤백:
  1. `ingress`/`network-policy`/`monitoring`의 class·네임스페이스 값을 `nginx`/`ingress-nginx`로 되돌리고, platform ApplicationSet에서 `traefik` 원소를 제거해 커밋·sync
  2. ApplicationSet이 `preserveResourcesOnDeletion: true`라 Traefik 리소스는 남는다 → 직접 삭제해 NodePort 32088·IngressClass를 비운다:
     `kubectl delete ns traefik`, `kubectl delete ingressclass traefik`, `kubectl delete clusterrole,clusterrolebinding traefik-traefik`
  3. 기존 ingress-nginx 재설치 (NodePort 32088)
- 스토리지 이전 롤백 (4.8 도중 문제): helm 레포의 `storageClassName`을 `""`로 되돌려 커밋 → 새 Longhorn PVC·StatefulSet 삭제 → sync(local-path로 재생성) → 2장 백업으로 복원. 3단계에서 보존한 옛 PV를 다시 쓰려면 PV의 `claimRef`를 지우고 같은 이름의 PVC가 그 PV를 지정하도록(`volumeName`) 만든다
- MySQL replica 끄기: `env=dev` (또는 `mysql/values-full.yaml`의 `replication.enabled: false`) → replica StatefulSet은 prune으로 삭제된다 (항목 7 이후). **PVC `data-mysql-mysql-replica-0`은 ArgoCD가 관리하지 않으므로 직접 삭제**한다. PVC를 남겨 두면 binlog 보관 기간(3일)이 지난 뒤 full을 다시 켰을 때 replica가 따라잡지 못해(오류 1236) 설정 Job이 계속 실패한다
- PDB가 drain을 막을 때(예: 노드 1대만 남은 상태): 해당 Pod가 다른 노드에 Ready로 뜨기를 기다리거나, 급하면 `kubectl delete pdb <이름>` (다음 sync에서 다시 생성된다). full에서 노드를 2대 이상 끌 계획이면 먼저 `env=dev`로 바꾼다(4.3)
- 이미지 태그 MR 운영: 같은 차트의 `ci/` MR이 여러 개 열려 있으면 **가장 최신 것만 머지**하고 나머지는 닫는다 (오래된 MR을 나중에 머지하면 이미지가 되돌아간다)
- 배포 롤백: kim-minji-helm에서 해당 MR을 revert(또는 이전 SHA로 태그를 되돌리는 MR) → 머지 → ArgoCD가 이전 이미지로 롤아웃
- 알림 참고:
  - Watchdog(상시 알림)은 Slack으로 보내지 않으므로 "알림 경로 자체가 죽은 것"은 감지하지 못한다. 6.2의 amtool 테스트로 주기적으로 확인한다
  - `KubeCPUOvercommit`/`KubeMemoryOvercommit`(기본 규칙)는 VM이 작으면 full에서 상시 warning일 수 있다 (C12). 계속 울리면 VM 크기를 조정하거나 route에 `null` 규칙을 추가한다
  - 백업 실패는 `BackupJobFailed`(CronJob 기준, 다음 성공 시 자동 해소)로만 알린다. storage의 기본 `KubeJobFailed`는 음소거했다
- 알림 끄기: Slack 소음이 심하면 `monitoring/values.yaml`의 `route.routes`에 `receiver: "null"` 규칙을 추가해 커밋 (일시적으로는 Alertmanager UI의 Silence). Alertmanager 자체를 끄려면 `alertmanager.enabled: false`
- NetworkPolicy 롤백: `network-policy/values.yaml`의 `defaultDenyNamespaces: []`(또는 문제 네임스페이스만 제외)로 커밋 → 해당 네임스페이스의 default-deny·DNS 허용·egress 허용 정책이 함께 제거되고, 기존 ingress 허용 규칙만 남는다
- HTTPS 리다이렉트 롤백: `traefik/values.yaml`의 `ports.web.http.redirections` 제거 커밋 (TLS 인증서는 그대로 두어도 무방)
- k3s 설정 롤백: `/etc/rancher/k3s/config.yaml`의 `disable` 항목 제거 후 `systemctl restart k3s`

작성 예정 (항목 8~9). 원칙:

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

### 8.1 요약 (항목 6 기준, 2026-10-26)

| 구분 | dev (cp1 1대) CPU | dev 메모리 | full (3대) CPU | full 메모리 |
|---|---|---|---|---|
| 레포 워크로드 requests | 1660m | 3.31Gi | 3175m | 6.38Gi |
| 레포 워크로드 (HPA 최대치) | 1910m | 3.81Gi | 4675m | 9.38Gi |
| 레포 밖·동적 생성 구성요소 (추정, C8) | ~0.4 core | ~1.5Gi | ~1.2 core | ~2.2Gi |
| k3s 프로세스·OS (추정) | — | ~1.0Gi | — | ~2.2Gi (server 1.0 + agent 0.6×2) |
| **합계 (HPA 최소)** | **~2.1 core** | **~5.8Gi** | **~4.4 core** | **~10.8Gi** |
| **합계 (HPA 최대)** | **~2.3 core** | **~6.3Gi** | **~5.9 core** | **~13.8Gi** |

레포 밖·동적 생성 구성요소 추정(실제 사용량 기준):
- ArgoCD ~800Mi, k3s 내장 coredns 70Mi(100m)·metrics-server 70Mi(100m)·local-path 20Mi (full은 coredns 2개분)
- Longhorn이 직접 만드는 Pod(렌더링 결과에 없음): instance-manager(longhorn-manager가 도는 **모든 노드**, full에서는 ai2 포함. CPU는 노드 allocatable의 dev 5% / full 8% 예약), CSI plugin DaemonSet, CSI controller(attacher·provisioner·resizer·snapshotter, dev 각 1 / full 각 2), engine-image → dev ~0.5Gi, full ~1.2Gi
- ingress-nginx·sealed-secrets는 항목 2부터 레포 워크로드(traefik, sealed-secrets)로 계산

### 8.2 32GB 안에서의 배분 (가정, C7)

| 항목 | 메모리 |
|---|---|
| 맥 호스트 OS·앱 | ~6GB |
| GitLab CE (클러스터 밖) | ~4GB (권장 최소) |
| **VM 3대에 쓸 수 있는 양** | **~22GB** |
| 제안 배분 | cp1 8GB / w1 7GB / ai2 7GB |

- dev: 모든 Pod가 cp1에 몰린다. 메모리 ~6.3Gi(HPA 최대) < cp1 8GB → 여유 ~1.7GB. **CPU requests ~2.3 core → cp1은 3 vCPU 이상 필요 (C12)**, 2 vCPU면 메모리가 남아도 CPU 부족으로 Pending. 이후 항목(Alertmanager 등)이 더해지면 cp1을 9GB로 늘리고 ai2를 6GB로 줄이는 안을 검토한다.
- full: 합계 ~10.8Gi(HPA 최소) / ~13.8Gi(최대) < 22GB. (항목 4: MySQL replica 200m·512Mi, 항목 6: Alertmanager 25m·64Mi 추가)
- 백업·복원 검증·복제 설정 Job은 실행될 때만 잠깐 뜨므로 합계에 넣지 않았다 (백업 이미지 기준 수십~수백 MiB).
- **주의 (full)**: ai-serving은 ai2 고정이라 HPA 4개면 ai2에 requests 4Gi, **limits 12Gi(3Gi×4)** 가 몰린다. ai2 7GB에서는 limits 기준 과다 할당이다. 항목 8(AI 서빙)에서 maxReplicas 또는 limit 조정을 검토한다.

### 8.3 워크로드별 상세

`(미설정 포함)`은 requests가 없는 컨테이너(sidecar 등)가 있다는 뜻이다. coredns는 k3s가 관리해 렌더링 결과에 requests가 없다.

dev (`--nodes 1`):

| 릴리스 | 종류 | 이름 | Pod 수 (최대) | requests/Pod (CPU · 메모리) | 합계 CPU | 합계 메모리 |
|---|---|---|---|---|---|---|
| ai-serving | Deployment | ai-serving-ai-serving | 1 (2) | 250m · 512Mi | 250m | 512Mi |
| backend | Deployment | backend-backend | 1 | 250m · 512Mi | 250m | 512Mi |
| cert-manager | Deployment | cert-manager | 1 | 25m · 64Mi | 25m | 64Mi |
| cert-manager | Deployment | cert-manager-cainjector | 1 | 10m · 64Mi | 10m | 64Mi |
| cert-manager | Deployment | cert-manager-webhook | 1 | 10m · 32Mi | 10m | 32Mi |
| coredns | Deployment | coredns | 1 | 0m · 0Mi (k3s 관리, 실제 100m · 70Mi) | 0m | 0Mi |
| frontend | Deployment | frontend-frontend | 1 | 100m · 128Mi | 100m | 128Mi |
| loki | DaemonSet | loki-promtail | 1 | 25m · 64Mi | 25m | 64Mi |
| loki | StatefulSet | loki | 1 | 50m · 128Mi | 50m | 128Mi |
| longhorn | DaemonSet | longhorn-manager | 1 | 50m · 192Mi (미설정 포함) | 50m | 192Mi |
| longhorn | Deployment | longhorn-driver-deployer | 1 | 0m · 0Mi (미설정 포함) | 0m | 0Mi |
| longhorn | Deployment | longhorn-ui | 1 | 0m · 0Mi (미설정 포함) | 0m | 0Mi |
| metallb | DaemonSet | metallb-speaker | 1 | 25m · 64Mi | 25m | 64Mi |
| metallb | Deployment | metallb-controller | 1 | 25m · 64Mi | 25m | 64Mi |
| minio | StatefulSet | minio-minio | 1 | 200m · 256Mi | 200m | 256Mi |
| monitoring | Alertmanager | monitoring-kube-prometheus-alertmanager | 1 | 25m · 64Mi | 25m | 64Mi |
| monitoring | DaemonSet | monitoring-prometheus-node-exporter | 1 | 20m · 32Mi | 20m | 32Mi |
| monitoring | Deployment | monitoring-grafana | 1 | 50m · 128Mi (미설정 포함) | 50m | 128Mi |
| monitoring | Deployment | monitoring-kube-prometheus-operator | 1 | 50m · 64Mi | 50m | 64Mi |
| monitoring | Deployment | monitoring-kube-state-metrics | 1 | 20m · 64Mi | 20m | 64Mi |
| monitoring | Prometheus | monitoring-kube-prometheus-prometheus | 1 | 100m · 256Mi | 100m | 256Mi |
| mysql | StatefulSet | mysql-mysql | 1 | 200m · 512Mi | 200m | 512Mi |
| node-local-dns | DaemonSet | node-local-dns | 1 | 100m · 64Mi | 100m | 64Mi |
| sealed-secrets | Deployment | sealed-secrets-controller | 1 | 25m · 64Mi | 25m | 64Mi |
| traefik | Deployment | traefik | 1 | 50m · 64Mi | 50m | 64Mi |
| **합계** | | | | | **1660m** | **3.31Gi** |
| **HPA 최대치 기준** | | | | | **1910m** | **3.81Gi** |

full (`--nodes 3`):

| 릴리스 | 종류 | 이름 | Pod 수 (최대) | requests/Pod (CPU · 메모리) | 합계 CPU | 합계 메모리 |
|---|---|---|---|---|---|---|
| ai-serving | Deployment | ai-serving-ai-serving | 1 (4) | 500m · 1024Mi | 500m | 1024Mi |
| backend | Deployment | backend-backend | 2 | 250m · 512Mi | 500m | 1024Mi |
| cert-manager | Deployment | cert-manager | 1 | 25m · 64Mi | 25m | 64Mi |
| cert-manager | Deployment | cert-manager-cainjector | 1 | 10m · 64Mi | 10m | 64Mi |
| cert-manager | Deployment | cert-manager-webhook | 1 | 10m · 32Mi | 10m | 32Mi |
| coredns | Deployment | coredns | 2 | 0m · 0Mi (k3s 관리, 실제 100m · 70Mi) | 0m | 0Mi |
| frontend | Deployment | frontend-frontend | 2 | 100m · 128Mi | 200m | 256Mi |
| loki | DaemonSet | loki-promtail | 3 | 50m · 128Mi | 150m | 384Mi |
| loki | StatefulSet | loki | 1 | 100m · 256Mi | 100m | 256Mi |
| longhorn | DaemonSet | longhorn-manager | 3 | 50m · 192Mi (미설정 포함) | 150m | 576Mi |
| longhorn | Deployment | longhorn-driver-deployer | 1 | 0m · 0Mi (미설정 포함) | 0m | 0Mi |
| longhorn | Deployment | longhorn-ui | 1 | 0m · 0Mi (미설정 포함) | 0m | 0Mi |
| metallb | DaemonSet | metallb-speaker | 3 | 25m · 64Mi | 75m | 192Mi |
| metallb | Deployment | metallb-controller | 1 | 25m · 64Mi | 25m | 64Mi |
| minio | StatefulSet | minio-minio | 1 | 200m · 256Mi | 200m | 256Mi |
| monitoring | Alertmanager | monitoring-kube-prometheus-alertmanager | 1 | 25m · 64Mi | 25m | 64Mi |
| monitoring | DaemonSet | monitoring-prometheus-node-exporter | 3 | 20m · 32Mi | 60m | 96Mi |
| monitoring | Deployment | monitoring-grafana | 1 | 50m · 128Mi (미설정 포함) | 50m | 128Mi |
| monitoring | Deployment | monitoring-kube-prometheus-operator | 1 | 50m · 64Mi | 50m | 64Mi |
| monitoring | Deployment | monitoring-kube-state-metrics | 1 | 20m · 64Mi | 20m | 64Mi |
| monitoring | Prometheus | monitoring-kube-prometheus-prometheus | 1 | 200m · 512Mi | 200m | 512Mi |
| mysql | StatefulSet | mysql-mysql | 1 | 200m · 512Mi | 200m | 512Mi |
| mysql | StatefulSet | mysql-mysql-replica | 1 | 200m · 512Mi | 200m | 512Mi |
| node-local-dns | DaemonSet | node-local-dns | 3 | 100m · 64Mi | 300m | 192Mi |
| sealed-secrets | Deployment | sealed-secrets-controller | 1 | 25m · 64Mi | 25m | 64Mi |
| traefik | Deployment | traefik | 2 | 50m · 64Mi | 100m | 128Mi |
| **합계** | | | | | **3175m** | **6.38Gi** |
| **HPA 최대치 기준** | | | | | **4675m** | **9.38Gi** |

> 이후 항목(KEDA, Tempo/OTel 등)이 추가될 때마다 이 표를 갱신한다.
