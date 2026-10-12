#!/usr/bin/env bash
# Deploy Crane to minikube, one step at a time.
# Run from Git Bash on Windows, or any bash on macOS/Linux:  ./scripts/up.sh
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck source=scripts/lib.sh
source scripts/lib.sh

step "1/7 Turn on metrics-server (the autoscaler needs CPU numbers)"
minikube addons enable metrics-server

step "2/7 Build the image inside minikube"
minikube image build -t crane:1.0 .

step "3/7 Namespace, settings and Secret"
kubectl apply -f k8s/00-namespace.yaml -f k8s/01-config.yaml
if kubectl -n "$NS" get secret crane-redis >/dev/null 2>&1; then
  echo "secret/crane-redis already exists, keeping it"
else
  kubectl -n "$NS" create secret generic crane-redis \
    --from-literal=password="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi

step "4/7 Redis (StatefulSet + 1Gi volume)"
kubectl apply -f k8s/02-redis.yaml
kubectl -n "$NS" rollout status statefulset/redis --timeout=300s

step "5/7 API (2 replicas + LoadBalancer) and workers (+ autoscaler)"
kubectl apply -f k8s/03-api.yaml -f k8s/04-worker.yaml
kubectl -n "$NS" rollout status deployment/crane-api --timeout=300s
kubectl -n "$NS" rollout status deployment/crane-worker --timeout=300s

step "6/7 Janitor CronJob and NetworkPolicy"
kubectl apply -f k8s/05-janitor.yaml -f k8s/06-networkpolicy.yaml

step "7/7 What's running"
kubectl -n "$NS" get pods,svc,hpa,cronjob,pvc

cat <<'EOF'

Crane is up. Open the dashboard:
  minikube service crane-api -n crane          (opens your browser)
  or run `minikube tunnel` in a second terminal, then open http://127.0.0.1

Next:
  ./scripts/check.sh   test it from inside the cluster
  ./scripts/load.sh    send 300 heavy jobs and watch the autoscaler add workers
  ./scripts/chaos.sh   kill a worker mid-job and watch the job survive
EOF
