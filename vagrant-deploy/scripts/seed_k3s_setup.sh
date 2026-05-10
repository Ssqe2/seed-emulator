#!/usr/bin/env bash
set -euo pipefail

# macOS PATH bridge: see seed_all.sh comment.
if [ "$(uname)" = "Darwin" ]; then
  for _p in /opt/homebrew/bin /usr/local/bin; do
    [ -d "$_p" ] || continue
    case ":${PATH}:" in *":$_p:"*) ;; *) PATH="$_p:${PATH}" ;; esac
  done
  export PATH; unset _p
fi

# ============================================================================
# SEED Emulator — K3s Cluster Setup (Ansible-driven)
#
# Usage: seed_k3s_setup.sh <install|status|reset>
#
# Reads configs/cluster.yaml + configs/k3s.yaml + output/vagrant_ssh_config,
# generates an Ansible inventory, and runs scripts/ansible/seed_k3s.yml.
# All cluster-side actions (K3s install, Multus, CNI plugins, Docker registry,
# kubeconfig fetch, validation) live in the playbook.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"            # vagrant-deploy/
SEED_DIR="$(cd "${REPO_ROOT}/.." && pwd)"              # seed-emulator/
CLUSTER_CONFIG="${REPO_ROOT}/configs/cluster.yaml"
K3S_CONFIG="${REPO_ROOT}/configs/k3s.yaml"
SSH_CONFIG="${REPO_ROOT}/output/vagrant_ssh_config"
OUTPUT_DIR="${REPO_ROOT}/output"
INVENTORY="${OUTPUT_DIR}/ansible_inventory.yml"
KUBECONFIG_OUT="${OUTPUT_DIR}/kubeconfig.yaml"
CNI_IFACE_FILE="${OUTPUT_DIR}/cni_master_interface"
PLAYBOOK="${SCRIPT_DIR}/ansible/seed_k3s.yml"

# Where the SEED compiler expects to find its cluster description (per
# seed-emulator/scripts/seed_k8s_cluster_inventory.{sh,py}).
COMPILER_INVENTORY_DIR="${SEED_DIR}/configs/clusters"

# Where the upstream profile_runner.sh expects to find the kubeconfig
# (hardcoded in seed_k8s_profile_runner.sh:318 as
#  ${REPO_ROOT}/output/kubeconfigs/${cluster_name}.yaml relative to seed-emulator/).
# We mirror our kubeconfig.yaml into that path at the end of action_install
# so the upstream runner can find it without falling back to its own
# `k3s_fetch_kubeconfig.sh` (which expects different env vars than ours).
UPSTREAM_KUBECONFIG_DIR="${SEED_DIR}/output/kubeconfigs"
UPSTREAM_CLUSTER_NAME="seedemu-k3s"

ENV_FILE="${REPO_ROOT}/configs/env.sh"
if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck source=/dev/null
  source "${ENV_FILE}"
fi

log()  { echo "[seed_k3s] $*"; }
die()  { echo "[seed_k3s] ERROR: $*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: seed_k3s_setup.sh <action>

Actions:
  install   Generate inventory and run the Ansible playbook
  status    Show node and Pod status via kubectl
  reset     Run k3s-uninstall on every node

Configs:
  configs/cluster.yaml    Node definitions (used to build the inventory)
  configs/k3s.yaml        K3s parameters (versions, CIDRs, mirrors, ...)

Generated artifacts:
  output/ansible_inventory.yml   Ansible inventory (regenerated each run)
  output/kubeconfig.yaml         Kubeconfig with 127.0.0.1 rewritten to master IP
EOF
  exit 0
}

preflight() {
  command -v ansible-playbook &>/dev/null || die "ansible-playbook not found. Install with: sudo apt install ansible"
  command -v python3 &>/dev/null || die "python3 not found"
  python3 -c "import yaml" 2>/dev/null || die "PyYAML missing. Install with: pip3 install pyyaml"
  [[ -f "${CLUSTER_CONFIG}" ]] || die "Missing ${CLUSTER_CONFIG}"
  [[ -f "${K3S_CONFIG}" ]]     || die "Missing ${K3S_CONFIG}"
  [[ -f "${SSH_CONFIG}" ]]     || die "Missing ${SSH_CONFIG}. Run seed_vagrant.sh up first."
  [[ -f "${PLAYBOOK}" ]]       || die "Missing playbook at ${PLAYBOOK}"
}

generate_inventory() {
  log "Generating Ansible inventory"
  mkdir -p "${OUTPUT_DIR}"
  python3 "${SCRIPT_DIR}/gen_inventory.py" \
    --cluster "${CLUSTER_CONFIG}" \
    --k3s "${K3S_CONFIG}" \
    --ssh-config "${SSH_CONFIG}" \
    --output "${INVENTORY}"
}

run_playbook() {
  log "Running playbook: ${PLAYBOOK}"
  # Force a locale that ansible (and brew-installed Python) accepts. Without
  # this, macOS SSH sessions inherit a locale like 'UTF-8' or empty which
  # makes ansible-playbook abort with "could not initialize the preferred
  # locale: unsupported locale setting".
  ANSIBLE_CONFIG="${SCRIPT_DIR}/ansible/ansible.cfg" \
  LC_ALL="${LC_ALL:-en_US.UTF-8}" \
  LANG="${LANG:-en_US.UTF-8}" \
  ansible-playbook \
    -i "${INVENTORY}" \
    --extra-vars "output_kubeconfig=${KUBECONFIG_OUT}" \
    --extra-vars "output_cni_iface=${CNI_IFACE_FILE}" \
    "${PLAYBOOK}"
}

generate_compiler_inventory() {
  # Build the cluster description that seed-emulator's Phase 4+ tooling expects
  # (configs/clusters/<cluster_name>.yaml). Picks up the master's CNI interface
  # detected by the playbook so SEED_CNI_MASTER_INTERFACE is populated when the
  # compiler runs.
  local cluster_name cni_iface output_file
  cluster_name="$(python3 - "${CLUSTER_CONFIG}" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f) or {}
print(cfg.get("cluster_name", "seedemu-k3s"))
PY
)"
  cni_iface=""
  if [[ -f "${CNI_IFACE_FILE}" ]]; then
    cni_iface="$(tr -d '[:space:]' < "${CNI_IFACE_FILE}")"
  fi
  output_file="${COMPILER_INVENTORY_DIR}/${cluster_name}.yaml"

  log "Generating SEED compiler cluster inventory: ${output_file}"
  python3 "${SCRIPT_DIR}/gen_cluster_inventory.py" \
    --cluster "${CLUSTER_CONFIG}" \
    --k3s "${K3S_CONFIG}" \
    --ssh-config "${SSH_CONFIG}" \
    --cni-interface "${cni_iface}" \
    --output "${output_file}"
}

mirror_kubeconfig_for_upstream() {
  # Upstream profile_runner.sh expects KUBECONFIG at
  # seed-emulator/output/kubeconfigs/<cluster>.yaml. Mirror ours there.
  mkdir -p "${UPSTREAM_KUBECONFIG_DIR}"
  cp "${KUBECONFIG_OUT}" "${UPSTREAM_KUBECONFIG_DIR}/${UPSTREAM_CLUSTER_NAME}.yaml"
  log "Kubeconfig mirrored to ${UPSTREAM_KUBECONFIG_DIR}/${UPSTREAM_CLUSTER_NAME}.yaml"
}

action_install() {
  preflight
  generate_inventory
  run_playbook
  generate_compiler_inventory
  mirror_kubeconfig_for_upstream
  echo ""
  log "================================================="
  log "  K3s cluster setup complete!"
  log "  Kubeconfig:         ${KUBECONFIG_OUT}"
  log "  Compiler inventory: ${COMPILER_INVENTORY_DIR}/seedemu-k3s.yaml"
  log "  Use: export KUBECONFIG=${KUBECONFIG_OUT}"
  log "================================================="
}

# A master IP is needed for SSH-based status / reset commands.
master_ssh_target() {
  python3 - "$@" <<'PY'
import sys, yaml
with open(sys.argv[1]) as f:
    cfg = yaml.safe_load(f)
for n in cfg.get('nodes', []):
    if n.get('role') == 'master':
        print(n['name'])
        break
PY
}

action_status() {
  preflight
  generate_inventory
  ANSIBLE_CONFIG="${SCRIPT_DIR}/ansible/ansible.cfg" \
  ansible master -i "${INVENTORY}" -b -a "k3s kubectl get nodes -o wide" 2>/dev/null || \
    die "Cannot reach master via Ansible. Is the cluster up?"
  echo ""
  ANSIBLE_CONFIG="${SCRIPT_DIR}/ansible/ansible.cfg" \
  ansible master -i "${INVENTORY}" -b -a "k3s kubectl -n kube-system get pods" 2>/dev/null || true
}

action_reset() {
  preflight
  generate_inventory
  log "Uninstalling K3s on master and workers"
  ANSIBLE_CONFIG="${SCRIPT_DIR}/ansible/ansible.cfg" \
  ansible master  -i "${INVENTORY}" -b -a "/usr/local/bin/k3s-uninstall.sh"       || true
  ANSIBLE_CONFIG="${SCRIPT_DIR}/ansible/ansible.cfg" \
  ansible workers -i "${INVENTORY}" -b -a "/usr/local/bin/k3s-agent-uninstall.sh" || true
  log "K3s removed from all nodes"
}

main() {
  case "${1:-}" in
    install) action_install ;;
    status)  action_status ;;
    reset)   action_reset ;;
    -h|--help|help|"") usage ;;
    *) die "Unknown action: ${1}" ;;
  esac
}

main "$@"
