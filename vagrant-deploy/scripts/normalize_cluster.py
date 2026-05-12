#!/usr/bin/env python3
"""Normalize a SEED Emulator cluster.yaml.

cluster.yaml supports two syntaxes:

(1) Shorthand — for mostly-homogeneous clusters, the user writes:

        provider: vmware_desktop
        box: generic/ubuntu2204
        network:
          type: private_network
          cidr: 192.168.77.0/24    # optional — picked per-provider if omitted
        workers: 3
        master: { cpus: 2, memory: 4096 }
        worker: { cpus: 2, memory: 2048 }      # default template for every worker

    Per-worker overrides on top of the shorthand template are supported via
    top-level keys named worker1, worker2, worker3, ... Any field set here
    wins over the worker template; missing fields fall back to the template:

        worker1: { cpus: 4, memory: 8192 }            # override #1 cpu/memory
        worker3: { cpus: 8, memory: 16384, ip: 20 }   # override #3 + move IP to .20

    `ip` in an override must be an integer last-octet offset (e.g. `ip: 11`
    expands to <cidr-prefix>.11). The cidr prefix is fixed; only the offset
    can move. Use the explicit `nodes:` form below if you need a full IP.

    Constraint: a workerN override requires `workers >= N` — overriding a
    worker that the cluster doesn't even have is an error (caught here at
    normalize time, not silently dropped).

(2) Explicit — for fully heterogeneous setups, the user writes a full nodes
    list and is responsible for every field:

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
import re
import sys
from pathlib import Path
from typing import Any

import yaml


# Per-provider CIDR default. Two reasons CIDR is provider-coupled:
#   1. VirtualBox 6.1+ restricts host-only networks to 192.168.56.0/21
#      (hardcoded in VBox; users would otherwise need root-edit
#      C:\ProgramData\VirtualBox\networks.conf which violates hands-off).
#   2. VMware has no such restriction; we use .77 to keep clear of the
#      .56 range commonly occupied by VBox-leftover adapters when both
#      hypervisors are installed on the same host.
PROVIDER_DEFAULT_CIDR = {
    "vmware_desktop": "192.168.77.0/24",
    "virtualbox":     "192.168.56.0/24",
    "libvirt":        "192.168.77.0/24",
}
DEFAULT_CIDR = "192.168.77.0/24"  # ultimate fallback if provider unknown
DEFAULT_MASTER_OFFSET = 10        # master IP suffix within the /24


def _ip_for_offset(cidr: str, offset: int) -> str:
    """Return the n-th host address inside the given CIDR (offset 10 → .10)."""
    network = ipaddress.ip_network(cidr, strict=False)
    if offset >= network.num_addresses - 1:
        raise ValueError(f"offset {offset} exceeds address space of {cidr}")
    return str(network.network_address + offset)


_WORKER_OVERRIDE_RE = re.compile(r"^worker(\d+)$")


def _parse_worker_override_index(key: str) -> int | None:
    """Return N for 'workerN' keys (N >= 1), or None otherwise.
    The bare 'worker' key (template) and unrelated keys return None."""
    m = _WORKER_OVERRIDE_RE.match(key)
    if not m:
        return None
    n = int(m.group(1))
    return n if n >= 1 else None


def _expand_shorthand(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a nodes list from `workers` / `master` / `worker` / `workerN` / network.cidr."""
    workers = int(cfg.get("workers", 0))
    if workers < 0:
        raise ValueError(f"`workers` must be >= 0 (got {workers})")

    network = cfg.get("network", {}) or {}
    provider = str(cfg.get("provider", "") or "")
    default_cidr = PROVIDER_DEFAULT_CIDR.get(provider, DEFAULT_CIDR)
    cidr = network.get("cidr") or default_cidr
    master_offset = int(network.get("master_offset", DEFAULT_MASTER_OFFSET))

    master_tpl = cfg.get("master", {}) or {}
    worker_tpl = cfg.get("worker", {}) or {}

    # Collect per-worker overrides (workerN top-level keys) and validate
    # that workers >= N — overriding a worker that doesn't exist is an
    # error, not a silent drop.
    worker_overrides: dict[int, dict[str, Any]] = {}
    for key, val in cfg.items():
        n = _parse_worker_override_index(key)
        if n is None:
            continue
        if n > workers:
            raise ValueError(
                f"cluster.yaml has '{key}' override but workers={workers}. "
                f"To customize {key}, set workers >= {n}."
            )
        worker_overrides[n] = val or {}

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
        # Merge: worker template provides defaults, workerN override wins
        # for any field it sets.
        override = worker_overrides.get(i, {})
        merged_cpus = override.get("cpus", worker_tpl.get("cpus", 2))
        merged_memory = override.get("memory", worker_tpl.get("memory", 2048))

        # IP override is an integer last-octet offset. Default offset is
        # master_offset + i (i.e. .10 + i for the standard /24 + offset 10).
        ip_override = override.get("ip")
        if ip_override is None:
            ip_offset = master_offset + i
        elif isinstance(ip_override, int) and not isinstance(ip_override, bool):
            ip_offset = ip_override
        else:
            raise ValueError(
                f"cluster.yaml worker{i}.ip must be an integer offset "
                f"(e.g. `ip: 11` for .11). Got {ip_override!r}. "
                f"Use the explicit `nodes:` form for a full IP."
            )

        nodes.append({
            "name": f"worker{i}",
            "role": "worker",
            "cpus": int(merged_cpus),
            "memory": int(merged_memory),
            "ip": _ip_for_offset(cidr, ip_offset),
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

    # Sanity check: no two nodes share an IP. Applies to both shorthand
    # (where a workerN.ip override could land on master_offset or duplicate
    # another worker's default offset) and explicit (where the user could
    # have a typo in two `ip:` fields).
    seen: dict[str, str] = {}
    for n in nodes:
        ip = n["ip"]
        if ip in seen:
            raise ValueError(
                f"cluster.yaml IP conflict: nodes '{seen[ip]}' and "
                f"'{n['name']}' both have IP {ip}. Pick a different "
                f"`workerN.ip` offset or fix the duplicate in `nodes:`."
            )
        seen[ip] = n["name"]

    cfg["nodes"] = nodes
    # Drop shorthand fields (and per-worker overrides workerN) from the
    # normalized form to avoid confusion downstream — `nodes` is the only
    # thing consumers look at.
    for key in list(cfg.keys()):
        if key in ("workers", "master", "worker"):
            cfg.pop(key, None)
        elif _parse_worker_override_index(key) is not None:
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
