#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export KUBECONFIG="$PWD/.kubeconfig"
cluster=coroot-poc

if ! kind get clusters | grep -qx "$cluster"; then
  kind create cluster --name "$cluster" --image kindest/node:v1.32.2 \
    --config kind.yaml --kubeconfig "$KUBECONFIG" --wait 180s
else
  kind export kubeconfig --name "$cluster" --kubeconfig "$KUBECONFIG"
fi

docker build -q -t coroot-poc-app:v1 apps
kind load docker-image coroot-poc-app:v1 --name "$cluster"
kubectl create namespace coroot --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace demo --dry-run=client -o yaml | kubectl apply -f -
if ! kubectl -n coroot get secret coroot-poc-api-key >/dev/null 2>&1; then
  python3 -c 'import secrets; print(secrets.token_hex(32), end="")' |
    kubectl -n coroot create secret generic coroot-poc-api-key --from-file=api-key=/dev/stdin
fi
kubectl -n coroot get secret coroot-poc-api-key -o json |
  python3 -c 'import json,sys; s=json.load(sys.stdin); print(json.dumps({"apiVersion":"v1","kind":"Secret","metadata":{"name":s["metadata"]["name"],"namespace":"demo"},"type":"Opaque","data":s["data"]}))' |
  kubectl apply -f -

helm upgrade --install coroot-operator coroot-operator \
  --repo https://coroot.github.io/helm-charts --version 0.10.6 \
  -n coroot --wait --timeout 5m
helm upgrade --install coroot coroot-ce \
  --repo https://coroot.github.io/helm-charts --version 0.3.3 \
  -n coroot -f coroot-values.yaml
kubectl -n coroot wait --for=create statefulset/coroot-coroot --timeout=180s
kubectl -n coroot rollout status statefulset/coroot-coroot --timeout=300s
kubectl -n coroot rollout status statefulset/coroot-clickhouse-shard-0 --timeout=300s
kubectl -n coroot rollout status deployment/coroot-prometheus --timeout=300s
kubectl -n coroot rollout status deployment/coroot-cluster-agent --timeout=300s
kubectl apply -f demo.yaml
kubectl -n demo rollout restart deployment/app-a deployment/app-b deployment/load-generator
kubectl -n demo rollout status deployment/redis --timeout=180s
kubectl -n demo rollout status deployment/app-b --timeout=180s
kubectl -n demo rollout status deployment/app-a --timeout=180s
kubectl -n demo rollout status deployment/load-generator --timeout=180s
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 2 \
  http://127.0.0.1:8081/checkout
echo
# In kind, rescan established sockets after the demo's connection pools are warm.
sleep 15
kubectl -n coroot rollout restart daemonset/coroot-node-agent
kubectl -n coroot rollout status daemonset/coroot-node-agent --timeout=180s
python3 verify.py
echo "Apps: http://localhost:8081/checkout"
echo "Coroot: http://localhost:8080"
kubectl -n coroot get pods
