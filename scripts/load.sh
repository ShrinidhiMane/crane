#!/usr/bin/env bash
# Send a burst of CPU-heavy jobs and watch the HorizontalPodAutoscaler add
# workers, then (after a quiet minute) remove them again.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/lib.sh
source scripts/lib.sh
COUNT=${1:-300}

step "Queue $COUNT heavy jobs"
api POST /api/jobs "{\"kind\":\"primes\",\"n\":3000000,\"count\":$COUNT}" >/dev/null
echo "Queued. Open the dashboard to watch new worker bars appear."

step "Watch the autoscaler (every 15s for 4 minutes, Ctrl+C to stop early)"
for _ in $(seq 1 16); do
  stats=$(api GET /api/stats)
  printf '%s  workers=%s  queued=%s  done=%s  |  ' "$(date +%H:%M:%S)" \
    "$(kubectl -n "$NS" get deploy crane-worker -o jsonpath='{.status.readyReplicas}')" \
    "$(json_get "$stats" queued)" "$(json_get "$stats" "done")"
  kubectl -n "$NS" get hpa crane-worker -o \
    jsonpath='{"worker cpu: "}{.status.currentMetrics[0].resource.current.averageUtilization}{"% of request (target 70%)  desired workers: "}{.status.desiredReplicas}{"\n"}'
  sleep 15
done

step "Scale events"
kubectl -n "$NS" describe hpa crane-worker | sed -n '/Events:/,$p'
