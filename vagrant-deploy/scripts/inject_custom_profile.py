#!/usr/bin/env python3
"""Auto-register a 'custom' profile in the upstream profile catalog when the
user has set `profile: custom` + `topology_file: <path>` in deploy.yaml.

This lets users add their own SEED topology Python script without having to
hand-edit seed-emulator/configs/seed_k8s_profiles.yaml.

Behavior:
    deploy.yaml has profile=custom, topology_file=topologies/my.py
        -> upsert profiles.custom in the upstream profile yaml,
           with compile_script pointing at the absolute path of my.py.
    deploy.yaml has profile != custom
        -> remove profiles.custom from the upstream yaml (if present)
           so leftover state from a previous custom run does not linger.
    deploy.yaml has profile=custom but topology_file is empty
        -> error.

The script only touches the `custom` key — every other profile defined by
upstream is left untouched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


CUSTOM_KEY = "custom"


def build_profile_block(topology_abspath: Path, deploy: dict) -> dict:
    # cni.type 是部署决策,scheduling_strategy 是拓扑作者决策(defined-by-topology)
    compiler = deploy.get("compiler") or {}
    cni = deploy.get("cni") or {}
    return {
        "profile_id": CUSTOM_KEY,
        "support_tier": "tier3",
        "acceptance_level": "best_effort",
        "capacity_gate": "none",
        "default_topology_size": 0,
        "compile_script": str(topology_abspath),
        "default_namespace": (deploy.get("namespace") or "seedemu-custom"),
        "default_cni_type": (cni.get("type") or "macvlan"),
        # scheduling_strategy: yaml 空 = defined-by-topology, 不硬编码 fallback
        # 否则 profile_runner.sh 会用这个 default 覆盖拓扑作者意图
        "default_scheduling_strategy": (compiler.get("scheduling_strategy") or ""),
        "verify_mode": "generic_ready",
        "verify_targets": {
            "min_deployments_available": 1,
            "min_pods_running": 1,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--deploy",  required=True, type=Path)
    p.add_argument("--profile-yaml", required=True, type=Path,
                   help="Upstream seed_k8s_profiles.yaml")
    p.add_argument("--repo-root", required=True, type=Path,
                   help="Project root (where topology_file paths are resolved against)")
    args = p.parse_args()

    deploy = yaml.safe_load(args.deploy.read_text()) or {}
    profile_name = (deploy.get("profile") or "").strip()
    topo_raw = (deploy.get("topology_file") or "").strip()

    profiles_doc = yaml.safe_load(args.profile_yaml.read_text()) or {}
    profiles_doc.setdefault("profiles", {})

    if profile_name == CUSTOM_KEY:
        if not topo_raw:
            print(
                "ERROR: deploy.yaml has profile=custom but topology_file is empty.\n"
                "       Set topology_file to your SEED topology Python script "
                "(relative to project root).",
                file=sys.stderr,
            )
            return 1
        topo_abs = (args.repo_root / topo_raw).resolve()
        if not topo_abs.exists():
            print(f"ERROR: topology_file not found: {topo_abs}", file=sys.stderr)
            return 1
        profiles_doc["profiles"][CUSTOM_KEY] = build_profile_block(topo_abs, deploy)
        action = f"injected -> compile_script={topo_abs}"
    else:
        # Remove a stale 'custom' profile so the catalog stays clean.
        if profiles_doc["profiles"].pop(CUSTOM_KEY, None) is None:
            return 0  # nothing to do
        action = "removed stale 'custom' profile"

    args.profile_yaml.write_text(
        yaml.safe_dump(profiles_doc, sort_keys=False, default_flow_style=False)
    )
    print(f"[inject_custom_profile] {action}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
