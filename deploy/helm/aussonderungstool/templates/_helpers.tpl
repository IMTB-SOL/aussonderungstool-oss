{{- define "aussonderungstool.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "aussonderungstool.fullname" -}}
{{- default .Chart.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "aussonderungstool.labels" -}}
app.kubernetes.io/name: {{ include "aussonderungstool.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "aussonderungstool.selectorLabels" -}}
app.kubernetes.io/name: {{ include "aussonderungstool.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
