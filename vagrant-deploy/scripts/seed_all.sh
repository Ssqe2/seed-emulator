#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# SEED Emulator — One-shot driver
#
# Usage:
#   seed_all.sh up        Full pipeline: VM + K3s + simulation (first time)
#   seed_all.sh vm        Only start VMs
#   seed_all.sh k3s       Only install K3s on already-running VMs
#   seed_all.sh sim       Run full simulation (auto-cleans previous namespace)
#   seed_all.sh quick     Dev loop: only compile + build + deploy (skips
#                         verify/observe/report; use after editing topology)
#   seed_all.sh clean     Delete the SEED simulation namespace, keep cluster
#   seed_all.sh down      Destroy all VMs (everything goes)
#   seed_all.sh status    Show VM + K3s status
#   seed_all.sh reset     Uninstall K3s but keep the VMs
#
# A dependency preflight (configs/deps.yaml) runs before up/vm/k3s/sim/quick.
# To check or install deps directly:  bash scripts/install_deps.sh {check|install}
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"           # vagrant-deploy/
SEED_DIR="$(cd "${REPO_ROOT}/.." && pwd)"             # seed-emulator/ (parent of vagrant-deploy)
KUBECONFIG_FILE="${REPO_ROOT}/output/kubeconfig.yaml"

log()  { echo "[seed_all] $*"; }
die()  { echo "[seed_all] ERROR: $*" >&2; exit 1; }

stage_preflight() {
  log "Preflight — dependency check + auto-ensure (configs/deps.yaml)"
  # Use 'install' mode rather than 'check': it pip-installs missing python
  # packages, then for the active provider (cluster.yaml) it auto-starts any
  # daemon that's not running (eg vagrant-vmware-utility on macOS via sudo
  # launchctl). This avoids the user needing manual `sudo launchctl ...` dance
  # mid-run. CLI tools / hypervisor apps still aren't auto-installed (they
  # need the user's chosen install method) — those still print install_hint
  # and abort.
  bash "${SCRIPT_DIR}/install_deps.sh" install \
    || die "Preflight failed; install missing items above and re-run."
}

stage_vm()    { bash "${SCRIPT_DIR}/seed_vagrant.sh"   up;       }
stage_k3s()   { bash "${SCRIPT_DIR}/seed_k3s_setup.sh" install;  }

# Resolve the namespace deploy.yaml is currently aiming at, then delete it.
# Idempotent: if it doesn't exist we don't error out.
stage_clean() {
  if [[ ! -f "${KUBECONFIG_FILE}" ]]; then
    log "No kubeconfig at ${KUBECONFIG_FILE}; nothing to clean."
    return 0
  fi
  local ns
  ns="$(python3 "${SCRIPT_DIR}/get_active_namespace.py" \
        --deploy "${REPO_ROOT}/configs/deploy.yaml" \
        --profile-yaml "${SEED_DIR}/configs/seed_k8s_profiles.yaml")"
  if [[ -z "${ns}" ]]; then
    log "No active namespace resolved from deploy.yaml; skipping clean."
    return 0
  fi
  if KUBECONFIG="${KUBECONFIG_FILE}" kubectl get ns "${ns}" &>/dev/null; then
    log "Cleaning previous run in namespace '${ns}'"
    KUBECONFIG="${KUBECONFIG_FILE}" kubectl delete ns "${ns}" --wait=true \
      || log "(delete failed; continuing anyway)"
  else
    log "Namespace '${ns}' does not exist; nothing to clean."
  fi
}

stage_sim() {
  stage_clean
  # Some upstream profiles (e.g. tier2 transit_as via opencode_seedlab_smoke.sh)
  # auto-trigger their own k3s_fetch_kubeconfig.sh fallback when starting,
  # which overwrites seed-emulator/output/kubeconfigs/<cluster>.yaml with a
  # kubeconfig whose server points at the cluster private IP (192.168.77.10:6443)
  # — unreachable from the WSL controller. Force the mirror back to our
  # forwarded-port kubeconfig (127.0.0.1:16443) right before sim starts.
  if [[ -f "${KUBECONFIG_FILE}" ]]; then
    mkdir -p "${SEED_DIR}/output/kubeconfigs"
    cp -f "${KUBECONFIG_FILE}" "${SEED_DIR}/output/kubeconfigs/seedemu-k3s.yaml"
  fi

  local profile
  profile="$(python3 -c "import yaml; print(yaml.safe_load(open('${REPO_ROOT}/configs/deploy.yaml')).get('profile',''))" 2>/dev/null || echo "")"

  # Stage list per profile. mini_internet / real_topology_rr need the phased
  # `start-bird` + `start-kernel` between deploy and verify; lighter profiles
  # bring BIRD up automatically when pods come up so we skip those two.
  local stages
  case "${profile}" in
    mini_internet|real_topology_rr|real_topology_rr_scale)
      stages="compile build deploy start-bird start-kernel verify observe report"
      ;;
    *)
      stages="compile build deploy verify observe report"
      ;;
  esac

  log "Profile '${profile}' stages: ${stages}"
  for stage in ${stages}; do
    bash "${SCRIPT_DIR}/seed_run.sh" "${stage}" || die "stage_sim: ${stage} failed"
    # PR-G: after compile produces vxlan-overlay-spec.json, set up the
    # cross-node VXLAN overlay on host (no DaemonSet, no image needed).
    # Must run before deploy so pod NADs find their `br-<key>` already up.
    if [[ "${stage}" == "compile" ]]; then
      local spec
      spec="$(ls -t "${SEED_DIR}/output/profile_runs/${profile}"/*/compiled/vxlan-overlay-spec.json 2>/dev/null | head -1 || true)"
      if [[ -n "${spec}" ]]; then
        bash "${SCRIPT_DIR}/setup_vxlan_overlay.sh" "${spec}" \
          || die "stage_sim: vxlan overlay setup failed"
      else
        log "No vxlan-overlay-spec.json under '${profile}' (cni_type != vxlan-overlay or no cross-node nets); skipping host vxlan setup."
      fi
    fi
  done
}

# Dev loop: assume images are already built (or topology code-only changes).
# Only re-compile and re-deploy. Skip build/verify/observe/report.
stage_quick() {
  stage_clean
  bash "${SCRIPT_DIR}/seed_run.sh" compile
  bash "${SCRIPT_DIR}/seed_run.sh" deploy
}

case "${1:-up}" in
  up)
    stage_preflight
    log "Stage 1/3 — bring VMs up"
    stage_vm
    log "Stage 2/3 — install K3s cluster"
    stage_k3s
    log "Stage 3/3 — run SEED simulation"
    stage_sim
    log "All stages complete."
    ;;
  vm)     stage_preflight; stage_vm    ;;
  k3s)    stage_preflight; stage_k3s   ;;
  sim)    stage_preflight; stage_sim   ;;
  quick)  stage_preflight; stage_quick ;;
  clean)  stage_clean ;;
  down)
    bash "${SCRIPT_DIR}/seed_vagrant.sh" down
    ;;
  status)
    bash "${SCRIPT_DIR}/seed_vagrant.sh"   status
    bash "${SCRIPT_DIR}/seed_k3s_setup.sh" status || true
    ;;
  reset)
    bash "${SCRIPT_DIR}/seed_k3s_setup.sh" reset
    ;;
  -h|--help|help|"")
    sed -n '4,21p' "$0"
    ;;
  *)
    die "Unknown action: $1"
    ;;
esac
