#!/usr/bin/env python3
"""Print the K8s namespace that the current deploy.yaml will deploy into.

Resolution order:
  1. deploy.yaml: namespace (if non-empty)
  2. profile yaml: profiles[<profile>].default_namespace
  3. (fallback) empty string

Usage:
    ns=$(python3 scripts/get_active_namespace.py)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--deploy",       required=True, type=Path)
    p.add_argument("--profile-yaml", required=True, type=Path)
    args = p.parse_args()

    deploy = yaml.safe_load(args.deploy.read_text()) or {}
    explicit = (deploy.get("namespace") or "").strip()
    if explicit:
        print(explicit)
        return 0

    profile = (deploy.get("profile") or "").strip()
    if not profile or not args.profile_yaml.exists():
        return 0

    catalog = yaml.safe_load(args.profile_yaml.read_text()) or {}
    profiles = catalog.get("profiles") or {}
    block = profiles.get(profile) or {}
    default_ns = (block.get("default_namespace") or "").strip()
    print(default_ns)
    return 0


if __name__ == "__main__":
    sys.exit(main())
