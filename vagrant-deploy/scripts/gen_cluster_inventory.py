#!/usr/bin/env python3
"""Generate the SEED compiler's cluster description (seedemu-k3s.yaml).

Reads:
  configs/cluster.yaml          -- VM layout (nodes, IPs, roles)
  configs/k3s.yaml              -- K3s parameters (CIDRs, registry, etc.)
  output/vagrant_ssh_config     -- SSH user / key path
  output/cni_master_interface   -- master's detected interface name (optional)

Writes:
  seed-emulator/configs/clusters/<cluster_name>.yaml

Output schema matches seed_k8s_cluster_inventory.py's normalize_inventory:
    cluster_name, reference_cluster, runtime, max_validated_topology_size,
    k3s.{cluster_cidr, service_cidr, node_cidr_mask_size_ipv4, max_pods},
    ssh.{user, key_path_env, default_key_path},
    registry.{host, port},
    cni.default_master_interface,
    nodes: [{name, role, management_ip, runtime, labels.kubernetes.io/hostname}]
"""
import argparse
import sys
from pathlib import Path

import yaml

# Local import: scripts/ contains both this file and normalize_cluster.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_cluster import normalize_cluster  # noqa: E402


def parse_ssh_config(path: Path) -> dict:
    """Return {host: {key: value}} from a Vagrant-style ssh-config."""
    hosts, current = {}, None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("Host "):
            current = line.split(None, 1)[1]
            hosts[current] = {}
            continue
        if current is None:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            hosts[current][parts[0]] = parts[1]
    return hosts


def detect_cni_interface_via_ssh(ssh_config: Path, master_name: str, master_ip: str) -> str:
    """Fall back to SSH detection when no precomputed interface is available."""
    import subprocess
    cmd = [
        "ssh", "-F", str(ssh_config),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-n",
        master_name,
        f"ip -o addr show | awk -v ip='{master_ip}' '$0 ~ ip\"/\" {{print $2; exit}}'",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True)
        return result.stdout.strip()
    except Exception:
        return ""


def build_cluster_inventory(
    cluster_cfg: dict,
    k3s_cfg: dict,
    ssh_hosts: dict,
    cni_iface: str,
) -> dict:
    nodes_in = cluster_cfg.get("nodes", [])
    if not nodes_in:
        raise ValueError("cluster.yaml has no nodes")

    # Figure out master + IP
    master_node = next((n for n in nodes_in if n.get("role") == "master"), nodes_in[0])
    master_ip = master_node["ip"]

    # SSH details: take the first host entry as representative; assume Vagrant
    # users + key are uniform across nodes (they are by construction).
    ssh_user = "vagrant"
    ssh_key = "~/.ssh/id_ed25519"
    if ssh_hosts:
        first = next(iter(ssh_hosts.values()))
        ssh_user = first.get("User", ssh_user)
        ssh_key = first.get("IdentityFile", ssh_key)

    k3s = k3s_cfg.get("k3s", {})
    registry = k3s_cfg.get("registry", {})

    inventory = {
        "cluster_name": cluster_cfg.get("cluster_name", "seedemu-k3s"),
        "reference_cluster": True,
        "runtime": "k3s",
        "max_validated_topology_size": int(k3s_cfg.get("max_validated_topology_size", 214)),
        "k3s": {
            "cluster_cidr": k3s.get("cluster_cidr", "10.42.0.0/16"),
            "service_cidr": k3s.get("service_cidr", "10.43.0.0/16"),
            "node_cidr_mask_size_ipv4": int(k3s.get("node_cidr_mask_size_ipv4", 24)),
            "max_pods": int(k3s.get("max_pods", 110)),
        },
        "ssh": {
            "user": ssh_user,
            "key_path_env": "SEED_K3S_SSH_KEY",
            "default_key_path": ssh_key,
        },
        "registry": {
            "host": master_ip,
            "port": int(registry.get("port", 5000)),
        },
        "cni": {
            "default_master_interface": cni_iface or "",
        },
        "nodes": [
            {
                "name": n["name"],
                "role": n.get("role", "worker"),
                "management_ip": n["ip"],
                "runtime": "k3s",
                "labels": {
                    "kubernetes.io/hostname": n["name"],
                },
            }
            for n in nodes_in
        ],
    }
    return inventory


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cluster", required=True, type=Path)
    p.add_argument("--k3s", required=True, type=Path)
    p.add_argument("--ssh-config", type=Path)
    p.add_argument(
        "--cni-interface",
        default="",
        help="Master's CNI master interface (eth1/enp0s8/...). "
             "If empty, the script will SSH to the master to detect it.",
    )
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()

    with open(args.cluster) as f:
        cluster_cfg = normalize_cluster(yaml.safe_load(f))
    with open(args.k3s) as f:
        k3s_cfg = yaml.safe_load(f)

    ssh_hosts = {}
    if args.ssh_config and args.ssh_config.exists():
        ssh_hosts = parse_ssh_config(args.ssh_config)

    cni_iface = args.cni_interface.strip()
    if not cni_iface and ssh_hosts:
        master_node = next(
            (n for n in cluster_cfg.get("nodes", []) if n.get("role") == "master"),
            None,
        )
        if master_node and master_node["name"] in ssh_hosts:
            cni_iface = detect_cni_interface_via_ssh(
                args.ssh_config, master_node["name"], master_node["ip"]
            )

    inventory = build_cluster_inventory(cluster_cfg, k3s_cfg, ssh_hosts, cni_iface)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        yaml.safe_dump(inventory, f, sort_keys=False, default_flow_style=False)

    print(f"Wrote {args.output}", file=sys.stderr)
    if not cni_iface:
        print(
            "Warning: cni.default_master_interface is empty. "
            "Phase 4 may need SEED_CNI_MASTER_INTERFACE to be exported manually.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
