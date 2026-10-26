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
| C2 | ai-serving dev 메모리 (limit 1Gi 이하)로 모델 로딩 가능 여부 | 항목 1에서 설정 | `kim-minji-helm/ai-serving/values-dev.yaml` | dev 배포 후 OOMKilled 여부, `kubectl top pod -n ai-serving` |
| C3 | GitLab 이전 후 repo URL·호스트 IP | 작성 예정 (항목 1) | ApplicationSet `repoURL`, CoreDNS `gitlab.local` hosts | 이전한 GitLab 주소 |
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

1. k3s 설정 (내장 traefik/servicelb 사용 여부, ai2 taint `PreferNoSchedule`)
2. Sealed Secrets controller → MetalLB → cert-manager → Longhorn → ingress-nginx
3. ArgoCD + ApplicationSet (클러스터 Secret에 `env` 라벨, 기본 dev)
4. 인프라 앱 (DNS → NetworkPolicy → monitoring/loki → backup)
5. 애플리케이션 (mysql/minio → backend → ai-serving → frontend)

## 5. 채워야 할 시크릿

모든 값은 `kubeseal`로 암호화해 해당 values 파일의 placeholder 자리에 커밋한다. 평문 커밋 금지.

| 시크릿 | 네임스페이스 | 위치 | 상태 |
|---|---|---|---|
| `mysql-secret` (4개 키) | storage | `kim-minji-helm/mysql/values.yaml` `sealedSecret.encryptedData` | helm#3 |
| 그 밖의 항목 | | | 작성 예정 (항목 3, 4, 6) |

## 6. 배포 후 확인 체크리스트

작성 예정 (항목 1~9). 노드 1대(cp1만) / 3대 켠 상태로 나눠 확인한다.

## 7. 롤백

작성 예정 (항목 1~9). 원칙:

- 코드 롤백은 Git revert → ArgoCD sync
- 프로파일 전환 롤백은 클러스터 Secret의 `env` 라벨을 이전 값으로 되돌린다
- 데이터 롤백은 2장의 사전 백업에서 복원한다
