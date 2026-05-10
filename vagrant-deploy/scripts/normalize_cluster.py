#!/usr/bin/env python3
"""Normalize a SEED Emulator cluster.yaml.

cluster.yaml supports two syntaxes:

(1) Shorthand — for homogeneous workers, the user only writes:

        provider: vmware_desktop
        box: generic/ubuntu2204
        network:
          type: private_network
          cidr: 192.168.77.0/24
        workers: 10
        master: { cpus: 2, memory: 4096 }
        worker: { cpus: 2, memory: 2048 }

(2) Explicit — for heterogeneous setups, the user writes a full nodes list:

        nodes:
          - { name: master,   role: master, cpus: 4, memory: 8192,  ip: 192.168.77.10 }
          - { name: worker-a, role: worker, cpus: 2, memory: 2048,  ip: 192.168.77.11 }
          - { name: worker-b, role: worker, cpus: 8, memory: 16384, ip: 192.168.77.20 }

If `nodes:` is non-empty the explicit list wins. Otherwise the shorthand
fields are expanded into a nodes list:

  - master goes at <cidr-network>.<master_offset>      (default offset 10)
  - worker N (1..workers) goes at <cidr-network>.<master_offset + N>

The function returns a dict that is shape-compatible with the explicit form,
so all downstream consumers (Vagrantfile generator, ansible inventory
generator, compiler cluster-inventory generator) can pretend they only ever
got the explicit form.

Usage from Python:
    from normalize_cluster import normalize_cluster
    cfg = normalize_cluster(yaml.safe_load(open('cluster.yaml')))
    for node in cfg['nodes']:
        ...

Usage from the shell:
    python3 scripts/normalize_cluster.py configs/cluster.yaml > /tmp/normalized.yaml
"""
from __future__ import annotations

import argparse
import ipaddress
import sys
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CIDR = "192.168.77.0/24"
DEFAULT_MASTER_OFFSET = 10        # master IP suffix within the /24


def _ip_for_offset(cidr: str, offset: int) -> str:
    """Return the n-th host address inside the given CIDR (offset 10 → .10)."""
    network = ipaddress.ip_network(cidr, strict=False)
    if offset >= network.num_addresses - 1:
        raise ValueError(f"offset {offset} exceeds address space of {cidr}")
    return str(network.network_address + offset)


def _expand_shorthand(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a nodes list from `workers` / `master` / `worker` / network.cidr."""
    workers = int(cfg.get("workers", 0))
    if workers < 0:
        raise ValueError(f"`workers` must be >= 0 (got {workers})")

    network = cfg.get("network", {}) or {}
    cidr = network.get("cidr", DEFAULT_CIDR)
    master_offset = int(network.get("master_offset", DEFAULT_MASTER_OFFSET))

    master_tpl = cfg.get("master", {}) or {}
    worker_tpl = cfg.get("worker", {}) or {}

    nodes: list[dict[str, Any]] = []

    # Master always present.
    nodes.append({
        "name": "master",
        "role": "master",
        "cpus": int(master_tpl.get("cpus", 2)),
        "memory": int(master_tpl.get("memory", 4096)),
        "ip": _ip_for_offset(cidr, master_offset),
    })

    for i in range(1, workers + 1):
        nodes.append({
            "name": f"worker{i}",
            "role": "worker",
            "cpus": int(worker_tpl.get("cpus", 2)),
            "memory": int(worker_tpl.get("memory", 2048)),
            "ip": _ip_for_offset(cidr, master_offset + i),
        })

    return nodes


def normalize_cluster(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a dict whose `nodes` field is always an explicit list."""
    cfg = dict(cfg or {})
    explicit = cfg.get("nodes") or []

    if explicit:
        # User wrote the explicit list; trust it but clean up types/defaults.
        nodes = []
        for n in explicit:
            nodes.append({
                "name": str(n["name"]),
                "role": str(n.get("role", "worker")),
                "cpus": int(n.get("cpus", 2)),
                "memory": int(n.get("memory", 2048)),
                "ip": str(n["ip"]),
            })
    else:
        nodes = _expand_shorthand(cfg)

    cfg["nodes"] = nodes
    # Drop shorthand fields from the normalized form to avoid confusion
    # downstream — `nodes` is the only thing consumers look at.
    for key in ("workers", "master", "worker"):
        cfg.pop(key, None)

    return cfg


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("cluster_yaml", type=Path)
    args = p.parse_args()

    with open(args.cluster_yaml) as f:
        raw = yaml.safe_load(f) or {}
    normalized = normalize_cluster(raw)
    yaml.safe_dump(normalized, sys.stdout, sort_keys=False, default_flow_style=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
