# Shared helpers for the scripts in this folder. Not meant to be run directly.
# shellcheck shell=bash
NS=crane

step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

# Call the Crane API from inside an API pod (no port-forward or tunnel needed).
# usage: api GET /api/stats      |  api POST /api/jobs '{"kind":"sleep","n":15}'
api() {
  kubectl -n "$NS" exec deploy/crane-api -c api -- python -c '
import json, sys, urllib.request
method, path = sys.argv[1], sys.argv[2]
data = sys.argv[3].encode() if len(sys.argv) > 3 else None
req = urllib.request.Request("http://localhost:8080" + path, data=data, method=method,
                             headers={"content-type": "application/json"})
print(urllib.request.urlopen(req, timeout=10).read().decode())
' "$@"
}

# Pull one field out of a JSON string, e.g.  json_get "$out" status
json_get() {
  printf '%s' "$1" | kubectl -n "$NS" exec -i deploy/crane-api -c api -- python -c \
    'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$2"
}
