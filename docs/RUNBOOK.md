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
| C5 | 기존 ArgoCD Application 이름 | ApplicationSet이 차트명(`frontend`, `backend`, `ai-serving`, `mysql`, `minio`, `ingress`, `network-policy`, `coredns`, `node-local-dns`, `monitoring`, `loki`)으로 생성 | `argocd/apps/*.yaml` | `kubectl -n gitops get applications`. 이름이 다르면 4.1 절차에서 non-cascade 삭제. **앱 차트는 이름이 곧 Helm 릴리스명**이라, 다르면 리소스 이름·StatefulSet PVC가 새로 생겨 빈 DB/버킷으로 뜬다 → 해당 원소에 기존 릴리스명을 `releaseName`으로 지정하거나 2장 백업으로 데이터 이전 |
| C6 | ArgoCD 버전 | `templatePatch`(v2.10+), `ignoreMissingValueFiles` 사용 | `argocd/apps/platform.yaml` | `argocd version` 또는 argocd-server 이미지 태그 |
| C7 | VM 메모리 배분 | 8장 가정: cp1 8GB / w1 7GB / ai2 7GB | UTM 설정 | 8장 표와 `kubectl describe node` Allocatable |
| C4 | 기존 Sealed Secrets controller 설치 방식·이름·네임스페이스 | `kube-system/sealed-secrets-controller` (가정) | `sealed-secrets/values.yaml` `fullnameOverride`, 릴리스 네임스페이스(`argocd/apps/platform.yaml`) | `kubectl get deploy -A \| grep sealed`, `helm list -A`. 다르면 값을 맞춘다 (키 Secret이 같은 네임스페이스에 있어야 재사용됨). 기존 Deployment selector가 `app.kubernetes.io/name: sealed-secrets, app.kubernetes.io/instance: sealed-secrets`가 아니면(예: controller.yaml로 설치 → `name: sealed-secrets-controller`) selector 불변 오류가 나므로 4.1 전에 `kubectl -n kube-system delete deploy sealed-secrets-controller` (키 Secret은 남는다). 기존 Helm 릴리스가 있으면 리소스를 지우지 않도록 릴리스 메타데이터 Secret(`sh.helm.release.v1.<릴리스>.*`)만 삭제하고 `helm uninstall`은 하지 않는다 |
| C8 | 레포 밖 구성요소 실제 사용량 | 8장 추정치 | — | `kubectl top pod -A` (ArgoCD, k3s 내장 coredns·metrics-server·local-path, Longhorn instance-manager·CSI) |
| C9 | **(4.1 전 필수)** MetalLB IP 대역 / Traefik LB IP | `PLACEHOLDER-IP-RANGE` | `metallb/values.yaml` `ipAddressPool.addresses` | 노드 IP(.57/.157/.240)·공유기 DHCP 범위와 겹치지 않는 대역(예: `192.168.0.200-192.168.0.209`). 할당된 Traefik IP는 `kubectl -n traefik get svc traefik`. UTM 브리지가 맥 Wi-Fi면 L2 광고(ARP)가 맥 외부로 안 나갈 수 있다 → 맥에서 `curl http://<LB IP>`로 확인, 안 되면 NodePort 32088 경로를 계속 쓴다 |
| C10 | 기존 ingress-nginx 설치 방식·네임스페이스 | NetworkPolicy가 참조하던 `ingress-nginx` 네임스페이스 | — | `helm list -A \| grep ingress`, `kubectl get ns`. 4.4 전환 시 삭제 대상 |
| C11 | k3s 내장 traefik·servicelb 사용 여부 | 둘 다 꺼야 함 | cp1 `/etc/rancher/k3s/config.yaml` | `kubectl -n kube-system get helmchart`(`traefik`, `traefik-crd` 없어야 함), `kubectl -n kube-system get ds \| grep svclb` |
| C13 | 외부 접속 방식 (HTTPS) | LB IP 443 기준: Traefik 리다이렉트 `to: websecure`(→:443), frontend `https://api.wafer.local`, `https://grafana.wafer.local` | `traefik/values.yaml` `ports.web.http.redirections.entryPoint.to`, GitLab frontend CI 변수 `VITE_API_URL`·`VITE_GRAFANA_URL`(5.1), `kim-minji-frontend/.env` | C9의 MetalLB IP가 맥에서 접속되면 그대로. **NodePort로만 접속 가능하면** 리다이렉트를 `":32443"`, frontend URL을 `https://*.wafer.local:32443`으로 바꾼다 (backend CORS는 두 경우 모두 허용) |
| C14 | 앱 네임스페이스의 다른 Pod·외부 통신 | default-deny 대상: application, ai-serving, storage | `network-policy/templates/egress.yaml` | `kubectl get pod -n application -n ai-serving -n storage`로 frontend·backend·ai-serving·mysql·minio·backup Job 외의 Pod가 있는지, backend·ai-serving이 클러스터 밖(외부 API 등)을 호출하는지 확인. 있으면 egress 규칙 추가 |
| C15 | GitLab Runner 인터넷 접근 (Trivy) | `aquasec/trivy:0.75.0` 이미지 pull, 취약점 DB(ghcr.io) 다운로드 | 앱 레포 `.gitlab-ci.yml` `image_scan` | Runner에서 `ghcr.io`, Docker Hub 접근 가능 여부. 막혀 있으면 job이 실패(allow_failure라 배포는 진행) |
| C12 | VM vCPU 수 | dev는 cp1 1대에 CPU requests ~2.0~2.3 core + instance-manager 예약(5%)이 몰린다 → **cp1 3 vCPU 이상** 권장. full instance-manager는 노드마다 8% | UTM 설정 | `kubectl describe node` Capacity·Allocated resources |

## 2. 사전 백업

작성 예정 (항목 4). 최소한 아래는 전환 작업 전에 수행한다.

- MySQL: `mysqldump` 전체 덤프를 클러스터 밖(맥 호스트)으로 복사
- MinIO: `mc mirror`로 `wafer-images`, `wafer-model` 버킷을 클러스터 밖으로 복사
- 현재 Secret 값 확인 (SealedSecret 재암호화에 필요): `mysql-secret`, `minio-secret`, `backend-secret`
- k3s 상태: cp1의 `/var/lib/rancher/k3s/server` (현재 단일 server라 datastore가 SQLite일 가능성이 높다 — `ls /var/lib/rancher/k3s/server/db`로 확인)
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
4. 인프라 앱 (DNS → NetworkPolicy → monitoring/loki → backup)
5. 애플리케이션 (mysql/minio → backend → ai-serving → frontend)

### 4.1 ApplicationSet 전환 (항목 1)

기존 개별 Application(coredns, loki, monitoring, node-local-dns 및 클러스터에만 있는 앱 Application)을 ApplicationSet이 만드는 Application으로 바꾼다. 리소스는 지우지 않는다.

**선행 조건**: 4.5 완료, C9 커밋 완료, 4.4(ingress-nginx 제거) 완료. ApplicationSet이 traefik·ingress·network-policy를 동시에 만들기 때문에, ingress-nginx가 남아 있으면 NodePort 32088 충돌로 traefik sync가 실패하고 Ingress·NetworkPolicy만 traefik 기준으로 바뀌어 접속이 끊긴다.

1. 기존 Application 목록 확인 (C5): `kubectl -n gitops get applications`
2. 기존 Application을 **리소스는 남기고** 삭제 (finalizer 제거 후 삭제 = non-cascade)
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
7. `backup/argocd-application.yaml`(backup 앱)은 프로파일과 무관해 그대로 둔다

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
5. 맥에서 `curl -H 'Host: app.wafer.local' http://192.168.0.57:32088/` 응답 확인. LB IP를 쓰려면 `/etc/hosts`의 `*.wafer.local`을 Traefik EXTERNAL-IP로 바꾸고 포트 없이 접속 (UTM 브리지가 Wi-Fi면 L2 ARP가 맥 밖으로는 안 나갈 수 있다 — C9)
6. 기존 ingress-nginx의 `nginx.ingress.kubernetes.io/rewrite-target: /`는 경로 `/`에 대해 실질적으로 동작이 없어 제거했다. 경로 재작성이 필요한 화면이 있으면 Traefik Middleware로 추가
7. Grafana Ingress는 monitoring 차트 것만 남겼다 (ingress 차트의 `wafer-grafana-ingress`는 중복이라 제거 → ArgoCD prune으로 삭제됨)
8. (항목 3) HTTP 요청은 HTTPS로 리다이렉트된다. 인증서는 cert-manager가 `wafer-ca`로 발급한다 (`kubectl get certificate -A` Ready). 맥에 CA를 신뢰 등록(4.6)하지 않으면 브라우저 경고가 뜬다. 리다이렉트 대상 포트는 C13

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

## 5. 채워야 할 시크릿

모든 값은 `kubeseal`로 암호화해 해당 values 파일의 placeholder 자리에 커밋한다. 평문 커밋 금지.

| 시크릿 | 네임스페이스 | 위치 | 상태 |
|---|---|---|---|
| `mysql-secret` (4개 키) | storage | `kim-minji-helm/mysql/values.yaml` `sealedSecret.encryptedData` | helm#3. **현재 클러스터 값 그대로** 암호화 (kim-minji-helm README) |
| `minio-secret` (`accessKey`, `secretKey`) | storage | `kim-minji-helm/minio/values.yaml` `sealedSecret.encryptedData` | **현재 클러스터 값 그대로** 암호화, 기존 Secret annotate (kim-minji-helm README) |
| `grafana-admin` (`admin-user`, `admin-password`) | monitoring | `kim-minji-infra/monitoring/values.yaml` `grafanaAdmin.sealedSecret.encryptedData` | 새 비밀번호로 생성 (아래 5.2) |
| 그 밖의 항목 | | | 작성 예정 (항목 4, 6) |

- 세 차트 모두 값이 비어 있으면 **렌더링이 실패**한다 (평문 배포 방지). ArgoCD 앱이 `ComparisonError`로 멈추고 기존 리소스는 그대로 남는다. 4.1 전에 모두 채워 커밋한다.
- 백업 CronJob은 `mysql-secret`, `minio-secret`을 그대로 참조하므로 별도 시크릿이 없다.

### 5.1 GitLab CI 변수 (frontend 프로젝트, 시크릿 아님)

| 변수 | 값 (C13) |
|---|---|
| `VITE_API_URL` | `https://api.wafer.local` (NodePort만 가능하면 `https://api.wafer.local:32443`) |
| `VITE_GRAFANA_URL` | `https://grafana.wafer.local` (NodePort만 가능하면 `https://grafana.wafer.local:32443`) |

값을 바꾼 뒤 frontend 파이프라인을 다시 돌려야 이미지에 반영된다.

### 5.2 grafana-admin 생성

```bash
NS=monitoring; NAME=grafana-admin
read -r -s -p "새 Grafana admin 비밀번호: " PW; echo
for kv in "admin-user=admin" "admin-password=$PW"; do
  k=${kv%%=*}; v=${kv#*=}
  printf '      %s: %s\n' "$k" "$(printf '%s' "$v" | kubeseal --raw --namespace $NS --name $NAME --from-file=/dev/stdin)"
done
```

출력된 2줄을 `monitoring/values.yaml`의 `grafanaAdmin.sealedSecret.encryptedData: {}`에서 `{}`를 지우고 붙여 넣는다. Grafana는 영속 볼륨이 없어(재시작마다 초기화) 새 Secret 값이 바로 admin 비밀번호가 된다.

## 6. 배포 후 확인 체크리스트

작성 예정 (항목 2~9에서 계속 추가). 노드 1대(cp1만) / 3대 켠 상태로 나눠 확인한다.

### 6.1 dev, cp1 1대만 켠 상태

- [ ] Running/Completed가 아닌 Pod 없음 (Pending뿐 아니라 Terminating·Unknown 포함): `kubectl get pods -A | grep -vE 'Running|Completed'`. **예외(현재 알려진 것)**:
  - mysql, minio: local-path PV가 k3s-w1에 있으면 Pending (Longhorn 전환 전까지 — 항목 2/4)
- [ ] coredns replica 1 Running
- [ ] ai-serving이 cp1에 Running (ai2 선호지만 ai2가 꺼져 있으므로), OOMKilled 없음 (C2)
- [ ] Prometheus retention 2d: `kubectl -n monitoring get prometheus -o jsonpath='{.items[0].spec.retention}'`
- [ ] Traefik 1개 Running, `curl -H 'Host: app.wafer.local' http://<cp1 IP>:32088/` 200
- [ ] MetalLB: `kubectl -n traefik get svc traefik`에 EXTERNAL-IP 할당 (C9 대역)
- [ ] cert-manager: `kubectl get clusterissuer wafer-ca` Ready=True
- [ ] Longhorn: `kubectl -n longhorn-system get nodes.longhorn.io` cp1 Ready·Schedulable, 볼륨이 있으면 복제본이 cp1에 있음
- [ ] Sealed Secrets: 기존 SealedSecret(backend-secret 등) `kubectl get sealedsecret -A` 모두 Synced (키 인계 확인)
- [ ] (항목 3) `mysql-secret`, `minio-secret`, `grafana-admin` SealedSecret Synced, mysql·minio Pod 재시작 후에도 정상 (값이 기존과 같아야 함)
- [ ] (항목 3) HTTPS: `curl -I http://app.wafer.local` → 308 → `https://...`, `curl --cacert wafer-root-ca.crt https://app.wafer.local` 200. frontend Monitoring 페이지의 Grafana iframe 3개 표시
- [ ] (항목 3) Grafana: 비로그인으로 대시보드 조회 가능, Explore 메뉴 없음, 새 admin 비밀번호로 로그인
- [ ] (항목 3) NetworkPolicy: 웨이퍼 이미지 업로드 → 추론 → 결과 저장 E2E 성공 (frontend→backend→ai-serving, backend→mysql/minio, ai-serving→minio). 막힌 경로 테스트: `kubectl -n application run np-test --rm -it --image=busybox -- wget -T3 -O- http://minio-minio.storage:9000` → 실패해야 정상
- [ ] (항목 3) 백업 Job 수동 실행 성공: `kubectl -n storage create job --from=cronjob/mysql-backup mysql-backup-manual` → Completed, 로그에 "백업 완료"

### 6.2 full, 3대 모두 켠 상태

- [ ] `env=full` 라벨 적용 후 모든 Application Synced/Healthy
- [ ] coredns 2개가 서로 다른 노드: `kubectl -n kube-system get pod -l k8s-app=kube-dns -o wide`
- [ ] frontend, backend 각 2개 Running
- [ ] ai-serving이 k3s-ai2에만 배치: `kubectl -n ai-serving get pod -o wide`
- [ ] Traefik 2개가 서로 다른 노드, PDB 존재: `kubectl -n traefik get pod -o wide`, `kubectl -n traefik get pdb`
- [ ] Longhorn 볼륨 복제본 2개가 cp1·w1에 분산 (4.3 복제 수 변경 후), ai2에는 복제본 없음

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
- NetworkPolicy 롤백: `network-policy/values.yaml`의 `defaultDenyNamespaces: []`로 커밋 → default-deny·DNS 허용 정책만 제거된다 (기존 ingress 허용 규칙은 유지)
- HTTPS 리다이렉트 롤백: `traefik/values.yaml`의 `ports.web.http.redirections` 제거 커밋 (TLS 인증서는 그대로 두어도 무방)
- k3s 설정 롤백: `/etc/rancher/k3s/config.yaml`의 `disable` 항목 제거 후 `systemctl restart k3s`

작성 예정 (항목 4~9). 원칙:

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

### 8.1 요약 (항목 2 기준, 2026-10-26)

| 구분 | dev (cp1 1대) CPU | dev 메모리 | full (3대) CPU | full 메모리 |
|---|---|---|---|---|
| 레포 워크로드 requests | 1635m | 3.25Gi | 2950m | 5.81Gi |
| 레포 워크로드 (HPA 최대치) | 1885m | 3.75Gi | 4450m | 8.81Gi |
| 레포 밖·동적 생성 구성요소 (추정, C8) | ~0.4 core | ~1.5Gi | ~1.2 core | ~2.2Gi |
| k3s 프로세스·OS (추정) | — | ~1.0Gi | — | ~2.2Gi (server 1.0 + agent 0.6×2) |
| **합계 (HPA 최소)** | **~2.0 core** | **~5.8Gi** | **~4.2 core** | **~10.2Gi** |
| **합계 (HPA 최대)** | **~2.3 core** | **~6.3Gi** | **~5.7 core** | **~13.2Gi** |

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
- full: 합계 ~10.2Gi(HPA 최소) / ~13.2Gi(최대) < 22GB.
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
| monitoring | DaemonSet | monitoring-prometheus-node-exporter | 1 | 20m · 32Mi | 20m | 32Mi |
| monitoring | Deployment | monitoring-grafana | 1 | 50m · 128Mi (미설정 포함) | 50m | 128Mi |
| monitoring | Deployment | monitoring-kube-prometheus-operator | 1 | 50m · 64Mi | 50m | 64Mi |
| monitoring | Deployment | monitoring-kube-state-metrics | 1 | 20m · 64Mi | 20m | 64Mi |
| monitoring | Prometheus | monitoring-kube-prometheus-prometheus | 1 | 100m · 256Mi | 100m | 256Mi |
| mysql | StatefulSet | mysql-mysql | 1 | 200m · 512Mi | 200m | 512Mi |
| node-local-dns | DaemonSet | node-local-dns | 1 | 100m · 64Mi | 100m | 64Mi |
| sealed-secrets | Deployment | sealed-secrets-controller | 1 | 25m · 64Mi | 25m | 64Mi |
| traefik | Deployment | traefik | 1 | 50m · 64Mi | 50m | 64Mi |
| **합계** | | | | | **1635m** | **3.25Gi** |
| **HPA 최대치 기준** | | | | | **1885m** | **3.75Gi** |

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
| monitoring | DaemonSet | monitoring-prometheus-node-exporter | 3 | 20m · 32Mi | 60m | 96Mi |
| monitoring | Deployment | monitoring-grafana | 1 | 50m · 128Mi (미설정 포함) | 50m | 128Mi |
| monitoring | Deployment | monitoring-kube-prometheus-operator | 1 | 50m · 64Mi | 50m | 64Mi |
| monitoring | Deployment | monitoring-kube-state-metrics | 1 | 20m · 64Mi | 20m | 64Mi |
| monitoring | Prometheus | monitoring-kube-prometheus-prometheus | 1 | 200m · 512Mi | 200m | 512Mi |
| mysql | StatefulSet | mysql-mysql | 1 | 200m · 512Mi | 200m | 512Mi |
| node-local-dns | DaemonSet | node-local-dns | 3 | 100m · 64Mi | 300m | 192Mi |
| sealed-secrets | Deployment | sealed-secrets-controller | 1 | 25m · 64Mi | 25m | 64Mi |
| traefik | Deployment | traefik | 2 | 50m · 64Mi | 100m | 128Mi |
| **합계** | | | | | **2950m** | **5.81Gi** |
| **HPA 최대치 기준** | | | | | **4450m** | **8.81Gi** |

> 이후 항목(Alertmanager, KEDA, Tempo/OTel 등)이 추가될 때마다 이 표를 갱신한다.
