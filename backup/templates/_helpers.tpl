{{/* 백업 Job 공통 환경변수: MinIO(클러스터 내) 계정, 외부 S3 설정·계정 */}}
{{- define "backup.minioEnv" -}}
- name: MINIO_ENDPOINT
  value: {{ .Values.minio.endpoint | quote }}
- name: MINIO_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.minio.secretName }}
      key: accessKey
- name: MINIO_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.minio.secretName }}
      key: secretKey
- name: BACKUP_BUCKET
  value: {{ .Values.minio.backupBucket | quote }}
{{- if .Values.external.enabled }}
- name: EXT_ENDPOINT
  value: {{ .Values.external.endpoint | quote }}
- name: EXT_BUCKET
  value: {{ .Values.external.bucket | quote }}
- name: EXT_PREFIX
  value: {{ .Values.external.prefix | quote }}
- name: EXT_RETENTION_DAYS
  value: {{ .Values.external.retentionDays | quote }}
- name: EXT_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.external.secretName }}
      key: accessKey
- name: EXT_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.external.secretName }}
      key: secretKey
{{- end }}
{{- end -}}

{{/* mc 별칭을 환경변수(MC_HOST_<alias>)로 설정 — 자격 증명을 명령행에 노출하지 않는다.
     키에 / + @ : 등이 있어도 되도록 URL 인코딩한다 (AWS secret key에는 /가 흔하다) */}}
{{- define "backup.mcSetup" -}}
urlenc() { local s="$1" out="" c i; for ((i=0; i<${#s}; i++)); do c=${s:i:1}; case "$c" in [a-zA-Z0-9.~_-]) out+="$c" ;; *) printf -v c '%%%02X' "'$c"; out+="$c" ;; esac; done; printf '%s' "$out"; }
export MC_HOST_local="${MINIO_ENDPOINT%%://*}://$(urlenc "$MINIO_ACCESS_KEY"):$(urlenc "$MINIO_SECRET_KEY")@${MINIO_ENDPOINT#*://}"
if [ -n "${EXT_ENDPOINT:-}" ]; then
  export MC_HOST_ext="${EXT_ENDPOINT%%://*}://$(urlenc "$EXT_ACCESS_KEY"):$(urlenc "$EXT_SECRET_KEY")@${EXT_ENDPOINT#*://}"
fi
# 스토리지 이전(Longhorn) 등으로 MinIO가 새로 만들어졌을 때도 1차 백업 버킷이 있게 한다
mc mb --ignore-existing local/${BACKUP_BUCKET} >/dev/null
{{- end -}}

{{- define "backup.podLabels" -}}
app.kubernetes.io/name: backup
app.kubernetes.io/component: {{ . }}
{{- end -}}
