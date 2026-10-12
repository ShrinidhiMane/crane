#!/usr/bin/env bash
# Delete everything Crane created, including the Redis volume.
# Add --cluster to delete the whole minikube cluster too.
set -euo pipefail
kubectl delete namespace crane --ignore-not-found
if [[ "${1:-}" == "--cluster" ]]; then
  minikube delete
fi
echo "Done."
