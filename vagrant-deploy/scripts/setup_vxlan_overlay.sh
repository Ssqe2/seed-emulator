#!/usr/bin/env bash
# ============================================================================
# Setup per-network VXLAN overlay on every K3s node.
#
# Reads `vxlan-overlay-spec.json` (emitted by the SEED Kubernetes compiler)
# and SSHs into each cluster node to create the corresponding `br-<key>` +
# `vxlan-<key>` interfaces directly on the host (no DaemonSet, no image,
# no in-cluster pod). Idempotent — safe to re-run.
#
# Spec format (from seedemu/compiler/Kubernetes.py:_writeVxlanOverlaySpec):
#   {"version":1, "namespace":"...", "vni_base":1000,
#    "peer_ips":["192.168.77.10",...], "networks":[{"key":"...","vni":1000},...]}
#
# Why on host instead of K8s DaemonSet?
#   - 0 image dependency (no nicolaka/netshoot or any other image to pull)
#   - 0 in-cluster CNI side-effects
#   - matches PR-E pre-step style (modprobe vxlan + sysctls in ansible)
#   - vagrant cluster has no "node restart" elasticity needs anyway —
#     destroy+up always re-runs the full ansible pipeline.
#
# Usage:
#   setup_vxlan_overlay.sh <spec.json>
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SSH_CFG="${REPO_ROOT}/output/vagrant_ssh_config"

SPEC_FILE="${1:-}"
if [[ -z "${SPEC_FILE}" ]]; then
  echo "[setup_vxlan_overlay] ERROR: spec file path required" >&2
  echo "Usage: $0 <vxlan-overlay-spec.json>" >&2
  exit 2
fi
if [[ ! -f "${SPEC_FILE}" ]]; then
  echo "[setup_vxlan_overlay] spec file not found: ${SPEC_FILE} — skipping (no vxlan-overlay networks)" >&2
  exit 0
fi
if [[ ! -f "${SSH_CFG}" ]]; then
  echo "[setup_vxlan_overlay] ERROR: vagrant ssh-config not found at ${SSH_CFG}" >&2
  echo "Run 'bash scripts/seed_vagrant.sh up' first." >&2
  exit 1
fi

# Parse spec via python (simpler than jq dependency).
read -r -d '' PARSE_SPEC <<'PYEOF' || true
import json, sys, os
with open(sys.argv[1]) as f:
    spec = json.load(f)
peer_ips = " ".join(spec.get("peer_ips") or [])
networks = spec.get("networks") or []
print(f"PEERS={peer_ips}")
print(f"NETWORK_COUNT={len(networks)}")
# Emit each network as KEY|VNI per line (newline-separated).
lines = [f"{n['key']}|{n['vni']}" for n in networks]
print("---NETWORKS---")
print("\n".join(lines))
PYEOF

OUT="$(python3 -c "${PARSE_SPEC}" "${SPEC_FILE}")"
PEERS="$(echo "${OUT}" | grep '^PEERS=' | cut -d= -f2-)"
NETWORK_COUNT="$(echo "${OUT}" | grep '^NETWORK_COUNT=' | cut -d= -f2)"
NETWORK_LINES="$(echo "${OUT}" | sed -n '/---NETWORKS---/,$p' | tail -n +2)"

echo "[setup_vxlan_overlay] spec: ${SPEC_FILE}"
echo "[setup_vxlan_overlay] peer_ips: ${PEERS}"
echo "[setup_vxlan_overlay] networks: ${NETWORK_COUNT}"

if [[ "${NETWORK_COUNT}" -eq 0 ]]; then
  echo "[setup_vxlan_overlay] no vxlan-overlay networks declared; nothing to do."
  exit 0
fi

# Build the per-node setup script.
TMP_SCRIPT="$(mktemp)"
trap 'rm -f "${TMP_SCRIPT}"' EXIT

cat > "${TMP_SCRIPT}" <<EOF
#!/bin/sh
set -eu

PEERS="${PEERS}"

# 1) Identify our own node by matching one of the peer IPs against local ifaces.
SELF_IP=""
UNDERLAY_IF=""
for IP in \$PEERS; do
    if ip -4 -o addr show | grep -qE "inet \${IP}/"; then
        SELF_IP="\$IP"
        UNDERLAY_IF=\$(ip -4 -o addr show | awk -v ip="\$IP" '\$4 ~ "^"ip"/" {print \$2; exit}')
        break
    fi
done
if [ -z "\$SELF_IP" ] || [ -z "\$UNDERLAY_IF" ]; then
    echo "[seed-vxlan] ERROR: no local iface matched any peer IP (\$PEERS)" >&2
    ip -4 -o addr show >&2 || true
    exit 1
fi
echo "[seed-vxlan] self=\$SELF_IP iface=\$UNDERLAY_IF"

# 2) Pre-cleanup: remove any stale vxlan-* ifaces from a previous SEED sim
#    (different namespace/profile uses different vxlan-<hash> names but the
#    same VNI range 1000+i, so a stale iface holding VNI N blocks our new one).
ip -br link show | awk '/^vxlan-/ {print \$1}' | while read stale; do
    ip link del "\$stale" 2>/dev/null || true
done

EOF

# Append per-network creation lines.
echo "${NETWORK_LINES}" | while IFS='|' read -r key vni; do
  [[ -z "${key}" ]] && continue
  cat >> "${TMP_SCRIPT}" <<EOF
# --- ${key} (vni=${vni}) ---
ip link show br-${key} >/dev/null 2>&1 || ip link add name br-${key} type bridge
ip link set br-${key} up
ip link show vxlan-${key} >/dev/null 2>&1 || ip link add name vxlan-${key} type vxlan id ${vni} dev "\$UNDERLAY_IF" dstport 4789 nolearning
ip link set vxlan-${key} master br-${key}
ip link set vxlan-${key} up
EOF
done

# FDB peer entries: every iface gets default broadcast/unknown-unicast pointing
# at every other peer node.
cat >> "${TMP_SCRIPT}" <<'EOF'

# --- FDB peer entries ---
for PEER in $PEERS; do
    [ "$PEER" = "$SELF_IP" ] && continue
EOF
echo "${NETWORK_LINES}" | while IFS='|' read -r key vni; do
  [[ -z "${key}" ]] && continue
  echo "    bridge fdb append 00:00:00:00:00:00 dev vxlan-${key} dst \"\$PEER\" 2>/dev/null || true" >> "${TMP_SCRIPT}"
done
echo "done" >> "${TMP_SCRIPT}"
echo 'echo "[seed-vxlan] setup complete on $SELF_IP"' >> "${TMP_SCRIPT}"

# Run the script on every node listed in vagrant ssh_config.
NODES="$(awk '/^Host / && $2 !~ /\*/ && $2 !~ /^[0-9]/ {print $2}' "${SSH_CFG}")"
echo "[setup_vxlan_overlay] target nodes: ${NODES}"
for node in ${NODES}; do
  echo "[setup_vxlan_overlay] applying on ${node}..."
  ssh -F "${SSH_CFG}" -o LogLevel=ERROR "${node}" "sudo bash -s" < "${TMP_SCRIPT}" \
    || { echo "[setup_vxlan_overlay] ERROR on ${node}" >&2; exit 1; }
done

echo "[setup_vxlan_overlay] complete."
