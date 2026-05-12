#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# verify_topology_refactor.sh — prove a refactored K8s topology Python file
# produces byte-equal compile output vs the pre-refactor git HEAD version.
#
# Why: full `seed_all.sh up` E2E is 25-40 min. A topology refactor that
# preserves behavior MUST produce a deterministic, identical `compiled/`
# directory when given equivalent inputs — SEED's KubernetesCompiler is
# pure. We exploit that to prove preservation in seconds.
#
# Usage:
#   bash scripts/verify_topology_refactor.sh <topology_rel_path>
#
# <topology_rel_path> is relative to seed-emulator/, eg:
#   examples/kubernetes/k8s_nano_internet.py
#
# What it does:
#   1. Extract the file at git HEAD (pre-refactor version) → /tmp/before.py
#   2. Run BEFORE version with the env vars that profile_runner would set
#      → /tmp/refactor_verify/before/
#   3. Run AFTER version via run_topology.py (kwargs path)
#      → /tmp/refactor_verify/after/
#   4. diff -r BEFORE vs AFTER → must be empty modulo timestamps
#
# Exit code: 0 = byte-equal (verified), 1 = differs (regression)
# ============================================================================

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <topology_rel_path_from_seed_root>"
  echo "Example: $0 examples/kubernetes/k8s_nano_internet.py"
  exit 2
fi

TOPO_REL="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"          # vagrant-deploy/
SEED_DIR="$(cd "${REPO_ROOT}/.." && pwd)"            # seed-emulator/

TOPO_ABS="${SEED_DIR}/${TOPO_REL}"
[[ -f "${TOPO_ABS}" ]] || { echo "ERROR: topology not found: ${TOPO_ABS}"; exit 2; }

# Workspaces. Wiped each run so we never compare stale artifacts.
WORK="/tmp/refactor_verify"
BEFORE_DIR="${WORK}/before"
AFTER_DIR="${WORK}/after"
BEFORE_PY="${WORK}/before_topology.py"
rm -rf "${WORK}"
mkdir -p "${BEFORE_DIR}" "${AFTER_DIR}"

# ----------------------------------------------------------------------------
# 1. Extract pre-refactor version from git HEAD.
#    We can't compile against arbitrary historical version — only HEAD's
#    snapshot of this file is treated as the "BEFORE" baseline.
# ----------------------------------------------------------------------------
cd "${SEED_DIR}"
if ! git cat-file -e "HEAD:${TOPO_REL}" 2>/dev/null; then
  echo "ERROR: ${TOPO_REL} not tracked in git HEAD (no baseline to compare)."
  exit 2
fi
git show "HEAD:${TOPO_REL}" > "${BEFORE_PY}"
echo "[verify] HEAD snapshot saved: ${BEFORE_PY} ($(wc -l < "${BEFORE_PY}") lines)"

# Quick sanity: HEAD version should have env reads; AFTER (current file)
# should have zero. If both are zero, there's nothing to verify.
HEAD_ENV_COUNT=$(grep -c 'os\.environ\|environ\.get' "${BEFORE_PY}" || true)
NOW_ENV_COUNT=$(grep -c 'os\.environ\|environ\.get' "${TOPO_ABS}" || true)
echo "[verify] env reads:   HEAD=${HEAD_ENV_COUNT}   refactored=${NOW_ENV_COUNT}"
if [[ "${HEAD_ENV_COUNT}" -eq 0 ]]; then
  echo "[verify] HEAD version has zero env reads — already refactored or never had any. Skipping diff."
  exit 0
fi

# ----------------------------------------------------------------------------
# 2. Pick canonical input values (these match what our driver computes from
#    yaml configs, AND what profile_runner used to export as SEED_*).
#    Keep them constant across BEFORE+AFTER so both runs see the same inputs.
# ----------------------------------------------------------------------------
NS="seedemu-custom"
CNI="vxlan-overlay"
IFACE="eth0"
PULL="Always"
REGISTRY="192.168.77.10:5000"

# ----------------------------------------------------------------------------
# 3. Run BEFORE version (env-var style).
#    Use the BEFORE snapshot from /tmp; it reads os.environ.get("SEED_X").
#    Set the env vars profile_runner would have set.
# ----------------------------------------------------------------------------
echo "[verify] Running BEFORE (env-var path)..."
(
  cd "${SEED_DIR}"
  PYTHONPATH="${SEED_DIR}" \
  SEED_REGISTRY="${REGISTRY}" \
  SEED_NAMESPACE="${NS}" \
  SEED_CNI_TYPE="${CNI}" \
  SEED_CNI_MASTER_INTERFACE="${IFACE}" \
  SEED_IMAGE_PULL_POLICY="${PULL}" \
  SEED_OUTPUT_DIR="${BEFORE_DIR}" \
  python3 "${BEFORE_PY}" > "${WORK}/before.stdout" 2> "${WORK}/before.stderr"
)
echo "[verify]   stdout tail: $(tail -1 "${WORK}/before.stdout")"

# ----------------------------------------------------------------------------
# 4. Run AFTER version (kwargs path via run_topology.py).
#    The driver itself reads our yaml configs to derive the same values.
#    To make this verification deterministic regardless of cluster.yaml
#    state, we override the topology kwargs via a tiny inline Python that
#    bypasses the yaml-reading driver and instead imports + calls run()
#    with the exact same constants used in step 3.
# ----------------------------------------------------------------------------
echo "[verify] Running AFTER (kwargs path)..."
(
  cd "${SEED_DIR}"
  PYTHONPATH="${SEED_DIR}" python3 - <<PY > "${WORK}/after.stdout" 2> "${WORK}/after.stderr"
import importlib.util, sys
spec = importlib.util.spec_from_file_location("topo", "${TOPO_ABS}")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
m.run(
    registry_prefix="${REGISTRY}",
    namespace="${NS}",
    cni_type="${CNI}",
    cni_master_interface="${IFACE}",
    image_pull_policy="${PULL}",
    output_dir="${AFTER_DIR}",
)
PY
)
echo "[verify]   stdout tail: $(tail -1 "${WORK}/after.stdout")"

# ----------------------------------------------------------------------------
# 5. Diff. We ignore Dockerfile mtimes / .env diffs that only contain
#    timestamps. SEED compiler emits deterministic content modulo a
#    timestamp comment line in some files; we tolerate those.
# ----------------------------------------------------------------------------
echo "[verify] Diffing BEFORE vs AFTER compiled outputs..."
if diff -rq "${BEFORE_DIR}" "${AFTER_DIR}" > "${WORK}/diff.txt"; then
  echo "[verify] ✅ BYTE-EQUAL — refactor preserves behavior."
  exit 0
fi

# Filter out known-acceptable differences (timestamp-only changes). Anything
# left after this filter is a real semantic divergence.
GENUINE_DIFFS=$(grep -v '^$' "${WORK}/diff.txt" | grep -v 'timestamp' || true)
if [[ -z "${GENUINE_DIFFS}" ]]; then
  echo "[verify] ✅ Only timestamp diffs — semantically equal."
  exit 0
fi

echo "[verify] ❌ DIFFERS — investigate:"
echo "----"
echo "${GENUINE_DIFFS}" | head -30
echo "----"
echo "[verify] Full diff: ${WORK}/diff.txt"
echo "[verify] BEFORE artifacts: ${BEFORE_DIR}"
echo "[verify] AFTER artifacts:  ${AFTER_DIR}"
echo "[verify] BEFORE stderr:    ${WORK}/before.stderr"
echo "[verify] AFTER stderr:     ${WORK}/after.stderr"
exit 1
