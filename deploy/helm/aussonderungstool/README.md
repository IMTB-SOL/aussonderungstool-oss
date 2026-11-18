# Helm-Chart: Aussonderungstool

Deployt das Aussonderungstool auf OpenShift (Route, PVC, NetworkPolicy,
ServiceMonitor, PrometheusRule).

## Voraussetzungen

- Das Container-Image liegt in einer vom Cluster erreichbaren Registry.
- Ein Secret (Name: `existingSecret`, Standard `aussonderungstool-secrets`)
  existiert im Ziel-Namespace. Vorlage: `secret.example.yaml`.

## Anzupassende Werte

| Wert | Bedeutung |
|---|---|
| `image.repository`, `image.tag` | Image und Version |
| `imagePullSecrets` | nur bei privater Registry |
| `route.host` | Hostname der Route |
| `config.OLLAMA_BASE_URL`, `config.OLLAMA_MODEL` | LLM-Endpoint und Modell |
| `config.AUTH_MODUS` | `passwort` (Standard) \| `azure` \| `beide` — Azure benötigt `AZURE_*` im Secret |
| `config.BASE_PATH` | Subpfad, falls nicht unter `/` betrieben |
| `persistence.storageClass`, `persistence.size` | Daten-PVC (leer = Cluster-Default) |
| `networkPolicy.egress` | Port/Ziel muss zum LLM-Endpoint passen |

## Installation

```bash
helm lint deploy/helm/aussonderungstool
helm install aussonderungstool deploy/helm/aussonderungstool \
  -n <namespace> \
  --set image.repository=registry.example.com/aussonderungstool \
  --set route.host=aussonderungstool.apps.example.com
```

## Hinweise

- **Eine Replik, Strategie `Recreate`** — SQLite und das RWO-Volume erlauben
  keinen Parallelbetrieb.
- Der Container läuft non-root mit read-only Root-Dateisystem; beschreibbar
  sind nur `/app/data` (PVC) und `/tmp` (`emptyDir`).
- Die NetworkPolicy lässt Ingress nur vom Router- und Monitoring-Namespace zu,
  Egress nur für DNS und die konfigurierten LLM-Ports.
- `/metrics` ist per Bearer-Token geschützt (`METRICS_TOKEN` im Secret).
