{{- define "project-fyr.fullname" -}}
{{- printf "%s-project-fyr" .Release.Name -}}
{{- end -}}

{{- define "project-fyr.watcherName" -}}
{{- printf "%s-watcher" (include "project-fyr.fullname" .) -}}
{{- end -}}

{{- define "project-fyr.analyzerName" -}}
{{- printf "%s-analyzer" (include "project-fyr.fullname" .) -}}
{{- end -}}

{{- define "project-fyr.dashboardName" -}}
{{- printf "%s-dashboard" (include "project-fyr.fullname" .) -}}
{{- end -}}

{{- define "project-fyr.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: project-fyr
{{- end -}}
