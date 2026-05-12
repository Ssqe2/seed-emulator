#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/env_seedemu.sh"
source "${SCRIPT_DIR}/seed_k8s_cluster_inventory.sh"
seed_load_cluster_inventory
# seed-refactor: CLI args parser injected
# All user-configurable values arrive via explicit CLI long-options now.
# Infrastructure values (master IP, ssh key, registry endpoint, ...) still
# come from `seed_k8s_cluster_inventory.sh` exports — that's a transitional
# carve-out documented in memory/no_env_var_principle.md.

NAMESPACE="seedemu-k3s"
EXPERIMENT_PROFILE=""
CNI_TYPE="macvlan"
CNI_MASTER_INTERFACE_FORCE="false"
SCHEDULING_STRATEGY=""
PLACEMENT_MODE="by_as_hard"
MIN_NODES_USED="2"
REQUIRE_ALL_NODES="false"
BUILD_PARALLELISM="1"
DOCKER_BUILDKIT="0"
REGISTRY_PUSH_RETRIES="5"
REGISTRY_PUSH_BACKOFF_SECONDS="5"
DOCKER_MAX_CONCURRENT_UPLOADS="1"
REGISTRY_PUSH_TIMEOUT_SECONDS="180"
PRELOAD_FALLBACK_MODE="registry"
IMAGE_DISTRIBUTION_MODE="preload"
IMAGE_PULL_POLICY=""
KUBECTL_EXEC_TIMEOUT_SECONDS="20"
BGP_HEALTH_PARALLELISM="8"
HOSTS_PER_AS="2"
CONNECTIVITY_RETRY="24"
CONNECTIVITY_RETRY_INTERVAL_SECONDS="5"
BGP_WAIT_TIMEOUT_SECONDS="300"
DEPLOY_WAIT_TIMEOUT="1800s"
CLEAN_NAMESPACE="true"
AUTO_CNI_FALLBACK="false"
PROFILE_KIND="baseline"
PROFILE_SUPPORT_TIER="tier1"
PROFILE_ACCEPTANCE_LEVEL="runtime_strict"
PROFILE_CAPACITY_GATE="none"
AGENT_PROACTIVE_MODE="guided"
BGP_STARTUP_MODE="phased"
IBGP_REFLECTION_MODE="simple"
ROUTING_KERNEL_EXPORT_MODE="default"
OSPF_TIMING_PROFILE="default"
REAL_TOPOLOGY_DIR="${HOME}/lxl_topology/autocoder_test"
TOPOLOGY_SIZE="214"
TOPOLOGY_FILE=""
ASSIGNMENT_FILE=""
NODE_LABELS_JSON=""
NODE_POD_RESERVE="5"
COLOCATE_IX_PEERS=""
EXCLUDED_NAMESPACES=""
CURRENT_PODS_JSON_PATH=""
K8S_RUNTIME_EXPORT_BGP_TO_KERNEL="true"
FAILURE_ACTION_MAP="${REPO_ROOT}/configs/seed_failure_action_map.yaml"
PHASE_START_DRIVER=""
RUN_ID=""
RUNNER_LOG="true"
ARTIFACT_DIR=""
OUTPUT_DIR=""
SSH_CONNECT_TIMEOUT_SECONDS="10"
SSH_PROBE_TIMEOUT_SECONDS="20"
SSH_LONG_PROBE_TIMEOUT_SECONDS="90"
ANSIBLE_TIMEOUT="1800s"
DOCKER_IO_MIRROR_ENDPOINT="https://docker.m.daocloud.io"
K3S_FORCE_REINSTALL="false"
K3S_VERSION="v1.28.5+k3s1"
K3S_INSTALL_VERSION=""
K3S_ARTIFACT_URL="https://rancher-mirror.rancher.cn/k3s"
K3S_NODE_CIDR_MASK_SIZE_IPV4="24"
K3S_MAX_PODS="110"
K3S_SERVER_URL_OVERRIDE=""

# Positional: first arg is the action verb (all|preflight|compile|...|clean).
shift || true

# CLI long-options. All optional; unset falls back to the defaults above.
while [ $# -gt 0 ]; do
  case "$1" in
    --namespace)                       NAMESPACE="${2:?}"; shift 2 ;;
    --cni-type)                        CNI_TYPE="${2:?}"; shift 2 ;;
    --cni-master-interface-force)      CNI_MASTER_INTERFACE_FORCE="${2:?}"; shift 2 ;;
    --scheduling-strategy)             SCHEDULING_STRATEGY="${2:?}"; shift 2 ;;
    --placement-mode)                  PLACEMENT_MODE="${2:?}"; shift 2 ;;
    --min-nodes-used)                  MIN_NODES_USED="${2:?}"; shift 2 ;;
    --require-all-nodes)               REQUIRE_ALL_NODES="${2:?}"; shift 2 ;;
    --build-parallelism)               BUILD_PARALLELISM="${2:?}"; shift 2 ;;
    --docker-buildkit)                 DOCKER_BUILDKIT="${2:?}"; shift 2 ;;
    --registry-push-retries)           REGISTRY_PUSH_RETRIES="${2:?}"; shift 2 ;;
    --registry-push-backoff)           REGISTRY_PUSH_BACKOFF_SECONDS="${2:?}"; shift 2 ;;
    --registry-push-timeout)           REGISTRY_PUSH_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --docker-max-concurrent-uploads)   DOCKER_MAX_CONCURRENT_UPLOADS="${2:?}"; shift 2 ;;
    --preload-fallback-mode)           PRELOAD_FALLBACK_MODE="${2:?}"; shift 2 ;;
    --image-distribution-mode)         IMAGE_DISTRIBUTION_MODE="${2:?}"; shift 2 ;;
    --image-pull-policy)               IMAGE_PULL_POLICY="${2:?}"; shift 2 ;;
    --kubectl-exec-timeout)            KUBECTL_EXEC_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --bgp-health-parallelism)          BGP_HEALTH_PARALLELISM="${2:?}"; shift 2 ;;
    --hosts-per-as)                    HOSTS_PER_AS="${2:?}"; shift 2 ;;
    --bgp-startup-mode)                BGP_STARTUP_MODE="${2:?}"; shift 2 ;;
    --ibgp-reflection-mode)            IBGP_REFLECTION_MODE="${2:?}"; shift 2 ;;
    --routing-kernel-export-mode)      ROUTING_KERNEL_EXPORT_MODE="${2:?}"; shift 2 ;;
    --ospf-timing-profile)             OSPF_TIMING_PROFILE="${2:?}"; shift 2 ;;
    --topology-size)                   TOPOLOGY_SIZE="${2:?}"; shift 2 ;;
    --topology-file)                   TOPOLOGY_FILE="${2:?}"; shift 2 ;;
    --topology-dir)                    REAL_TOPOLOGY_DIR="${2:?}"; shift 2 ;;
    --assignment-file)                 ASSIGNMENT_FILE="${2:?}"; shift 2 ;;
    --node-labels-json)                NODE_LABELS_JSON="${2:?}"; shift 2 ;;
    --node-pod-reserve)                NODE_POD_RESERVE="${2:?}"; shift 2 ;;
    --colocate-ix-peers)               COLOCATE_IX_PEERS="${2:?}"; shift 2 ;;
    --excluded-namespaces)             EXCLUDED_NAMESPACES="${2:?}"; shift 2 ;;
    --auto-cni-fallback)               AUTO_CNI_FALLBACK="${2:?}"; shift 2 ;;
    --clean-namespace)                 CLEAN_NAMESPACE="${2:?}"; shift 2 ;;
    --experiment-profile)              EXPERIMENT_PROFILE="${2:?}"; shift 2 ;;
    --profile-kind)                    PROFILE_KIND="${2:?}"; shift 2 ;;
    --profile-support-tier)            PROFILE_SUPPORT_TIER="${2:?}"; shift 2 ;;
    --profile-acceptance-level)        PROFILE_ACCEPTANCE_LEVEL="${2:?}"; shift 2 ;;
    --profile-capacity-gate)           PROFILE_CAPACITY_GATE="${2:?}"; shift 2 ;;
    --agent-proactive-mode)            AGENT_PROACTIVE_MODE="${2:?}"; shift 2 ;;
    --failure-action-map)              FAILURE_ACTION_MAP="${2:?}"; shift 2 ;;
    --phase-start-driver)              PHASE_START_DRIVER="${2:?}"; shift 2 ;;
    --run-id)                          RUN_ID="${2:?}"; shift 2 ;;
    --runner-log)                      RUNNER_LOG="${2:?}"; shift 2 ;;
    --artifact-dir)                    ARTIFACT_DIR="${2:?}"; shift 2 ;;
    --output-dir)                      OUTPUT_DIR="${2:?}"; shift 2 ;;
    --ssh-connect-timeout)             SSH_CONNECT_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --ssh-probe-timeout)               SSH_PROBE_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --ssh-long-probe-timeout)          SSH_LONG_PROBE_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --ansible-timeout)                 ANSIBLE_TIMEOUT="${2:?}"; shift 2 ;;
    --docker-io-mirror-endpoint)       DOCKER_IO_MIRROR_ENDPOINT="${2:?}"; shift 2 ;;
    --k3s-force-reinstall)             K3S_FORCE_REINSTALL="${2:?}"; shift 2 ;;
    --k3s-version)                     K3S_VERSION="${2:?}"; shift 2 ;;
    --k3s-install-version)             K3S_INSTALL_VERSION="${2:?}"; shift 2 ;;
    --k3s-artifact-url)                K3S_ARTIFACT_URL="${2:?}"; shift 2 ;;
    --k3s-node-cidr-mask-size-ipv4)    K3S_NODE_CIDR_MASK_SIZE_IPV4="${2:?}"; shift 2 ;;
    --k3s-max-pods)                    K3S_MAX_PODS="${2:?}"; shift 2 ;;
    --k3s-server-url-override)         K3S_SERVER_URL_OVERRIDE="${2:?}"; shift 2 ;;
    --senior-bird-settle)              SENIOR_BIRD_SETTLE_SECONDS="${2:?}"; shift 2 ;;
    --phase-status-parallelism)        PHASE_STATUS_PARALLELISM="${2:?}"; shift 2 ;;
    --phase-protocol-parallelism)      PHASE_PROTOCOL_PARALLELISM="${2:?}"; shift 2 ;;
    --relationship-sample-limit)       RELATIONSHIP_SAMPLE_LIMIT="${2:?}"; shift 2 ;;
    --bgp-phase-timeout)               BGP_PHASE_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --bird-phase-timeout)              BIRD_PHASE_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --bird-start-exec-timeout)         BIRD_START_EXEC_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --bird-start-retries)              BIRD_START_RETRIES="${2:?}"; shift 2 ;;
    --bird-start-retry-backoff)        BIRD_START_RETRY_BACKOFF_SECONDS="${2:?}"; shift 2 ;;
    --kernel-exec-timeout)             KERNEL_EXEC_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --kernel-birdc-timeout)            KERNEL_BIRDC_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --kernel-export-mode)              KERNEL_EXPORT_MODE="${2:?}"; shift 2 ;;
    --kernel-switch-retries)           KERNEL_SWITCH_RETRIES="${2:?}"; shift 2 ;;
    --kernel-switch-retry-backoff)     KERNEL_SWITCH_RETRY_BACKOFF_SECONDS="${2:?}"; shift 2 ;;
    --kernel-scan-base)                KERNEL_SCAN_BASE_SECONDS="${2:?}"; shift 2 ;;
    --kernel-scan-jitter)              KERNEL_SCAN_JITTER_SECONDS="${2:?}"; shift 2 ;;
    --generic-deploy-timeout)          GENERIC_DEPLOY_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --showcase-port)                   SHOWCASE_PORT="${2:?}"; shift 2 ;;
    --compose-dir)                     COMPOSE_DIR="${2:?}"; shift 2 ;;
    --registry-local-endpoint)         REGISTRY_LOCAL_ENDPOINT="${2:?}"; shift 2 ;;
    --multus-shim-install-mode)        MULTUS_SHIM_INSTALL_MODE="${2:?}"; shift 2 ;;
    --acceptance-namespace-delete-timeout) ACCEPTANCE_NAMESPACE_DELETE_TIMEOUT_SECONDS="${2:?}"; shift 2 ;;
    --acceptance-image-distribution-mode) ACCEPTANCE_IMAGE_DISTRIBUTION_MODE="${2:?}"; shift 2 ;;
    --) shift; break ;;
    *) echo "[$(basename "${BASH_SOURCE[0]}")] unknown option: $1" >&2; exit 2 ;;
  esac
done

PROJECT_ROOT="${REPO_ROOT}"
INVENTORY_TEMPLATE="${PROJECT_ROOT}/ansible/inventory.yml"
PLAYBOOK_PATH="${PROJECT_ROOT}/ansible/k3s-install.yml"

SEED_K3S_MASTER_IP="${SEED_K3S_MASTER_IP:-192.168.122.110}"
SEED_K3S_WORKER1_IP="${SEED_K3S_WORKER1_IP:-192.168.122.111}"
SEED_K3S_WORKER2_IP="${SEED_K3S_WORKER2_IP:-192.168.122.112}"
SEED_K3S_MASTER_NAME="${SEED_K3S_MASTER_NAME:-seed-k3s-master}"
SEED_K3S_WORKER1_NAME="${SEED_K3S_WORKER1_NAME:-seed-k3s-worker1}"
SEED_K3S_WORKER2_NAME="${SEED_K3S_WORKER2_NAME:-seed-k3s-worker2}"
SEED_K3S_USER="${SEED_K3S_USER:-ubuntu}"
SEED_K3S_SSH_KEY="${SEED_K3S_SSH_KEY:-$HOME/.ssh/id_ed25519}"
K3S_VERSION="${K3S_VERSION:-v1.28.5+k3s1}"
SEED_REGISTRY_HOST="${SEED_REGISTRY_HOST:-${SEED_K3S_MASTER_IP}}"
SEED_REGISTRY_PORT="${SEED_REGISTRY_PORT:-5000}"
SEED_K3S_CLUSTER_NAME="${SEED_K3S_CLUSTER_NAME:-seedemu-k3s}"
SEED_CNI_MASTER_INTERFACE="${SEED_CNI_MASTER_INTERFACE:-}"
SEED_K3S_CLUSTER_CIDR="${SEED_K3S_CLUSTER_CIDR:-10.42.0.0/16}"
SEED_K3S_SERVICE_CIDR="${SEED_K3S_SERVICE_CIDR:-10.43.0.0/16}"
K3S_NODE_CIDR_MASK_SIZE_IPV4="${K3S_NODE_CIDR_MASK_SIZE_IPV4:-24}"
K3S_MAX_PODS="${K3S_MAX_PODS:-110}"
K3S_FORCE_REINSTALL="${K3S_FORCE_REINSTALL:-false}"
DOCKER_IO_MIRROR_ENDPOINT="${DOCKER_IO_MIRROR_ENDPOINT:-https://docker.m.daocloud.io}"
K3S_ARTIFACT_URL="${K3S_ARTIFACT_URL:-https://rancher-mirror.rancher.cn/k3s}"
if [ -z "${K3S_INSTALL_VERSION:-}" ]; then
    if [[ "${K3S_ARTIFACT_URL}" == *"rancher-mirror.rancher.cn/k3s"* ]]; then
        K3S_INSTALL_VERSION="${SEED_K3S_VERSION//+/-}"
    else
        K3S_INSTALL_VERSION="${K3S_VERSION}"
    fi
fi

SSH_CONNECT_TIMEOUT_SECONDS="${SSH_CONNECT_TIMEOUT_SECONDS:-10}"
ANSIBLE_TIMEOUT="${ANSIBLE_TIMEOUT:-1800s}"
SSH_OPTS=(
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o LogLevel=ERROR
    -o BatchMode=yes
    -o IdentitiesOnly=yes
    -o IdentityAgent=none
    -o ConnectTimeout="${SSH_CONNECT_TIMEOUT_SECONDS}"
    -o ServerAliveInterval=30
    -o ServerAliveCountMax=3
    -i "${SEED_K3S_SSH_KEY}"
)

OUTPUT_KUBECONFIG_DIR="${PROJECT_ROOT}/output/kubeconfigs"
OUTPUT_KUBECONFIG="${OUTPUT_KUBECONFIG_DIR}/${SEED_K3S_CLUSTER_NAME}.yaml"

echo "=============================================="
echo "SEED Emulator - K3s Multi-Node Cluster Setup"
echo "=============================================="
echo "master=${SEED_K3S_MASTER_IP} worker1=${SEED_K3S_WORKER1_IP} worker2=${SEED_K3S_WORKER2_IP}"
echo "user=${SEED_K3S_USER} k3s_version=${K3S_VERSION} install_version=${K3S_INSTALL_VERSION}"
echo "registry=${SEED_REGISTRY_HOST}:${SEED_REGISTRY_PORT}"
echo "cluster_cidr=${SEED_K3S_CLUSTER_CIDR} service_cidr=${SEED_K3S_SERVICE_CIDR}"
echo "node_cidr_mask_size_ipv4=${K3S_NODE_CIDR_MASK_SIZE_IPV4} max_pods=${K3S_MAX_PODS} force_reinstall=${K3S_FORCE_REINSTALL}"
echo ""

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

run_with_timeout() {
    local duration="$1"
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "${duration}" "$@"
    else
        "$@"
    fi
}

check_prerequisites() {
    echo "[1/7] Checking prerequisites"
    require_cmd ansible-playbook
    require_cmd ssh
    require_cmd ping
    require_cmd kubectl
    require_cmd sed

    if [ ! -f "${SEED_K3S_SSH_KEY}" ]; then
        echo "SSH key not found: ${SEED_K3S_SSH_KEY}" >&2
        exit 1
    fi
}

verify_connectivity() {
    echo "[2/7] Verifying VM connectivity"
    local host
    for host in "${SEED_K3S_MASTER_IP}" "${SEED_K3S_WORKER1_IP}" "${SEED_K3S_WORKER2_IP}"; do
        if ping -c 1 -W 2 "${host}" >/dev/null 2>&1; then
            echo "  reachable: ${host}"
        else
            echo "  unreachable: ${host}" >&2
            exit 1
        fi

        if ! run_with_timeout 12s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${host}" "echo ssh-ok" >/dev/null 2>&1; then
            echo "  ssh failed (non-interactive): ${SEED_K3S_USER}@${host}" >&2
            echo "  Hint: ensure key-based SSH works (no password prompt) and security group allows 22/tcp." >&2
            exit 1
        fi
        if ! run_with_timeout 12s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${host}" "sudo -n true" >/dev/null 2>&1; then
            echo "sudo requires password: ${SEED_K3S_USER}@${host}" >&2
            echo "This script requires passwordless sudo on remote nodes to avoid hanging." >&2
            exit 1
        fi
    done

    if [ -z "${SEED_CNI_MASTER_INTERFACE}" ]; then
        local master_iface worker1_iface worker2_iface
        master_iface="$(run_with_timeout 12s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${SEED_K3S_MASTER_IP}" "ip -o -4 route show to default | sed -n '1{s/.* dev \\([^ ]*\\).*/\\1/p}'" || true)"
        worker1_iface="$(run_with_timeout 12s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${SEED_K3S_WORKER1_IP}" "ip -o -4 route show to default | sed -n '1{s/.* dev \\([^ ]*\\).*/\\1/p}'" || true)"
        worker2_iface="$(run_with_timeout 12s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${SEED_K3S_WORKER2_IP}" "ip -o -4 route show to default | sed -n '1{s/.* dev \\([^ ]*\\).*/\\1/p}'" || true)"
        if [ -z "${master_iface}" ] || [ -z "${worker1_iface}" ] || [ -z "${worker2_iface}" ]; then
            echo "Failed to detect default-route interface on one or more nodes; set SEED_CNI_MASTER_INTERFACE manually." >&2
            exit 1
        fi
        if [ "${master_iface}" != "${worker1_iface}" ] || [ "${master_iface}" != "${worker2_iface}" ]; then
            echo "Default-route interface mismatch across nodes: master=${master_iface} worker1=${worker1_iface} worker2=${worker2_iface}" >&2
            echo "Set SEED_CNI_MASTER_INTERFACE manually and ensure it exists on all nodes." >&2
            exit 1
        fi
        SEED_CNI_MASTER_INTERFACE="${master_iface}"
    fi
    echo "  cni_master_interface=${SEED_CNI_MASTER_INTERFACE}"
}

render_inventory() {
  local output_path="$1"
  sed \
        -e "s|__SEED_K3S_MASTER_NAME__|${SEED_K3S_MASTER_NAME}|g" \
        -e "s|__SEED_K3S_WORKER1_NAME__|${SEED_K3S_WORKER1_NAME}|g" \
        -e "s|__SEED_K3S_WORKER2_NAME__|${SEED_K3S_WORKER2_NAME}|g" \
        -e "s|__SEED_K3S_USER__|${SEED_K3S_USER}|g" \
        -e "s|__SEED_K3S_SSH_KEY__|${SEED_K3S_SSH_KEY}|g" \
        -e "s|__SEED_K3S_VERSION__|${K3S_VERSION}|g" \
        -e "s|__SEED_K3S_INSTALL_VERSION__|${K3S_INSTALL_VERSION}|g" \
        -e "s|__SEED_K3S_MASTER_IP__|${SEED_K3S_MASTER_IP}|g" \
        -e "s|__SEED_K3S_WORKER1_IP__|${SEED_K3S_WORKER1_IP}|g" \
        -e "s|__SEED_K3S_WORKER2_IP__|${SEED_K3S_WORKER2_IP}|g" \
        -e "s|__SEED_REGISTRY_HOST__|${SEED_REGISTRY_HOST}|g" \
        -e "s|__SEED_REGISTRY_PORT__|${SEED_REGISTRY_PORT}|g" \
        -e "s|__SEED_DOCKER_IO_MIRROR_ENDPOINT__|${DOCKER_IO_MIRROR_ENDPOINT}|g" \
        -e "s|__SEED_K3S_ARTIFACT_URL__|${K3S_ARTIFACT_URL}|g" \
        -e "s|__SEED_CNI_MASTER_INTERFACE__|${SEED_CNI_MASTER_INTERFACE}|g" \
        -e "s|__SEED_K3S_CLUSTER_CIDR__|${SEED_K3S_CLUSTER_CIDR}|g" \
        -e "s|__SEED_K3S_SERVICE_CIDR__|${SEED_K3S_SERVICE_CIDR}|g" \
        -e "s|__SEED_K3S_NODE_CIDR_MASK_SIZE_IPV4__|${K3S_NODE_CIDR_MASK_SIZE_IPV4}|g" \
        -e "s|__SEED_K3S_MAX_PODS__|${K3S_MAX_PODS}|g" \
        -e "s|__SEED_K3S_FORCE_REINSTALL__|${K3S_FORCE_REINSTALL}|g" \
        "${INVENTORY_TEMPLATE}" > "${output_path}"
}

run_ansible() {
    echo "[3/7] Installing K3s cluster via Ansible"
    local inventory_tmp
    inventory_tmp="$(mktemp --suffix=.yml)"
    trap 'rm -f "${inventory_tmp}"' RETURN
    render_inventory "${inventory_tmp}"
    ANSIBLE_HOST_KEY_CHECKING=False \
        run_with_timeout "${ANSIBLE_TIMEOUT}" \
        ansible-playbook \
        -i "${inventory_tmp}" \
        "${PLAYBOOK_PATH}" \
        --ssh-common-args="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=${SSH_CONNECT_TIMEOUT_SECONDS} -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o BatchMode=yes -o IdentitiesOnly=yes -o IdentityAgent=none"
    trap - RETURN
    rm -f "${inventory_tmp}"
}

setup_registry() {
    echo "[4/7] Ensuring private registry on master"
    run_with_timeout 60s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${SEED_K3S_MASTER_IP}" \
        "set -euo pipefail; sudo -n docker rm -f registry >/dev/null 2>&1 || true; \
         if ! sudo -n docker image inspect registry:2 >/dev/null 2>&1; then \
           sudo -n docker pull registry:2 >/dev/null 2>&1 || (sudo -n docker pull docker.m.daocloud.io/library/registry:2 >/dev/null && sudo -n docker tag docker.m.daocloud.io/library/registry:2 registry:2 >/dev/null); \
         fi; \
         sudo -n docker run -d --network host --restart=always --name registry \
           -e REGISTRY_HTTP_ADDR=0.0.0.0:${SEED_REGISTRY_PORT} registry:2 >/dev/null"
}

fetch_kubeconfig() {
    echo "[5/7] Fetching kubeconfig"
    mkdir -p "${OUTPUT_KUBECONFIG_DIR}"
    run_with_timeout 30s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${SEED_K3S_MASTER_IP}" \
        "sudo -n cat /etc/rancher/k3s/k3s.yaml" > "${OUTPUT_KUBECONFIG}"
    sed -i "s|127.0.0.1|${SEED_K3S_MASTER_IP}|g" "${OUTPUT_KUBECONFIG}"
    echo "kubeconfig: ${OUTPUT_KUBECONFIG}"
}

verify_cluster_readiness() {
    echo "[6/7] Verifying cluster readiness"
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" wait --for=condition=Ready node --all --timeout=300s
    patch_multus_atomic_shim_install
    patch_multus_k3s_hostpaths
    patch_multus_rbac
    ensure_cni_plugins
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system rollout status daemonset/kube-multus-ds --timeout=300s
    ensure_multus_kubeconfig_bridge
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" get nodes -o wide
}

patch_multus_atomic_shim_install() {
    if ! kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system get daemonset/kube-multus-ds >/dev/null 2>&1; then
        return
    fi

    local shim_mode
    shim_mode="${MULTUS_SHIM_INSTALL_MODE:-default}"

    local cmd0
    cmd0="$(kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system get daemonset/kube-multus-ds -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="install-multus-binary")].command[0]}' 2>/dev/null || true)"
    if [ "${shim_mode}" != "atomic" ]; then
        if [ "${cmd0}" = "/install_multus" ]; then
            echo "  multus shim installer already uses default upstream launcher"
            return
        fi
        echo "  restoring multus shim installer to the default upstream launcher"
        kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system patch daemonset/kube-multus-ds --type='strategic' -p "$(
            cat <<'JSON'
{
  "spec": {
    "template": {
      "spec": {
        "initContainers": [
          {
            "name": "install-multus-binary",
            "command": ["/install_multus"],
            "args": ["--type", "thin"]
          }
        ]
      }
    }
  }
}
JSON
        )" >/dev/null || true
        return
    fi

    if [ "${cmd0}" = "/bin/sh" ] || [ "${cmd0}" = "sh" ]; then
        echo "  multus shim installer already uses atomic install"
        return
    fi

    echo "  patching multus shim installer to avoid 'Text file busy' (atomic mv)"
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system patch daemonset/kube-multus-ds --type='strategic' -p "$(
        cat <<'JSON'
{
  "spec": {
    "template": {
      "spec": {
        "initContainers": [
          {
            "name": "install-multus-binary",
            "command": ["/bin/sh", "-c"],
            "args": [
              "set -eu; src=/usr/src/multus-cni/bin/multus-shim; dst=/host/opt/cni/bin/multus-shim; tmp=/host/opt/cni/bin/.multus-shim.tmp.$$; cp \"$src\" \"$tmp\"; chmod 0755 \"$tmp\"; mv -f \"$tmp\" \"$dst\";"
            ]
          }
        ]
      }
    }
  }
}
JSON
    )" >/dev/null || true
}

patch_multus_k3s_hostpaths() {
    if ! kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system get daemonset/kube-multus-ds >/dev/null 2>&1; then
        return
    fi

    local conf_dir bin_dir
    conf_dir="$(run_with_timeout 12s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${SEED_K3S_MASTER_IP}" \
        "sudo -n sh -c 'sed -n \"s/^\\s*conf_dir\\s*=\\s*\\\"\\([^\\\"]*\\)\\\".*/\\1/p\" /var/lib/rancher/k3s/agent/etc/containerd/config.toml | head -n 1' 2>/dev/null" \
        || true)"
    bin_dir="$(run_with_timeout 12s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${SEED_K3S_MASTER_IP}" \
        "sudo -n sh -c 'if [ -d /var/lib/rancher/k3s/data/current/bin ]; then echo /var/lib/rancher/k3s/data/current/bin; else sed -n \"s/^\\s*bin_dir\\s*=\\s*\\\"\\([^\\\"]*\\)\\\".*/\\1/p\" /var/lib/rancher/k3s/agent/etc/containerd/config.toml | head -n 1; fi' 2>/dev/null" \
        || true)"

    if [ -z "${conf_dir}" ] || [ -z "${bin_dir}" ]; then
        echo "  warning: unable to detect k3s CNI conf/bin dirs; skipping multus hostpath patch" >&2
        return
    fi

    local current_cni current_cnibin
    current_cni="$(kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system get daemonset/kube-multus-ds \
        -o jsonpath='{.spec.template.spec.volumes[?(@.name=="cni")].hostPath.path}' 2>/dev/null || true)"
    current_cnibin="$(kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system get daemonset/kube-multus-ds \
        -o jsonpath='{.spec.template.spec.volumes[?(@.name=="cnibin")].hostPath.path}' 2>/dev/null || true)"

    if [ "${current_cni}" = "${conf_dir}" ] && [ "${current_cnibin}" = "${bin_dir}" ]; then
        echo "  multus hostpaths already match k3s: cni=${conf_dir} cnibin=${bin_dir}"
        return
    fi

    echo "  patching multus hostpaths for k3s: cni=${conf_dir} cnibin=${bin_dir}"
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system patch daemonset/kube-multus-ds --type='strategic' -p "$(
        cat <<JSON
{
  "spec": {
    "template": {
      "spec": {
        "volumes": [
          {"name": "cni", "hostPath": {"path": "${conf_dir}"}},
          {"name": "cnibin", "hostPath": {"path": "${bin_dir}"}}
        ]
      }
    }
  }
}
JSON
    )" >/dev/null || true
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system rollout restart daemonset/kube-multus-ds >/dev/null 2>&1 || true
}

patch_multus_rbac() {
    if ! kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system get daemonset/kube-multus-ds >/dev/null 2>&1; then
        return
    fi

    local can_list
    can_list="$(kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" auth can-i list pods -n kube-system --as system:serviceaccount:kube-system:multus 2>/dev/null || echo no)"
    if [ "${can_list}" = "yes" ]; then
        echo "  multus RBAC ok (can list pods)"
        return
    fi

    echo "  patching multus RBAC (allow list/watch pods)"
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" apply -f - <<'YAML' >/dev/null
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: multus
rules:
- apiGroups:
  - k8s.cni.cncf.io
  resources:
  - '*'
  verbs:
  - '*'
- apiGroups:
  - ""
  resources:
  - pods
  - pods/status
  verbs:
  - get
  - list
  - watch
  - update
- apiGroups:
  - ""
  - events.k8s.io
  resources:
  - events
  verbs:
  - create
  - patch
  - update
YAML
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system rollout restart daemonset/kube-multus-ds >/dev/null 2>&1 || true
}

ensure_cni_plugins() {
    local -a required_plugins
    required_plugins=(macvlan ipvlan static)

    echo "  ensuring CNI plugins: ${required_plugins[*]}"
    local host
    for host in "${SEED_K3S_MASTER_IP}" "${SEED_K3S_WORKER1_IP}" "${SEED_K3S_WORKER2_IP}"; do
        if run_with_timeout 12s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${host}" \
            "set -euo pipefail; for p in ${required_plugins[*]}; do test -x \"/opt/cni/bin/\$p\"; done" >/dev/null 2>&1; then
            echo "  ${host}: plugins already present"
            continue
        fi

        echo "  ${host}: installing containernetworking-plugins + linking /opt/cni/bin"
        run_with_timeout 240s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${host}" \
            "set -euo pipefail; \
             export DEBIAN_FRONTEND=noninteractive; \
             sudo -n apt-get update -y >/dev/null; \
             sudo -n apt-get install -y containernetworking-plugins >/dev/null; \
             sudo -n mkdir -p /opt/cni/bin; \
             for p in ${required_plugins[*]}; do \
               if [ -x \"/usr/lib/cni/\$p\" ]; then sudo -n ln -sf \"/usr/lib/cni/\$p\" \"/opt/cni/bin/\$p\"; fi; \
             done"
    done
}

ensure_multus_kubeconfig_bridge() {
    local multus_target="/var/lib/rancher/k3s/agent/etc/cni/net.d/multus.d"
    local multus_link="/etc/cni/net.d/multus.d"

    echo "  ensuring Multus kubeconfig compatibility path"
    local host
    for host in "${SEED_K3S_MASTER_IP}" "${SEED_K3S_WORKER1_IP}" "${SEED_K3S_WORKER2_IP}"; do
        run_with_timeout 90s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${host}" \
            "set -euo pipefail; \
             sudo -n mkdir -p /etc/cni/net.d; \
             if [ ! -L '${multus_link}' ] || [ \"\$(readlink -f '${multus_link}' 2>/dev/null || true)\" != '${multus_target}' ]; then \
               sudo -n rm -rf '${multus_link}'; \
               sudo -n ln -s '${multus_target}' '${multus_link}'; \
             fi; \
             for i in \$(seq 1 30); do \
               if sudo -n test -f '${multus_link}/multus.kubeconfig'; then \
                 echo '${host}: multus-kubeconfig-ok'; \
                 exit 0; \
               fi; \
               sleep 2; \
             done; \
             echo '${host}: missing ${multus_link}/multus.kubeconfig' >&2; \
             exit 1"
    done
}

validate_registry_pull_chain() {
    echo "[7/7] Validating registry pull chain"
    local check_image="${SEED_REGISTRY_HOST}:${SEED_REGISTRY_PORT}/seedemu/registry-check:latest"

    run_with_timeout 120s ssh "${SSH_OPTS[@]}" "${SEED_K3S_USER}@${SEED_K3S_MASTER_IP}" \
        "set -euo pipefail; \
         if ! sudo -n docker pull --quiet busybox:1.36 >/dev/null 2>&1; then \
           sudo -n docker pull --quiet docker.m.daocloud.io/library/busybox:1.36 >/dev/null && sudo -n docker tag docker.m.daocloud.io/library/busybox:1.36 busybox:1.36 >/dev/null; \
         fi; \
         sudo -n docker tag busybox:1.36 ${check_image} >/dev/null && sudo -n docker push ${check_image} >/dev/null"

    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system delete pod registry-self-check --ignore-not-found >/dev/null || true
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system run registry-self-check \
        --image="${check_image}" --restart=Never --command -- sh -c 'echo registry-ok' >/dev/null
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system wait --for=jsonpath='{.status.phase}'=Succeeded --timeout=180s pod/registry-self-check
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system logs pod/registry-self-check
    kubectl --kubeconfig "${OUTPUT_KUBECONFIG}" -n kube-system delete pod registry-self-check --ignore-not-found >/dev/null
}

check_prerequisites
verify_connectivity
run_ansible
setup_registry
fetch_kubeconfig
verify_cluster_readiness
validate_registry_pull_chain

echo ""
echo "K3s cluster is ready."
echo "Use kubeconfig: ${OUTPUT_KUBECONFIG}"
