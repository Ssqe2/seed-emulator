#!/usr/bin/env bash
set -euo pipefail

# Fetch the K3s master's kubeconfig over SSH and rewrite its server URL.
#
# Usage:
#   k3s_fetch_kubeconfig.sh \
#       --master-ip <IP> \
#       [--user <name>]            # default: ubuntu
#       [--ssh-key <path>]         # default: $HOME/.ssh/id_ed25519
#       [--cluster-name <name>]    # default: seedemu-k3s; controls output file name
#       [--server-url-override <url>]   # if set, sed-replaces the kubeconfig
#                                        # server URL with this exact value
#                                        # (useful in WSL/forwarded-port setups)
#
# Positional fallback (legacy callers, in order):
#   k3s_fetch_kubeconfig.sh <master_ip> <user> <ssh_key> <cluster_name> [server_url_override]
#
# Writes to ${REPO_ROOT}/output/kubeconfigs/<cluster_name>.yaml.
#
# REPO_ROOT must be set in the environment because the output path is computed
# relative to it (treated as a workspace root, not as configuration).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults (replace what used to be SEED_* env fallbacks).
master_ip=""
ssh_user="ubuntu"
ssh_key="${HOME}/.ssh/id_ed25519"
cluster_name="seedemu-k3s"
server_url_override=""

# Parse CLI args. Long-options (--master-ip etc.) plus positional fallback.
positional=()
while [ $# -gt 0 ]; do
  case "$1" in
    --master-ip)            master_ip="${2:?}"; shift 2 ;;
    --user)                 ssh_user="${2:?}"; shift 2 ;;
    --ssh-key)              ssh_key="${2:?}"; shift 2 ;;
    --cluster-name)         cluster_name="${2:?}"; shift 2 ;;
    --server-url-override)  server_url_override="${2:?}"; shift 2 ;;
    -h|--help)
      sed -n '3,18p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    --) shift; while [ $# -gt 0 ]; do positional+=("$1"); shift; done ;;
    *)  positional+=("$1"); shift ;;
  esac
done

# Positional fallback: master_ip user ssh_key cluster_name [server_url_override]
[ ${#positional[@]} -ge 1 ] && [ -z "${master_ip}"           ] && master_ip="${positional[0]}"
[ ${#positional[@]} -ge 2 ] && [ "${ssh_user}" = "ubuntu"    ] && ssh_user="${positional[1]}"
[ ${#positional[@]} -ge 3 ] && [ "${ssh_key}"  = "${HOME}/.ssh/id_ed25519" ] && ssh_key="${positional[2]}"
[ ${#positional[@]} -ge 4 ] && [ "${cluster_name}" = "seedemu-k3s" ] && cluster_name="${positional[3]}"
[ ${#positional[@]} -ge 5 ] && [ -z "${server_url_override}" ] && server_url_override="${positional[4]}"

if [ -z "${master_ip}" ]; then
  echo "ERROR: --master-ip is required (or pass as first positional arg)." >&2
  exit 2
fi
if [ -z "${REPO_ROOT:-}" ]; then
  echo "ERROR: REPO_ROOT must be set in the environment." >&2
  exit 2
fi

OUTPUT_DIR="${REPO_ROOT}/output/kubeconfigs"
OUTPUT_KUBECONFIG="${OUTPUT_DIR}/${cluster_name}.yaml"
TMP_KUBECONFIG="${OUTPUT_KUBECONFIG}.tmp"

SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o IdentityAgent=none
  -o ConnectTimeout=10
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
  -i "${ssh_key}"
)

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd ssh
require_cmd sed
require_cmd kubectl

if [ ! -f "${ssh_key}" ]; then
  echo "SSH key not found: ${ssh_key}" >&2
  exit 1
fi

mkdir -p "${OUTPUT_DIR}"

if ! ssh "${SSH_OPTS[@]}" "${ssh_user}@${master_ip}" "sudo -n true" >/dev/null 2>&1; then
  echo "Cannot run passwordless sudo on master: ${ssh_user}@${master_ip}" >&2
  exit 1
fi

ssh "${SSH_OPTS[@]}" "${ssh_user}@${master_ip}" \
  "sudo -n cat /etc/rancher/k3s/k3s.yaml" > "${TMP_KUBECONFIG}"

sed "s|127.0.0.1|${master_ip}|g" "${TMP_KUBECONFIG}" > "${OUTPUT_KUBECONFIG}"
rm -f "${TMP_KUBECONFIG}"

# Allow callers (e.g. our vagrant + WSL setup, where the controller cannot
# reach the cluster's private IP and must use a forwarded host port like
# 127.0.0.1:16443) to force the final server URL via --server-url-override.
if [ -n "${server_url_override}" ]; then
  sed -i "s|^\([[:space:]]*\)server: .*|\\1server: ${server_url_override}|" "${OUTPUT_KUBECONFIG}"
fi

kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" get nodes -o wide >/dev/null

echo "Kubeconfig fetched: ${OUTPUT_KUBECONFIG}"
