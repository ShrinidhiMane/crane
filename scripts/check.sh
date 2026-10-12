#!/usr/bin/env bash
# Health check and troubleshooting tour: BusyBox test, events, logs, limits, usage.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

step "1/6 Test from inside the cluster with BusyBox"
kubectl -n "$NS" delete pod busybox-check --ignore-not-found >/dev/null
kubectl apply -f k8s/tools/busybox-check.yaml >/dev/null
kubectl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/busybox-check --timeout=120s >/dev/null
kubectl -n "$NS" logs busybox-check
kubectl -n "$NS" delete pod busybox-check >/dev/null

step "2/6 Recent events (the first place to look when a pod won't start)"
kubectl -n "$NS" get events --sort-by=.lastTimestamp | tail -n 10

step "3/6 Logs: API, a worker, and a janitor run"
kubectl -n "$NS" logs deployment/crane-api --tail=3
kubectl -n "$NS" logs deployment/crane-worker --tail=5
kubectl -n "$NS" delete job janitor-now --ignore-not-found >/dev/null
kubectl -n "$NS" create job janitor-now --from=cronjob/crane-janitor >/dev/null
kubectl -n "$NS" wait --for=condition=complete job/janitor-now --timeout=90s >/dev/null
kubectl -n "$NS" logs job/janitor-now

step "4/6 Requests and limits per pod"
kubectl -n "$NS" get pods -o custom-columns='POD:.metadata.name,CPU_REQ:.spec.containers[0].resources.requests.cpu,CPU_LIM:.spec.containers[0].resources.limits.cpu,MEM_REQ:.spec.containers[0].resources.requests.memory,MEM_LIM:.spec.containers[0].resources.limits.memory'

step "5/6 Live CPU and memory use (from metrics-server)"
kubectl -n "$NS" top pods || echo "metrics not ready yet, try again in a minute"

step "6/6 Autoscaler status"
kubectl -n "$NS" get hpa crane-worker
