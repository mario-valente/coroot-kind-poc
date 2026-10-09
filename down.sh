#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
kind delete cluster --name coroot-poc --kubeconfig "$PWD/.kubeconfig"
