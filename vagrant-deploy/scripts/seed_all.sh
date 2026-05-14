#!/usr/bin/env bash
set -euo pipefail

# macOS: ensure brew-installed CLIs are reachable (SSH non-login sessions
# and bash sub-shells don't source /opt/homebrew/bin/brew shellenv).
if [ "$(uname)" = "Darwin" ]; then
  for _p in /opt/homebrew/bin /usr/local/bin; do
    [ -d "$_p" ] || continue
    case ":${PATH}:" in *":$_p:"*) ;; *) PATH="$_p:${PATH}" ;; esac
  done
  export PATH; unset _p
fi

# ============================================================================
# SEED Emulator — One-shot driver
#
# Usage:
#   seed_all.sh all       Full pipeline: doctor install + VM + K3s + simulation
#   seed_all.sh up        Bring VMs up + install K3s + run simulation (no doctor)
#   seed_all.sh vm        Only start VMs
#   seed_all.sh k3s       Only install K3s on already-running VMs
#   seed_all.sh sim       Run full simulation (auto-cleans previous namespace)
#   seed_all.sh quick     Dev loop: only compile + build + deploy (skips
#                         verify/observe/report; use after editing topology)
#   seed_all.sh doctor [check|install]
#                             check    : report missing deps from deps.yaml
#                             install  : auto-install pip pkgs + start daemons
#                             default = check
#   seed_all.sh clean         Delete the SEED simulation namespace, keep cluster
#   seed_all.sh showcase      (Re)start the background showcase web UI
#   seed_all.sh showcase-down Stop the background showcase web UI
#   seed_all.sh down          Destroy all VMs (everything goes)
#   seed_all.sh status        Show VM + K3s status
#   seed_all.sh reset         Uninstall K3s but keep the VMs
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"           # vagrant-deploy/
SEED_DIR="$(cd "${REPO_ROOT}/.." && pwd)"             # seed-emulator/ (parent of vagrant-deploy)
KUBECONFIG_FILE="${REPO_ROOT}/output/kubeconfig.yaml"

log()  { echo "[seed_all] $*"; }
die()  { echo "[seed_all] ERROR: $*" >&2; exit 1; }

# Dependency check / install from configs/deps.yaml.
#   check    only report missing items, exit non-zero
#   install  pip-install missing python pkgs + auto-start any provider daemon
#            (eg vagrant-vmware-utility on macOS via sudo launchctl). CLI
#            tools / hypervisor apps still aren't auto-installed (they need
#            the user's chosen install method) — those still print install_hint
#            and abort.
stage_doctor() {
  local mode="${1:-check}"
  case "${mode}" in
    check|install) ;;
    *) die "doctor mode must be 'check' or 'install' (got: ${mode})" ;;
  esac
  log "Doctor (${mode}) — dependency check (configs/deps.yaml)"
  bash "${SCRIPT_DIR}/install_deps.sh" "${mode}" \
    || die "Doctor ${mode} failed; install missing items above and re-run."
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

stage_showcase_down() {
  local pidfile="${REPO_ROOT}/output/showcase.pid"
  if [[ -f "${pidfile}" ]]; then
    local pid
    pid="$(cat "${pidfile}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      log "Stopped showcase (pid ${pid})"
    fi
    rm -f "${pidfile}"
  fi
}

stage_showcase() {
  # 读 deploy.yaml.services.showcase_port,若 > 0,后台跑
  # seed_k8s_showcase.py(controller 上跑,直连 K8s API + 读 latest
  # run artifacts),浏览器开 http://localhost:<port>/ 看实时 UI。
  local port
  port="$(python3 -c "
import yaml
cfg = yaml.safe_load(open('${REPO_ROOT}/configs/deploy.yaml')) or {}
val = (cfg.get('services') or {}).get('showcase_port', '')
text = str(val).strip()
print(text if text and text != '0' else '')
" 2>/dev/null)"

  if [[ -z "${port}" ]]; then
    return 0
  fi

  if [[ ! -f "${KUBECONFIG_FILE}" ]]; then
    return 0
  fi

  local profile
  profile="$(python3 -c "
import yaml
cfg = yaml.safe_load(open('${REPO_ROOT}/configs/deploy.yaml')) or {}
print(str(cfg.get('profile') or 'custom').strip())
" 2>/dev/null)"

  # Kill stale showcase(防同端口冲突 + 让新 sim 的 latest run 重新被 bind)
  stage_showcase_down

  local pidfile="${REPO_ROOT}/output/showcase.pid"
  local logfile="${REPO_ROOT}/output/showcase.log"
  mkdir -p "${REPO_ROOT}/output"

  log "Starting showcase web UI: localhost:${port} (profile=${profile}, run=latest)"
  KUBECONFIG="${KUBECONFIG_FILE}" nohup python3 "${SEED_DIR}/scripts/seed_k8s_showcase.py" \
    --profile "${profile}" \
    --run-id latest \
    --host 0.0.0.0 \
    --port "${port}" \
    >> "${logfile}" 2>&1 &
  echo $! > "${pidfile}"

  sleep 1
  if kill -0 "$(cat "${pidfile}")" 2>/dev/null; then
    log "  Showcase UI: http://localhost:${port}/"
    log "  Stop:        bash scripts/seed_all.sh showcase-down"
  else
    log "  showcase exited immediately; see ${logfile}"
    rm -f "${pidfile}"
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
    mini_internet|real_topology_rr|real_topology_rr_scale|custom)
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

  # 全部 stage 跑完后,如果 deploy.yaml 配了 services.showcase_port,
  # 后台启动 seed_k8s_showcase web UI(直连 K8s API + 读 latest run artifacts)。
  stage_showcase
}

# Dev loop: assume images are already built (or topology code-only changes).
# Only re-compile and re-deploy. Skip build/verify/observe/report.
stage_quick() {
  stage_clean
  bash "${SCRIPT_DIR}/seed_run.sh" compile
  bash "${SCRIPT_DIR}/seed_run.sh" deploy
}

case "${1:-up}" in
  all)
    stage_doctor install
    log "Stage 1/3 — bring VMs up"
    stage_vm
    log "Stage 2/3 — install K3s cluster"
    stage_k3s
    log "Stage 3/3 — run SEED simulation"
    stage_sim
    log "All stages complete."
    ;;
  up)
    log "Stage 1/3 — bring VMs up"
    stage_vm
    log "Stage 2/3 — install K3s cluster"
    stage_k3s
    log "Stage 3/3 — run SEED simulation"
    stage_sim
    log "All stages complete."
    ;;
  vm)     stage_vm    ;;
  k3s)    stage_k3s   ;;
  sim)    stage_sim   ;;
  quick)  stage_quick ;;
  doctor) stage_doctor "${2:-check}" ;;
  clean)  stage_clean ;;
  showcase)      stage_showcase ;;
  showcase-down) stage_showcase_down ;;
  down)
    stage_showcase_down
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
    sed -n '14,35p' "$0"
    ;;
  *)
    die "Unknown action: $1"
    ;;
esac
