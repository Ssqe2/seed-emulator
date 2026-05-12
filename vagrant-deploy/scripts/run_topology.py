#!/usr/bin/env python3
"""SEED topology driver — explicit kwargs, zero environment variables.

Reads our yaml configs (deploy.yaml + k3s.yaml + cluster.yaml) and computes
the kwargs that the topology Python file's run() function expects. Then
imports the topology file and calls run(...) directly.

This replaces upstream seed_k8s_profile_runner.sh's run_generic_compile()
for the `profile: custom` use case, which used to set a bunch of SEED_*
env vars and `exec python3 <compile_script>` — an AI-style indirection
that hides data flow. We pass values explicitly, like Docker.py users do.

Subsequent stages (build / deploy / verify) still flow through upstream
profile_runner — they don't invoke the topology Python directly, so the
env-var path doesn't pollute them.

Output goes to seed-emulator/output/profile_runs/<profile>/<timestamp>/compiled/
and we update the `latest` symlink so following stages find it.

Usage:
    python3 scripts/run_topology.py \\
        --deploy configs/deploy.yaml \\
        --k3s configs/k3s.yaml \\
        --cluster configs/cluster.yaml \\
        --seed-dir ..

Invoked from scripts/seed_run.sh when ACTION=compile + PROFILE=custom.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import os
import sys
from pathlib import Path

import yaml


def find_master_ip(cluster_cfg: dict, scripts_dir: Path) -> str:
    """Resolve the master node's IP via normalize_cluster()."""
    # normalize_cluster expands the shorthand form; reuse it for consistency
    # with seed_vagrant.sh and gen_inventory.py.
    sys.path.insert(0, str(scripts_dir))
    from normalize_cluster import normalize_cluster
    nodes = (normalize_cluster(cluster_cfg) or {}).get("nodes") or []
    for n in nodes:
        if n.get("role") == "master":
            return str(n.get("ip") or "")
    return ""


def load_topology_module(topology_abs: Path):
    """Dynamically import the topology Python file as a module."""
    spec = importlib.util.spec_from_file_location("seed_topology", topology_abs)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load topology spec from {topology_abs}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--deploy",   required=True, type=Path,
                   help="configs/deploy.yaml")
    p.add_argument("--k3s",      required=True, type=Path,
                   help="configs/k3s.yaml")
    p.add_argument("--cluster",  required=True, type=Path,
                   help="configs/cluster.yaml")
    p.add_argument("--seed-dir", required=True, type=Path,
                   help="seed-emulator/ root (upstream submodule)")
    p.add_argument("--run-id", type=str, default=None,
                   help="Override the timestamp used for the run directory "
                        "name; useful when re-running compile within an "
                        "existing run. Default: generate fresh timestamp.")
    args = p.parse_args()

    scripts_dir = Path(__file__).resolve().parent
    vagrant_deploy = scripts_dir.parent
    seed_dir = args.seed_dir.resolve()

    deploy  = yaml.safe_load(args.deploy.read_text())  or {}
    k3s     = yaml.safe_load(args.k3s.read_text())     or {}
    cluster = yaml.safe_load(args.cluster.read_text()) or {}

    profile = (deploy.get("profile") or "custom").strip()
    topology_rel = (deploy.get("topology_file") or "").strip()
    if not topology_rel:
        print("ERROR: deploy.yaml: topology_file is empty.", file=sys.stderr)
        return 1
    topology_abs = (vagrant_deploy / topology_rel).resolve()
    if not topology_abs.is_file():
        print(f"ERROR: topology file not found: {topology_abs}", file=sys.stderr)
        return 1

    # ---- Resolve kwargs from configs ----
    namespace = (deploy.get("namespace") or "seedemu-custom").strip()
    cni_type  = ((k3s.get("cni") or {}).get("type") or "vxlan-overlay").strip().lower()
    image_pull_policy = ((deploy.get("image") or {}).get("pull_policy") or "Always").strip()

    # CNI master interface — only meaningful for macvlan/ipvlan. Ansible step 0
    # writes the detected iface to output/cni_master_interface during stage_k3s.
    cni_iface_file = vagrant_deploy / "output" / "cni_master_interface"
    cni_master_interface = "eth0"
    if cni_iface_file.exists():
        val = cni_iface_file.read_text().strip()
        if val:
            cni_master_interface = val

    # Registry endpoint = <master_ip>:<port>. Matches what upstream
    # seed_k8s_cluster_inventory.py:138 computes.
    master_ip = find_master_ip(cluster, scripts_dir)
    registry_port = int((k3s.get("registry") or {}).get("port") or 5000)
    registry_prefix = f"{master_ip}:{registry_port}" if master_ip else f"localhost:{registry_port}"

    # ---- Output directory layout (mirrors profile_runner.sh:19) ----
    # seed-emulator/output/profile_runs/<profile>/<run_id>/compiled/
    run_id = args.run_id or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = seed_dir / "output" / "profile_runs" / profile / run_id
    compiled_dir = base_dir / "compiled"
    validation_dir = base_dir / "validation"
    compiled_dir.mkdir(parents=True, exist_ok=True)
    validation_dir.mkdir(parents=True, exist_ok=True)

    # ---- Prepare PYTHONPATH so `from seedemu...` import resolves ----
    # The topology imports seedemu.*; that lives under seed-emulator/seedemu/.
    sys.path.insert(0, str(seed_dir))

    print(f"[run_topology] profile          = {profile}")
    print(f"[run_topology] topology         = {topology_abs}")
    print(f"[run_topology] run_id           = {run_id}")
    print(f"[run_topology] output_dir       = {compiled_dir}")
    print(f"[run_topology] namespace        = {namespace}")
    print(f"[run_topology] cni_type         = {cni_type}")
    print(f"[run_topology] cni_master_iface = {cni_master_interface}")
    print(f"[run_topology] image_pull_pol   = {image_pull_policy}")
    print(f"[run_topology] registry_prefix  = {registry_prefix}")
    sys.stdout.flush()

    # ---- Load topology + invoke run() with explicit kwargs ----
    topo = load_topology_module(topology_abs)
    if not hasattr(topo, "run"):
        print(f"ERROR: {topology_abs} has no `run()` function.", file=sys.stderr)
        return 1

    # Topology may use relative paths (eg `os.path.dirname(__file__)`) inside,
    # so we don't chdir — keep cwd intact. The output_dir kwarg is absolute,
    # which dominates any relative fallback in the topology.
    topo.run(
        registry_prefix=registry_prefix,
        namespace=namespace,
        cni_type=cni_type,
        cni_master_interface=cni_master_interface,
        image_pull_policy=image_pull_policy,
        output_dir=str(compiled_dir),
    )

    # ---- Verify compiled artifacts exist (same check profile_runner does) ----
    k8s_yaml = compiled_dir / "k8s.yaml"
    build_sh = compiled_dir / "build_images.sh"
    missing = [str(p) for p in (k8s_yaml, build_sh) if not p.exists()]
    if missing:
        print(f"ERROR: compile finished but artifacts missing: {missing}", file=sys.stderr)
        return 1

    # ---- Update `latest` symlink (atomic-ish: unlink + symlink) ----
    # Profile_runner.sh maintains this pointer so subsequent stages
    # (build / deploy / verify) find the most recent compiled output via
    # output/profile_runs/<profile>/latest/compiled/.
    latest = seed_dir / "output" / "profile_runs" / profile / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
    except FileNotFoundError:
        pass
    latest.symlink_to(run_id)  # relative symlink, points to sibling dir
    print(f"[run_topology] latest -> {run_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
