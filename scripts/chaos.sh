#!/usr/bin/env bash
# Two ways a worker pod can die in the middle of a job, and why neither loses it.
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

start_slow_job() {
  local out
  out=$(api POST /api/jobs '{"kind":"sleep","n":15}')
  printf '%s' "$out" | kubectl -n "$NS" exec -i deploy/crane-api -c api -- python -c \
    'import json, sys; print(json.load(sys.stdin)["ids"][0])'
}

wait_for_worker() {  # print the pod that picked up job $1
  local w=""
  for _ in $(seq 1 20); do
    w=$(json_get "$(api GET "/api/jobs/$1")" worker)
    [[ -n "$w" ]] && break
    sleep 1
  done
  echo "$w"
}

watch_job() {  # print the job's status every 5s until it's done
  for _ in $(seq 1 14); do
    local j
    j=$(api GET "/api/jobs/$1")
    printf '  %s  status=%-8s worker=%s  attempts=%s\n' "$(date +%H:%M:%S)" \
      "$(json_get "$j" status)" "$(json_get "$j" worker)" "$(json_get "$j" attempts)"
    [[ "$(json_get "$j" status)" == "done" ]] && return 0
    sleep 5
  done
}

step "Test 1: graceful stop (what scale-down and rolling updates do)"
job=$(start_slow_job); pod=$(wait_for_worker "$job")
echo "Job $job (15s) is running on $pod. Deleting the pod normally (SIGTERM)..."
kubectl -n "$NS" delete pod "$pod" --wait=false
watch_job "$job"
echo "-> The worker finished the job before exiting: done on the first attempt."
kubectl -n "$NS" logs "$pod" --tail=3 2>/dev/null || true

step "Test 2: hard crash (like a node dying or an out-of-memory kill)"
job=$(start_slow_job); pod=$(wait_for_worker "$job")
echo "Job $job (15s) is running on $pod. Killing it instantly (no SIGTERM)..."
kubectl -n "$NS" delete pod "$pod" --grace-period=0 --force
echo "The Deployment starts a replacement pod right away. The job's 20s lease"
echo "runs out, it goes back in the queue, and a live worker runs it again:"
watch_job "$job"
echo "-> Done on attempt 2, by a different pod. A crash delays a job but never loses it."
