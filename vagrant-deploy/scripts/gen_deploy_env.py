#!/usr/bin/env python3
"""Translate the three deploy YAML files into shell `export SEED_*=...` lines.

Reads:
  configs/deploy.yaml    -- common knobs the user touches every run
  configs/advanced.yaml  -- experiment / profile-specific / debug knobs
  configs/tuning.yaml    -- timeout / retry / parallelism dials

Each YAML field maps to one SEED_* environment variable. Empty values are
skipped so the upstream defaults stay in effect.

Usage:
    eval "$(python3 scripts/gen_deploy_env.py \\
        --deploy   configs/deploy.yaml \\
        --advanced configs/advanced.yaml \\
        --tuning   configs/tuning.yaml)"

Any of the --deploy / --advanced / --tuning arguments may be omitted (or
point to a missing file); we just skip that layer.

Variables intentionally NOT mapped (they live elsewhere or are not user-facing):
    - KVM-related (replaced by Vagrant)
    - K3s-install-related (replaced by scripts/ansible/seed_k3s.yml)
    - Cluster inventory (auto-exported by seed_k8s_cluster_inventory.sh)
    - Internal plumbing / runtime-computed paths
"""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


# (yaml-path-tuple, SEED_* env var name)
DEPLOY_MAPPING: list[tuple[tuple[str, ...], str]] = [
    # Profile selection
    (("profile",),                                "SEED_EXPERIMENT_PROFILE"),
    (("namespace",),                              "SEED_NAMESPACE"),
    # CNI
    (("cni", "type"),                             "SEED_CNI_TYPE"),
    # Scheduling (主选项)
    (("scheduling", "strategy"),                  "SEED_SCHEDULING_STRATEGY"),
    (("scheduling", "placement_mode"),            "SEED_PLACEMENT_MODE"),
    (("scheduling", "min_nodes_used"),            "SEED_MIN_NODES_USED"),
    (("scheduling", "require_all_nodes"),         "SEED_REQUIRE_ALL_NODES"),
    # Topology
    (("topology", "hosts_per_as"),                "SEED_HOSTS_PER_AS"),
    # Image
    (("image", "pull_policy"),                    "SEED_IMAGE_PULL_POLICY"),
    (("image", "distribution_mode"),              "SEED_IMAGE_DISTRIBUTION_MODE"),
    # Build / run id
    (("advanced", "build_parallelism"),           "SEED_BUILD_PARALLELISM"),
    (("advanced", "docker_buildkit"),             "SEED_DOCKER_BUILDKIT"),
    (("advanced", "run_id"),                      "SEED_RUN_ID"),
]


ADVANCED_MAPPING: list[tuple[tuple[str, ...], str]] = [
    # CNI 进阶
    (("cni", "auto_fallback"),                    "SEED_AUTO_CNI_FALLBACK"),
    # Scheduling 进阶
    (("scheduling", "node_labels"),               "SEED_NODE_LABELS_JSON"),
    (("scheduling", "node_pod_reserve"),          "SEED_NODE_POD_RESERVE"),
    (("scheduling", "colocate_ix_peers"),         "SEED_COLOCATE_IX_PEERS"),
    # Topology 进阶
    (("topology", "size"),                        "SEED_TOPOLOGY_SIZE"),
    (("topology", "file"),                        "SEED_TOPOLOGY_FILE"),
    (("topology", "real_topology_dir"),           "SEED_REAL_TOPOLOGY_DIR"),
    # Image 进阶
    (("image", "preload_fallback_mode"),          "SEED_PRELOAD_FALLBACK_MODE"),
    (("image", "acceptance_distribution_mode"),   "SEED_ACCEPTANCE_IMAGE_DISTRIBUTION_MODE"),
    # Routing / BGP 实验
    (("routing", "bgp_startup_mode"),             "SEED_BGP_STARTUP_MODE"),
    (("routing", "phase_start_driver"),           "SEED_PHASE_START_DRIVER"),
    (("routing", "kernel_export_mode"),           "SEED_KERNEL_EXPORT_MODE"),
    (("routing", "routing_kernel_export_mode"),   "SEED_ROUTING_KERNEL_EXPORT_MODE"),
    (("routing", "export_bgp_to_kernel"),         "SEED_K8S_RUNTIME_EXPORT_BGP_TO_KERNEL"),
    (("routing", "ospf_timing_profile"),          "SEED_OSPF_TIMING_PROFILE"),
    (("routing", "ibgp_reflection_mode"),         "SEED_IBGP_REFLECTION_MODE"),
    # Observability
    (("observability", "grafana_password"),       "SEED_OBS_GRAFANA_PASSWORD"),
    (("observability", "namespace"),              "SEED_OBS_NAMESPACE"),
    # Failure injection
    (("failure_injection", "action_map"),         "SEED_FAILURE_ACTION_MAP"),
    (("failure_injection", "recovery_mode"),      "SEED_FAILURE_INJECTION_RECOVERY_MODE"),
    # Namespace management
    (("namespace_management", "clean"),           "SEED_CLEAN_NAMESPACE"),
    (("namespace_management", "excluded"),        "SEED_EXCLUDED_NAMESPACES"),
    # KubeVirt
    (("kubevirt", "vm_node"),                     "SEED_VM_NODE"),
    (("kubevirt", "worker_a"),                    "SEED_WORKER_A"),
    (("kubevirt", "worker_b"),                    "SEED_WORKER_B"),
    # Kind
    (("kind", "fix_masq"),                        "SEED_KIND_FIX_MASQ"),
    (("kind", "masq_exempt_cidrs"),               "SEED_KIND_MASQ_EXEMPT_CIDRS"),
    # Agent
    (("agent", "proactive_mode"),                 "SEED_AGENT_PROACTIVE_MODE"),
    # Debug
    (("debug", "runner_log"),                     "SEED_RUNNER_LOG"),
    (("debug", "smoke_verbose"),                  "SEED_SMOKE_VERBOSE"),
    (("debug", "showcase_port"),                  "SEED_SHOWCASE_PORT"),
    (("debug", "web151_sim_ip"),                  "SEED_WEB151_SIM_IP"),
    (("debug", "kubecontext"),                    "SEED_KUBECONTEXT"),
    # Files
    (("files", "assignment"),                     "SEED_ASSIGNMENT_FILE"),
    (("files", "registry_local_endpoint"),        "SEED_REGISTRY_LOCAL_ENDPOINT"),
    # Image push 调优
    (("advanced", "docker_max_concurrent_uploads"), "SEED_DOCKER_MAX_CONCURRENT_UPLOADS"),
]


TUNING_MAPPING: list[tuple[tuple[str, ...], str]] = [
    # Timeouts
    (("timeouts", "acceptance_namespace_delete"), "SEED_ACCEPTANCE_NAMESPACE_DELETE_TIMEOUT_SECONDS"),
    (("timeouts", "bgp_phase"),                   "SEED_BGP_PHASE_TIMEOUT_SECONDS"),
    (("timeouts", "bird_phase"),                  "SEED_BIRD_PHASE_TIMEOUT_SECONDS"),
    (("timeouts", "bird_start_exec"),             "SEED_BIRD_START_EXEC_TIMEOUT_SECONDS"),
    (("timeouts", "generic_deploy"),              "SEED_GENERIC_DEPLOY_TIMEOUT_SECONDS"),
    (("timeouts", "kernel_birdc"),                "SEED_KERNEL_BIRDC_TIMEOUT_SECONDS"),
    (("timeouts", "kernel_exec"),                 "SEED_KERNEL_EXEC_TIMEOUT_SECONDS"),
    (("timeouts", "kernel_scan_base"),            "SEED_KERNEL_SCAN_BASE_SECONDS"),
    (("timeouts", "kernel_scan_jitter"),          "SEED_KERNEL_SCAN_JITTER_SECONDS"),
    (("timeouts", "kubectl_exec"),                "SEED_KUBECTL_EXEC_TIMEOUT_SECONDS"),
    (("timeouts", "registry_push"),               "SEED_REGISTRY_PUSH_TIMEOUT_SECONDS"),
    (("timeouts", "senior_bird_settle"),          "SEED_SENIOR_BIRD_SETTLE_SECONDS"),
    (("timeouts", "ssh_connect"),                 "SEED_SSH_CONNECT_TIMEOUT_SECONDS"),
    (("timeouts", "ssh_long_probe"),              "SEED_SSH_LONG_PROBE_TIMEOUT_SECONDS"),
    (("timeouts", "ssh_probe"),                   "SEED_SSH_PROBE_TIMEOUT_SECONDS"),
    # Retries
    (("retries", "bird_start"),                   "SEED_BIRD_START_RETRIES"),
    (("retries", "bird_start_backoff"),           "SEED_BIRD_START_RETRY_BACKOFF_SECONDS"),
    (("retries", "kernel_switch"),                "SEED_KERNEL_SWITCH_RETRIES"),
    (("retries", "kernel_switch_backoff"),        "SEED_KERNEL_SWITCH_RETRY_BACKOFF_SECONDS"),
    (("retries", "registry_push"),                "SEED_REGISTRY_PUSH_RETRIES"),
    (("retries", "registry_push_backoff"),        "SEED_REGISTRY_PUSH_BACKOFF_SECONDS"),
    # Parallelism
    (("parallelism", "bgp_health"),               "SEED_BGP_HEALTH_PARALLELISM"),
    (("parallelism", "phase_protocol"),           "SEED_PHASE_PROTOCOL_PARALLELISM"),
    (("parallelism", "phase_status"),             "SEED_PHASE_STATUS_PARALLELISM"),
    (("parallelism", "relationship_sample_limit"), "SEED_RELATIONSHIP_SAMPLE_LIMIT"),
]


def dig(cfg: dict, path: Iterable[str]) -> Any:
    cur = cfg
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def lines_for(cfg_path: Path | None, mapping: list[tuple[tuple[str, ...], str]]) -> list[str]:
    if cfg_path is None or not cfg_path.exists():
        return []
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f) or {}
    out: list[str] = []
    for path, env_name in mapping:
        rendered = stringify(dig(cfg, path))
        if rendered is None:
            continue
        out.append(f"export {env_name}={shlex.quote(rendered)}")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--deploy",   type=Path, default=None)
    p.add_argument("--advanced", type=Path, default=None)
    p.add_argument("--tuning",   type=Path, default=None)
    args = p.parse_args()

    all_lines: list[str] = []
    all_lines += lines_for(args.deploy,   DEPLOY_MAPPING)
    all_lines += lines_for(args.advanced, ADVANCED_MAPPING)
    all_lines += lines_for(args.tuning,   TUNING_MAPPING)

    sys.stdout.write("\n".join(all_lines))
    if all_lines:
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
