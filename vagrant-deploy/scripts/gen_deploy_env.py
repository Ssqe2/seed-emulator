#!/usr/bin/env python3
"""Translate deploy/k3s/advanced/tuning YAMLs into newline-separated CLI
arguments to forward to seed_k8s_profile_runner.sh.

Reads:
  configs/deploy.yaml    -- common knobs the user touches every run
  configs/k3s.yaml       -- K3s infrastructure (cni.type is shared with the
                            ansible playbook; single source of truth)
  configs/advanced.yaml  -- experiment / profile-specific / debug knobs
  configs/tuning.yaml    -- timeout / retry / parallelism dials

Output: one token per line.  Caller eval-reads into an array, e.g.
    mapfile -t args < <(python3 scripts/gen_deploy_env.py ...)
    exec scripts/seed_k8s_profile_runner.sh "${profile}" "${action}" "${args[@]}"

Each yaml field maps to one CLI long-option in seed_k8s_profile_runner.sh.
Empty / missing values are skipped so the runner falls back to its baked-in
defaults (or to the profile catalog's default_* value).

Used to be `eval "$(... )"` of `export SEED_X=Y` lines — now it produces
plain CLI args so no env-var leakage exists between caller and callee.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml


# (yaml-path-tuple, CLI long-option name)
DEPLOY_MAPPING: list[tuple[tuple[str, ...], str]] = [
    (("namespace",),                              "--namespace"),
    (("scheduling", "strategy"),                  "--scheduling-strategy"),
    (("scheduling", "placement_mode"),            "--placement-mode"),
    (("topology", "hosts_per_as"),                "--hosts-per-as"),
    (("image", "pull_policy"),                    "--image-pull-policy"),
    (("image", "distribution_mode"),              "--image-distribution-mode"),
    (("advanced", "build_parallelism"),           "--build-parallelism"),
    (("advanced", "docker_buildkit"),             "--docker-buildkit"),
    (("advanced", "run_id"),                      "--run-id"),
]


# k3s.yaml is canonical for infrastructure-layer CNI choice. Ansible reads
# the same value for the node-side bridge/plugin install — single source.
K3S_MAPPING: list[tuple[tuple[str, ...], str]] = [
    (("cni", "type"),                             "--cni-type"),
]


ADVANCED_MAPPING: list[tuple[tuple[str, ...], str]] = [
    (("routing", "bgp_startup_mode"),             "--bgp-startup-mode"),
    (("topology", "size"),                        "--topology-size"),
    (("failure_injection", "action_map"),         "--failure-action-map"),
    (("agent", "proactive_mode"),                 "--agent-proactive-mode"),
    (("debug", "runner_log"),                     "--runner-log"),
    (("debug", "showcase_port"),                  "--showcase-port"),
    (("advanced", "docker_max_concurrent_uploads"), "--docker-max-concurrent-uploads"),
]


TUNING_MAPPING: list[tuple[tuple[str, ...], str]] = [
    (("timeouts", "kubectl_exec"),                "--kubectl-exec-timeout"),
    (("timeouts", "generic_deploy"),              "--generic-deploy-timeout"),
    (("timeouts", "registry_push"),               "--registry-push-timeout"),
    (("retries", "registry_push"),                "--registry-push-retries"),
    (("retries", "registry_push_backoff"),        "--registry-push-backoff"),
    (("parallelism", "bgp_health"),               "--bgp-health-parallelism"),
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


def emit_for(
    cfg_path: Path | None,
    mapping: list[tuple[tuple[str, ...], str]],
) -> list[str]:
    """Return a flat list of `[option, value, option, value, ...]` tokens."""
    if cfg_path is None or not cfg_path.exists():
        return []
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    tokens: list[str] = []
    for path, option in mapping:
        rendered = stringify(dig(cfg, path))
        if rendered is None:
            continue
        tokens.append(option)
        tokens.append(rendered)
    return tokens


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--deploy",   type=Path, default=None)
    p.add_argument("--k3s",      type=Path, default=None)
    p.add_argument("--advanced", type=Path, default=None)
    p.add_argument("--tuning",   type=Path, default=None)
    args = p.parse_args()

    tokens: list[str] = []
    tokens += emit_for(args.deploy,   DEPLOY_MAPPING)
    tokens += emit_for(args.k3s,      K3S_MAPPING)
    tokens += emit_for(args.advanced, ADVANCED_MAPPING)
    tokens += emit_for(args.tuning,   TUNING_MAPPING)

    for tok in tokens:
        # One token per line. Newline-separated rather than space-separated
        # so values that contain whitespace survive `mapfile`-style readback.
        print(tok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
