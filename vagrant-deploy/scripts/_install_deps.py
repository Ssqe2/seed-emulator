#!/usr/bin/env python3
"""SEED Emulator — Dependency checker / installer.

Called by scripts/install_deps.sh. Reads configs/deps.yaml from path provided
in env var DEPS_FILE (defaults to configs/deps.yaml relative to cwd).

Actions:
    check     Verify python_packages and cli_required. Exit 0 if OK, 1 otherwise.
    install   pip install --user the python_packages, then verify cli_required.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


def load_deps(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def check_cli(deps: dict) -> list[str]:
    required = deps.get("cli_required") or []
    return [cmd for cmd in required if shutil.which(cmd) is None]


def check_python(deps: dict) -> list[str]:
    pkgs = deps.get("python_packages") or []
    problems: list[str] = []
    import importlib.metadata as md

    for spec in pkgs:
        m = re.match(r"^([A-Za-z0-9_.\-]+)(.*)$", str(spec).strip())
        if not m:
            continue
        name, constraint = m.group(1), m.group(2).strip()
        try:
            installed = md.version(name)
        except md.PackageNotFoundError:
            problems.append(f"{name}: NOT installed (need {constraint or 'any'})")
            continue
        if constraint:
            try:
                from packaging.specifiers import SpecifierSet
                if installed not in SpecifierSet(constraint):
                    problems.append(f"{name} {installed} fails constraint {constraint}")
            except ImportError:
                # `packaging` not available; skip version check (best-effort).
                pass
    return problems


def print_cli_missing(missing: list[str], hints: dict) -> None:
    print("[install_deps] CLI tools missing:", file=sys.stderr)
    for cmd in missing:
        h = hints.get(cmd, "")
        print(f"  - {cmd}", file=sys.stderr)
        if h:
            print(f"      install: {h}", file=sys.stderr)


def action_check(deps: dict) -> int:
    rc = 0
    py_problems = check_python(deps)
    if py_problems:
        print("[install_deps] Python package issues:", file=sys.stderr)
        for p in py_problems:
            print(f"  - {p}", file=sys.stderr)
        print("  fix with: bash scripts/install_deps.sh install", file=sys.stderr)
        rc = 1
    cli_missing = check_cli(deps)
    if cli_missing:
        print_cli_missing(cli_missing, deps.get("install_hints") or {})
        rc = 1
    if rc == 0:
        print("[install_deps] All dependencies satisfied.")
    return rc


def action_install(deps: dict) -> int:
    pkgs = deps.get("python_packages") or []
    if pkgs:
        print(f"[install_deps] pip install --user {' '.join(map(str, pkgs))}")
        ret = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--user", "--quiet", *map(str, pkgs)],
            check=False,
        )
        if ret.returncode != 0:
            print("[install_deps] pip install failed.", file=sys.stderr)
            return ret.returncode
    cli_missing = check_cli(deps)
    if cli_missing:
        print_cli_missing(cli_missing, deps.get("install_hints") or {})
        print(
            "[install_deps] CLI tools are NOT auto-installed; install them then re-run check.",
            file=sys.stderr,
        )
        return 1
    print("[install_deps] All dependencies satisfied.")
    return 0


def main() -> int:
    deps_file = Path(os.environ.get("DEPS_FILE") or "configs/deps.yaml")
    if not deps_file.is_file():
        print(f"[install_deps] deps.yaml not found at {deps_file}", file=sys.stderr)
        return 2
    deps = load_deps(deps_file)
    action = sys.argv[1] if len(sys.argv) > 1 else "check"
    if action == "check":
        return action_check(deps)
    if action == "install":
        return action_install(deps)
    print(f"[install_deps] unknown action: {action} (try check|install)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
