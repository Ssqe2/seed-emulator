#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# SEED Emulator — Universal Kubernetes Deployment Script
#
# Usage: seed_deploy.sh <topology.py> [options]
#        seed_deploy.sh --cleanup [--namespace NS]
#
# Deploys SEED Emulator on any K8s/K3s cluster. Only requires:
#   - kubectl connected to a cluster
#   - Docker (for building images)
#   - Python 3 (for compiling the topology)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Defaults
NAMESPACE="seedemu"
REGISTRY_NODEPORT="30500"
REGISTRY_NAMESPACE="seedemu-registry"
OUTPUT_DIR=""
SKIP_MULTUS="false"
SKIP_REGISTRY="false"
CLEANUP="false"
TOPOLOGY_SCRIPT=""
CNI_TYPE="${SEED_CNI_TYPE:-macvlan}"
CNI_MASTER_INTERFACE="${SEED_CNI_MASTER_INTERFACE:-}"

# Detected at runtime
CLUSTER_TYPE=""
NODE_IP=""
SEED_REGISTRY=""
SEED_REGISTRY_LOCAL_ENDPOINT=""

# ============================================================================
# Utility functions
# ============================================================================

log()  { echo "[seed_deploy] $*"; }
warn() { echo "[seed_deploy] WARN: $*" >&2; }
die()  { echo "[seed_deploy] ERROR: $*" >&2; exit 1; }

_ensure_kubeconfig() {
  if ! kubectl get nodes &>/dev/null; then
    if [[ -f "$HOME/.kube/config" ]]; then
      export KUBECONFIG="$HOME/.kube/config"
    elif [[ -f "/etc/rancher/k3s/k3s.yaml" ]]; then
      export KUBECONFIG="/etc/rancher/k3s/k3s.yaml"
    fi
  fi
  kubectl get nodes &>/dev/null || die "Cannot connect to cluster. Check KUBECONFIG."
}

usage() {
  cat <<'EOF'
Usage: seed_deploy.sh <topology.py> [options]
       seed_deploy.sh --cleanup [--namespace NS]

Options:
  --output-dir DIR        Compile output directory (default: auto)
  --namespace NS          Kubernetes namespace (default: seedemu)
  --registry-port PORT    Registry NodePort (default: 30500)
  --cni-type TYPE         CNI type: bridge, macvlan, ipvlan (default: macvlan)
  --cni-iface IFACE       Host interface for macvlan/ipvlan (default: auto-detect)
  --skip-multus           Skip Multus installation
  --skip-registry         Use external registry (set SEED_REGISTRY env var)
  --cleanup               Remove deployed resources and registry
  -h, --help              Show this help
EOF
  exit 0
}

# ============================================================================
# Argument parsing
# ============================================================================

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --output-dir)    [[ $# -ge 2 ]] || die "$1 requires an argument"; OUTPUT_DIR="$2"; shift 2 ;;
      --namespace)     [[ $# -ge 2 ]] || die "$1 requires an argument"; NAMESPACE="$2"; shift 2 ;;
      --registry-port) [[ $# -ge 2 ]] || die "$1 requires an argument"; REGISTRY_NODEPORT="$2"; shift 2 ;;
      --cni-type)      [[ $# -ge 2 ]] || die "$1 requires an argument"; CNI_TYPE="$2"; shift 2 ;;
      --cni-iface)     [[ $# -ge 2 ]] || die "$1 requires an argument"; CNI_MASTER_INTERFACE="$2"; shift 2 ;;
      --skip-multus)   SKIP_MULTUS="true"; shift ;;
      --skip-registry) SKIP_REGISTRY="true"; shift ;;
      --cleanup)       CLEANUP="true"; shift ;;
      -h|--help)       usage ;;
      -*)              die "Unknown option: $1" ;;
      *)
        if [[ -z "${TOPOLOGY_SCRIPT}" ]]; then
          TOPOLOGY_SCRIPT="$1"
        else
          die "Unexpected argument: $1"
        fi
        shift
        ;;
    esac
  done

  if [[ "${CLEANUP}" == "true" ]]; then
    return 0
  fi

  if [[ -z "${TOPOLOGY_SCRIPT}" ]]; then
    die "Missing topology script. Usage: seed_deploy.sh <topology.py>"
  fi

  if [[ ! -f "${TOPOLOGY_SCRIPT}" ]]; then
    die "Topology script not found: ${TOPOLOGY_SCRIPT}"
  fi

  if [[ -z "${OUTPUT_DIR}" ]]; then
    OUTPUT_DIR="$(dirname "$(realpath "${TOPOLOGY_SCRIPT}")")/seed_output"
  fi
}

# ============================================================================
# Step 1: Preflight
# ============================================================================

preflight() {
  log "Step 1/9: Preflight check"

  if ! command -v kubectl &>/dev/null; then
    if command -v k3s &>/dev/null; then
      kubectl() { k3s kubectl "$@"; }
      export -f kubectl 2>/dev/null || true
    else
      die "kubectl not found"
    fi
  fi

  _ensure_kubeconfig
  docker info &>/dev/null || die "Docker is not running or not accessible"
  python3 --version &>/dev/null || die "python3 not found"
  command -v curl &>/dev/null || die "curl not found"

  # Detect cluster type
  local kubelet_version
  kubelet_version="$(kubectl get nodes -o jsonpath='{.items[0].status.nodeInfo.kubeletVersion}' 2>/dev/null || true)"
  if echo "${kubelet_version}" | grep -qi "k3s"; then
    CLUSTER_TYPE="k3s"
  else
    CLUSTER_TYPE="k8s"
  fi

  # Get a node IPv4 address
  local all_ips
  all_ips="$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || true)"
  for ip in ${all_ips}; do
    if [[ "${ip}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      NODE_IP="${ip}"
      break
    fi
  done
  [[ -n "${NODE_IP}" ]] || die "Cannot determine node IPv4 address"

  # Auto-detect CNI master interface and check promiscuous mode
  if [[ "${CNI_TYPE}" =~ ^(macvlan|ipvlan)$ ]]; then
    _detect_and_check_cni_interface
  fi

  local node_count
  node_count="$(kubectl get nodes --no-headers 2>/dev/null | wc -l)"

  log "  Cluster type: ${CLUSTER_TYPE}"
  log "  Nodes: ${node_count}"
  log "  Node IP: ${NODE_IP}"
  [[ -n "${CNI_MASTER_INTERFACE}" ]] && log "  CNI interface: ${CNI_MASTER_INTERFACE}"
  log "  Preflight passed"
}

_detect_and_check_cni_interface() {
  # Single pod to both detect interface and check promiscuous mode
  local pod_name="seedemu-preflight-net"
  kubectl delete pod "${pod_name}" --ignore-not-found &>/dev/null || true

  kubectl run "${pod_name}" \
    --image=busybox:1.36 \
    --restart=Never \
    --overrides='{"spec":{"hostNetwork":true,"tolerations":[{"operator":"Exists"}]}}' \
    --command -- sleep 30 &>/dev/null

  local attempts=0
  while ! kubectl get pod "${pod_name}" -o jsonpath='{.status.phase}' 2>/dev/null | grep -q Running; do
    attempts=$((attempts + 1))
    if [[ ${attempts} -ge 30 ]]; then
      kubectl delete pod "${pod_name}" --ignore-not-found &>/dev/null || true
      die "Cannot start preflight pod"
    fi
    sleep 2
  done

  # Detect default route interface if not specified
  if [[ -z "${CNI_MASTER_INTERFACE}" ]]; then
    CNI_MASTER_INTERFACE="$(kubectl exec "${pod_name}" -- sh -c "ip -o -4 route show default | awk '{print \$5}' | head -1" 2>/dev/null || true)"
    if [[ -z "${CNI_MASTER_INTERFACE}" ]]; then
      kubectl delete pod "${pod_name}" --ignore-not-found &>/dev/null || true
      die "Cannot detect host network interface. Specify with --cni-iface"
    fi
    log "  Detected interface: ${CNI_MASTER_INTERFACE}"
  fi

  # Check promiscuous mode
  local flags
  flags="$(kubectl exec "${pod_name}" -- sh -c "cat /sys/class/net/${CNI_MASTER_INTERFACE}/flags 2>/dev/null" 2>/dev/null || true)"
  if [[ -n "${flags}" ]]; then
    local int_flags=$((flags))
    if (( (int_flags & 0x100) == 0 )); then
      warn "Promiscuous mode is NOT enabled on ${CNI_MASTER_INTERFACE}"
      warn "Macvlan cross-node traffic may not work in virtualized environments"
      warn "Fix: run 'sudo ip link set ${CNI_MASTER_INTERFACE} promisc on' on all nodes"
    fi
  fi

  kubectl delete pod "${pod_name}" --ignore-not-found &>/dev/null || true
}

# ============================================================================
# Step 2: Install Multus + CNI plugins
# ============================================================================

install_multus() {
  if [[ "${SKIP_MULTUS}" == "true" ]]; then
    log "Step 2/9: Multus installation skipped (--skip-multus)"
    return 0
  fi

  log "Step 2/9: Checking Multus CNI"

  local multus_installed="false"
  if kubectl -n kube-system get ds kube-multus-ds &>/dev/null; then
    local desired ready
    desired="$(kubectl -n kube-system get ds kube-multus-ds -o jsonpath='{.status.desiredNumberScheduled}' 2>/dev/null || echo 0)"
    ready="$(kubectl -n kube-system get ds kube-multus-ds -o jsonpath='{.status.numberReady}' 2>/dev/null || echo 0)"
    if [[ "${desired}" != "0" && "${desired}" == "${ready}" ]]; then
      log "  Multus already installed and ready (${ready}/${desired})"
      multus_installed="true"
    fi
  fi

  if [[ "${multus_installed}" == "false" ]]; then
    if [[ "${CLUSTER_TYPE}" == "k3s" ]]; then
      _install_multus_k3s
    else
      _install_multus_standard
    fi
    log "  Waiting for Multus to be ready..."
    kubectl -n kube-system rollout status ds/kube-multus-ds --timeout=300s
  fi

  # Always check CNI plugins (macvlan etc.) regardless of whether Multus was just installed
  if [[ "${CLUSTER_TYPE}" == "k3s" && "${CNI_TYPE}" =~ ^(macvlan|ipvlan)$ ]]; then
    _install_cni_plugins
  fi

  log "  Multus ready"
}

_install_multus_standard() {
  log "  Installing Multus CNI (standard K8s)..."
  kubectl apply -f https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/master/deployments/multus-daemonset.yml
}

_install_multus_k3s() {
  log "  Installing Multus CNI (K3s)..."

  local manifest
  manifest="$(curl -fsSL https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni/master/deployments/multus-daemonset.yml)"

  local conf_dir="/var/lib/rancher/k3s/agent/etc/cni/net.d"
  local bin_dir="/var/lib/rancher/k3s/data/cni"

  # Patch manifest for K3s paths before applying
  manifest="$(echo "${manifest}" | sed \
    -e 's|"kubeconfig": "/etc/cni/net.d/multus.d/multus.kubeconfig"|"kubeconfig": "'"${conf_dir}"'/multus.d/multus.kubeconfig"|' \
    -e 's|path: /etc/cni/net.d$|path: '"${conf_dir}"'|' \
    -e 's|path: /opt/cni/bin$|path: '"${bin_dir}"'|' \
    -e 's|"--multus-autoconfig-dir=/host/etc/cni/net.d"|"--multus-autoconfig-dir=/host/etc/cni/net.d"\n        - "--multus-kubeconfig-file-host='"${conf_dir}"'/multus.d/multus.kubeconfig"|'
  )"

  echo "${manifest}" | kubectl apply -f -
}

_install_cni_plugins() {
  log "  Checking CNI plugins (macvlan, ipvlan, static)..."

  local ds_name="seedemu-cni-install"
  local bin_dir="/var/lib/rancher/k3s/data/cni"
  local cni_version="v1.6.2"
  local cni_url="https://github.com/containernetworking/plugins/releases/download/${cni_version}/cni-plugins-linux-amd64-${cni_version}.tgz"

  # Use a DaemonSet with alpine to download pre-compiled CNI binaries.
  # Mount the host CNI dir directly — no nsenter, no apt-get, works on any OS.
  kubectl -n kube-system delete ds "${ds_name}" --ignore-not-found &>/dev/null || true
  sleep 2

  kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ${ds_name}
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: ${ds_name}
  template:
    metadata:
      labels:
        app: ${ds_name}
    spec:
      hostNetwork: true
      tolerations:
      - operator: Exists
      initContainers:
      - name: install
        image: alpine:3.19
        securityContext:
          privileged: true
        command: ["/bin/sh", "-c"]
        args:
        - |
          # Check if macvlan already exists
          if [ -x /host-cni/macvlan ]; then
            echo "macvlan already present, skipping"
            exit 0
          fi
          echo "Downloading CNI plugins ${cni_version}..."
          wget -q -O /tmp/cni.tgz "${cni_url}" || exit 1
          mkdir -p /tmp/cni
          tar -xzf /tmp/cni.tgz -C /tmp/cni
          for p in macvlan ipvlan static; do
            if [ -f /tmp/cni/\$p ]; then
              cp /tmp/cni/\$p /host-cni/\$p
              chmod +x /host-cni/\$p
              echo "Installed \$p"
            fi
          done
        volumeMounts:
        - name: host-cni-bin
          mountPath: /host-cni
      containers:
      - name: done
        image: busybox:1.36
        command: ["sleep", "infinity"]
      volumes:
      - name: host-cni-bin
        hostPath:
          path: ${bin_dir}
EOF

  log "  Waiting for CNI plugins to install on all nodes..."
  kubectl -n kube-system rollout status ds/"${ds_name}" --timeout=180s || warn "CNI plugin install incomplete"
  kubectl -n kube-system delete ds "${ds_name}" --ignore-not-found &>/dev/null || true

  log "  CNI plugins ready"
}

# ============================================================================
# Step 3: Deploy in-cluster Registry
# ============================================================================

deploy_registry() {
  if [[ "${SKIP_REGISTRY}" == "true" ]]; then
    log "Step 3/9: Registry deployment skipped (--skip-registry)"
    [[ -n "${SEED_REGISTRY:-}" ]] || die "SEED_REGISTRY must be set when using --skip-registry"
    return 0
  fi

  log "Step 3/9: Setting up in-cluster registry"

  if kubectl -n "${REGISTRY_NAMESPACE}" get deployment seedemu-registry &>/dev/null; then
    local ready
    ready="$(kubectl -n "${REGISTRY_NAMESPACE}" get deployment seedemu-registry -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
    if [[ "${ready}" -ge 1 ]]; then
      log "  Registry already running"
      _set_registry_vars
      return 0
    fi
  fi

  log "  Deploying registry..."

  kubectl apply -f - <<EOF
apiVersion: v1
kind: Namespace
metadata:
  name: ${REGISTRY_NAMESPACE}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: seedemu-registry
  namespace: ${REGISTRY_NAMESPACE}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: seedemu-registry
  template:
    metadata:
      labels:
        app: seedemu-registry
    spec:
      containers:
      - name: registry
        image: registry:2
        ports:
        - containerPort: 5000
        env:
        - name: REGISTRY_STORAGE_DELETE_ENABLED
          value: "true"
        volumeMounts:
        - name: registry-data
          mountPath: /var/lib/registry
      volumes:
      - name: registry-data
        emptyDir: {}
---
apiVersion: v1
kind: Service
metadata:
  name: seedemu-registry
  namespace: ${REGISTRY_NAMESPACE}
spec:
  type: NodePort
  selector:
    app: seedemu-registry
  ports:
  - port: 5000
    targetPort: 5000
    nodePort: ${REGISTRY_NODEPORT}
EOF

  log "  Waiting for registry to be ready..."
  kubectl -n "${REGISTRY_NAMESPACE}" rollout status deployment/seedemu-registry --timeout=120s

  _set_registry_vars

  log "  Verifying registry at ${NODE_IP}:${REGISTRY_NODEPORT}..."
  local attempts=0
  while ! curl -fsS "http://${NODE_IP}:${REGISTRY_NODEPORT}/v2/" &>/dev/null; do
    attempts=$((attempts + 1))
    [[ ${attempts} -lt 15 ]] || die "Registry not reachable at ${NODE_IP}:${REGISTRY_NODEPORT}"
    sleep 2
  done

  if [[ "${CLUSTER_TYPE}" == "k3s" ]]; then
    _configure_k3s_registry
  fi

  _configure_docker_insecure_registry

  log "  Registry ready"
}

_set_registry_vars() {
  SEED_REGISTRY="${NODE_IP}:${REGISTRY_NODEPORT}"
  SEED_REGISTRY_LOCAL_ENDPOINT="${NODE_IP}:${REGISTRY_NODEPORT}"
}

_configure_k3s_registry() {
  log "  Configuring K3s nodes to trust registry..."

  local registry_addr="${NODE_IP}:${REGISTRY_NODEPORT}"
  local ds_name="seedemu-registry-setup"
  kubectl -n kube-system delete ds "${ds_name}" --ignore-not-found &>/dev/null || true
  sleep 2

  # DaemonSet uses nsenter to write registries.yaml and restart K3s on each node
  kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ${ds_name}
  namespace: kube-system
spec:
  selector:
    matchLabels:
      app: ${ds_name}
  template:
    metadata:
      labels:
        app: ${ds_name}
    spec:
      hostPID: true
      hostNetwork: true
      tolerations:
      - operator: Exists
      initContainers:
      - name: configure
        image: busybox:1.36
        securityContext:
          privileged: true
        command: ["/bin/sh", "-c"]
        args:
        - |
          nsenter --target 1 --mount --uts --ipc --net -- /bin/sh -c '
            mkdir -p /etc/rancher/k3s
            printf "mirrors:\n  \"${registry_addr}\":\n    endpoint:\n      - \"http://${registry_addr}\"\n" > /etc/rancher/k3s/registries.yaml
            if command -v systemctl >/dev/null 2>&1; then
              if systemctl is-active k3s >/dev/null 2>&1; then
                systemctl restart k3s
              elif systemctl is-active k3s-agent >/dev/null 2>&1; then
                systemctl restart k3s-agent
              fi
            fi
          '
      containers:
      - name: done
        image: busybox:1.36
        command: ["sleep", "infinity"]
EOF

  log "    Waiting for registry config on all nodes..."
  kubectl -n kube-system rollout status ds/"${ds_name}" --timeout=120s || warn "Registry config rollout incomplete"
  kubectl -n kube-system delete ds "${ds_name}" --ignore-not-found &>/dev/null || true

  log "  Waiting for nodes to be ready after K3s restart..."
  sleep 5
  local attempts=0
  while ! kubectl get nodes &>/dev/null; do
    attempts=$((attempts + 1))
    [[ ${attempts} -lt 30 ]] || die "Cluster not reachable after K3s restart"
    sleep 3
  done
  kubectl wait --for=condition=Ready nodes --all --timeout=120s

  # Wait for Multus to recover after K3s restart
  if kubectl -n kube-system get ds kube-multus-ds &>/dev/null; then
    log "  Waiting for Multus to recover..."
    kubectl -n kube-system rollout status ds/kube-multus-ds --timeout=120s || warn "Multus recovery slow"
  fi
}

_configure_docker_insecure_registry() {
  local registry_addr="${NODE_IP}:${REGISTRY_NODEPORT}"
  local daemon_json="/etc/docker/daemon.json"

  if [[ -f "${daemon_json}" ]] && grep -q "${registry_addr}" "${daemon_json}" 2>/dev/null; then
    return 0
  fi

  log "  Configuring Docker to trust insecure registry ${registry_addr}..."

  if [[ -f "${daemon_json}" ]]; then
    local tmp
    tmp="$(mktemp)"
    python3 -c "
import json, sys
try:
    with open('${daemon_json}') as f:
        cfg = json.load(f)
except:
    cfg = {}
regs = cfg.get('insecure-registries', [])
if '${registry_addr}' not in regs:
    regs.append('${registry_addr}')
cfg['insecure-registries'] = regs
json.dump(cfg, sys.stdout, indent=2)
" > "${tmp}"
    sudo cp "${tmp}" "${daemon_json}"
    rm -f "${tmp}"
  else
    sudo mkdir -p /etc/docker
    echo "{\"insecure-registries\": [\"${registry_addr}\"]}" | sudo tee "${daemon_json}" > /dev/null
  fi

  sudo systemctl restart docker || warn "Failed to restart Docker"
  sleep 2
}

# ============================================================================
# Step 4: Compile topology
# ============================================================================

compile_topology() {
  log "Step 4/9: Compiling topology"

  mkdir -p "${OUTPUT_DIR}"

  export SEED_REGISTRY="${SEED_REGISTRY}"
  export SEED_NAMESPACE="${NAMESPACE}"
  export SEED_CNI_TYPE="${CNI_TYPE}"
  export SEED_CNI_MASTER_INTERFACE="${CNI_MASTER_INTERFACE}"
  export SEED_IMAGE_PULL_POLICY="Always"
  export SEED_OUTPUT_DIR="${OUTPUT_DIR}"
  export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

  log "  Running: python3 ${TOPOLOGY_SCRIPT}"
  log "  Registry: ${SEED_REGISTRY}"
  log "  CNI: ${CNI_TYPE} (interface: ${CNI_MASTER_INTERFACE:-default})"

  (cd "$(dirname "${TOPOLOGY_SCRIPT}")" && python3 "$(basename "${TOPOLOGY_SCRIPT}")")

  # Verify output exists — compiler should have written to SEED_OUTPUT_DIR
  if [[ ! -f "${OUTPUT_DIR}/k8s.yaml" ]]; then
    # Fallback: search common locations
    local actual_output
    actual_output="$(cd "$(dirname "${TOPOLOGY_SCRIPT}")" && pwd)"

    for candidate in \
      "${actual_output}/output_nano_internet" \
      "${actual_output}/output_components" \
      "${actual_output}/seed_output"; do
      if [[ -f "${candidate}/k8s.yaml" ]]; then
        OUTPUT_DIR="$(cd "${candidate}" && pwd)"
        break
      fi
    done
  fi

  [[ -f "${OUTPUT_DIR}/k8s.yaml" ]] || die "Cannot find k8s.yaml in ${OUTPUT_DIR}. Check if the topology script compiled successfully."
  [[ -f "${OUTPUT_DIR}/build_images.sh" ]] || die "Cannot find build_images.sh in ${OUTPUT_DIR}"

  log "  Compiled to: ${OUTPUT_DIR}"
}

# ============================================================================
# Step 5: Build and push images
# ============================================================================

build_and_push() {
  log "Step 5/9: Building and pushing images"

  export REGISTRY_PREFIX="${SEED_REGISTRY}"
  export SEED_REGISTRY_LOCAL_ENDPOINT="${SEED_REGISTRY_LOCAL_ENDPOINT}"
  export SEED_IMAGE_DISTRIBUTION_MODE="registry"
  export DOCKER_BUILDKIT=0

  log "  Push target: ${SEED_REGISTRY_LOCAL_ENDPOINT}"

  chmod +x "${OUTPUT_DIR}/build_images.sh"
  (cd "${OUTPUT_DIR}" && ./build_images.sh)

  log "  Images built and pushed"
}

# ============================================================================
# Step 6: Deploy
# ============================================================================

deploy() {
  log "Step 6/9: Deploying to cluster"

  # Delete and recreate namespace to ensure clean state when switching topologies
  kubectl delete namespace "${NAMESPACE}" --ignore-not-found --timeout=60s 2>/dev/null || true
  kubectl create namespace "${NAMESPACE}"
  kubectl apply -f "${OUTPUT_DIR}/k8s.yaml"

  log "  Manifests applied to namespace: ${NAMESPACE}"
}

# ============================================================================
# Step 7: Wait and verify
# ============================================================================

wait_and_verify() {
  log "Step 7/9: Waiting for pods to be ready"

  kubectl -n "${NAMESPACE}" wait --for=condition=Available --timeout=600s deployment --all || true

  local total running
  total="$(kubectl -n "${NAMESPACE}" get pods --no-headers 2>/dev/null | wc -l)"
  running="$(kubectl -n "${NAMESPACE}" get pods --no-headers --field-selector=status.phase=Running 2>/dev/null | wc -l)"

  if [[ "${running}" -eq "${total}" && "${total}" -gt 0 ]]; then
    log "  All ${total} pods are Running"
  else
    warn "${running}/${total} pods Running"
    kubectl -n "${NAMESPACE}" get pods --field-selector='status.phase!=Running' 2>/dev/null || true
  fi
}

# ============================================================================
# Step 8: Start BIRD routing daemon
# ============================================================================

start_bird() {
  log "Step 8/9: Starting BIRD routing daemon"

  # Use Zhou Ziwei's existing Python script if available — it handles
  # parallel startup, PID cleanup, and convergence waiting properly.
  local bird_script="${SCRIPT_DIR}/seed_k8s_start_bird0130.py"
  if [[ -f "${bird_script}" ]]; then
    local artifact_dir="${OUTPUT_DIR}/bird_startup"
    mkdir -p "${artifact_dir}"
    log "  Using ${bird_script}"
    python3 "${bird_script}" "${NAMESPACE}" "${artifact_dir}" || warn "BIRD startup script returned non-zero"
  else
    _start_bird_simple
  fi

}

_start_bird_simple() {
  # Fallback: simple sequential BIRD startup
  local router_pods
  router_pods="$(kubectl -n "${NAMESPACE}" get pods \
    -l 'seedemu.io/role in (brd,r,rs)' \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)"

  local count=0
  for pod in ${router_pods}; do
    if kubectl -n "${NAMESPACE}" exec "${pod}" -- test -f /etc/bird/bird.conf 2>/dev/null; then
      kubectl -n "${NAMESPACE}" exec "${pod}" -- sh -c '
        if pgrep -x bird >/dev/null 2>&1; then exit 0; fi
        rm -f /run/bird/bird.ctl /run/bird/bird.pid 2>/dev/null || true
        bird -c /etc/bird/bird.conf
      ' 2>/dev/null && count=$((count + 1)) || warn "Failed to start BIRD on ${pod}"
    fi
  done

  log "  BIRD started on ${count} routers"
  log "  Waiting 20s for BGP convergence..."
  sleep 20
}

# ============================================================================
# Step 9: Enable kernel route export
# ============================================================================

start_kernel() {
  log "Step 9/9: Enabling kernel route export"

  # After BIRD starts, BGP routes are learned but not written to the Linux
  # kernel routing table. This step switches BIRD's kernel protocol to
  # "export all" so routes become effective for actual packet forwarding.
  local kernel_script="${SCRIPT_DIR}/seed_k8s_start_bird_kernel.py"
  if [[ -f "${kernel_script}" ]]; then
    local artifact_dir="${OUTPUT_DIR}/kernel_startup"
    mkdir -p "${artifact_dir}"
    log "  Using ${kernel_script}"
    python3 "${kernel_script}" "${NAMESPACE}" "${artifact_dir}" || warn "Kernel export script returned non-zero"
  else
    _start_kernel_simple
  fi

  # Connectivity test — now that routes are in the kernel
  # Find two host pods in different ASes and ping between them
  local host_pods
  host_pods="$(kubectl -n "${NAMESPACE}" get pods -l 'seedemu.io/role=h' -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.labels.seedemu\.io/asn}{"\n"}{end}' 2>/dev/null || true)"

  local src_pod="" dst_ip=""
  local first_asn="" first_pod=""
  while IFS=' ' read -r pod asn; do
    [[ -z "${pod}" ]] && continue
    if [[ -z "${first_asn}" ]]; then
      first_asn="${asn}"
      first_pod="${pod}"
    elif [[ "${asn}" != "${first_asn}" ]]; then
      # Found a host in a different AS — get its IP on net0
      src_pod="${first_pod}"
      dst_ip="$(kubectl -n "${NAMESPACE}" exec "${pod}" -- sh -c "ip -4 addr show net0 2>/dev/null | grep inet | awk '{print \$2}' | cut -d/ -f1" 2>/dev/null || true)"
      break
    fi
  done <<< "${host_pods}"

  if [[ -n "${src_pod}" && -n "${dst_ip}" ]]; then
    log "  Connectivity test: ${src_pod} -> ${dst_ip}"
    if kubectl -n "${NAMESPACE}" exec "${src_pod}" -- ping -c 3 -W 5 "${dst_ip}" 2>/dev/null; then
      log "  Connectivity test passed"
    else
      warn "Connectivity test failed. Possible causes:"
      warn "  - BGP may need more time to converge (wait 1-2 minutes and retry)"
      warn "  - If using VirtualBox/VMware: enable Promiscuous Mode (Allow All) on VM network adapters"
      warn "  - Run 'sudo ip link set <iface> promisc on' on all nodes"
      warn "  - Ensure all nodes share the same L2 network"
    fi
  else
    log "  Skipping connectivity test (not enough host pods in different ASes)"
  fi
}

_start_kernel_simple() {
  # Fallback: manually write kernel.conf and reload on each router
  local router_pods
  router_pods="$(kubectl -n "${NAMESPACE}" get pods \
    -l 'seedemu.io/role in (brd,r,rs)' \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)"

  local count=0
  for pod in ${router_pods}; do
    kubectl -n "${NAMESPACE}" exec "${pod}" -- sh -c '
      cat > /etc/bird/conf/kernel.conf <<EOF
protocol kernel {
    merge paths on;
    persist;
    scan time 6000;
    ipv4 {
        import none;
        export all;
    };
}
EOF
      birdc configure >/dev/null 2>&1
    ' 2>/dev/null && count=$((count + 1)) || true
  done

  log "  Kernel export enabled on ${count} routers"
  log "  Waiting 10s for routes to propagate..."
  sleep 10
}

# ============================================================================
# Cleanup
# ============================================================================

cleanup() {
  log "Cleaning up..."

  if kubectl get namespace "${NAMESPACE}" &>/dev/null; then
    log "  Deleting namespace: ${NAMESPACE}"
    kubectl delete namespace "${NAMESPACE}" --timeout=120s || warn "Namespace deletion timed out"
  fi

  if kubectl get namespace "${REGISTRY_NAMESPACE}" &>/dev/null; then
    log "  Deleting registry namespace: ${REGISTRY_NAMESPACE}"
    kubectl delete namespace "${REGISTRY_NAMESPACE}" --timeout=60s || warn "Registry namespace deletion timed out"
  fi

  log "Cleanup complete"
}

# ============================================================================
# Main
# ============================================================================

main() {
  parse_args "$@"

  if [[ "${CLEANUP}" == "true" ]]; then
    _ensure_kubeconfig
    cleanup
    exit 0
  fi

  preflight
  install_multus
  deploy_registry
  compile_topology
  build_and_push
  deploy
  wait_and_verify
  start_bird
  start_kernel

  echo ""
  log "========================================="
  log "  SEED Emulator deployed successfully!"
  log "  Namespace: ${NAMESPACE}"
  log "  View pods: kubectl -n ${NAMESPACE} get pods"
  log "  Cleanup:   $0 --cleanup"
  log "========================================="
}

main "$@"
