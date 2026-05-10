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


def get_active_provider(deps_file: Path) -> str | None:
    """Read configs/cluster.yaml's `provider` field. Returns None if missing."""
    cluster_yaml = deps_file.parent / "cluster.yaml"
    if not cluster_yaml.is_file():
        return None
    try:
        cfg = yaml.safe_load(cluster_yaml.read_text()) or {}
        return cfg.get("provider")
    except Exception:
        return None


def is_macos() -> bool:
    return sys.platform == "darwin"


def check_mac_brew_packages(ensure: bool = False) -> list[str]:
    """SEED upstream / our framework rely on a few CLI tools that come
    pre-installed on Linux but not on macOS:

      bash 4+   — required for ${VAR@Q} parameter transformations in
                  upstream seed_k8s_profile_runner.sh; macOS ships GPL-v2
                  bash 3.2 which lacks this.
      flock     — used by upstream profile_runner.sh for run locking;
                  Linux ships it via util-linux, macOS needs `brew install flock`.

    When ensure=True we'll auto-`brew install` whatever's missing,
    routing bottle downloads through the tsinghua mirror to avoid
    ghcr.io network issues from China.
    """
    if not is_macos():
        return []
    problems: list[str] = []

    # Each entry: (binary path to check, brew formula, optional version-major-floor)
    requirements = [
        ("/opt/homebrew/bin/bash", "bash", 4),
        ("/opt/homebrew/bin/flock", "flock", None),
    ]

    env = os.environ.copy()
    env.setdefault("HOMEBREW_BOTTLE_DOMAIN",
                   "https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles")

    for binpath, formula, min_major in requirements:
        if not Path(binpath).is_file():
            if ensure and shutil.which("brew"):
                print(f"[install_deps] installing brew {formula} (required by framework/upstream)", file=sys.stderr)
                ret = subprocess.run(["brew", "install", formula], check=False, env=env)
                if ret.returncode != 0 or not Path(binpath).is_file():
                    problems.append(f"brew install {formula} failed; please install manually")
                    continue
            else:
                problems.append(
                    f"brew {formula} not found at {binpath} — install: brew install {formula}"
                )
                continue
        if min_major is not None:
            try:
                out = subprocess.run([binpath, "--version"], capture_output=True, text=True, check=False).stdout
                major = int(out.split("version ")[1].split(".")[0])
                if major < min_major:
                    problems.append(f"{formula} version {major} < {min_major}: brew upgrade {formula}")
            except Exception:
                pass
    return problems


def check_provider_deps(deps: dict, provider: str, ensure: bool = False) -> list[str]:
    """Validate per-provider dependencies (plugins, files, apps, daemons).
    When ensure=True, attempt sudo launchctl/systemctl to start any
    not-running daemon (with platform-appropriate command).
    Returns a list of human-readable problems (empty = all good)."""
    problems: list[str] = []
    pdeps_all = deps.get("provider_dependencies") or {}
    pdeps = pdeps_all.get(provider)
    if not pdeps:
        return problems  # provider not declared → skip

    # 1. vagrant plugins
    plugins = pdeps.get("vagrant_plugins") or []
    if plugins and shutil.which("vagrant"):
        try:
            out = subprocess.run(
                ["vagrant", "plugin", "list"],
                capture_output=True, text=True, check=False, timeout=15,
            ).stdout
        except Exception:
            out = ""
        for plug in plugins:
            if plug not in out:
                problems.append(
                    f"vagrant plugin missing: {plug}  (install: vagrant plugin install {plug})"
                )

    # 2. extra CLI binaries
    for cli in pdeps.get("cli") or []:
        if shutil.which(cli) is None:
            problems.append(f"CLI missing: {cli}")

    # 3. file paths that must exist
    for f in pdeps.get("files") or []:
        path = f.get("path", "")
        if path and not Path(path).exists():
            problems.append(
                f"file missing: {path}  ({f.get('purpose','')}) — install: {f.get('install_hint','')}"
            )

    # 4. multi-platform app paths (any-of)
    for a in pdeps.get("apps") or []:
        paths = a.get("paths") or []
        if paths and not any(Path(p).exists() for p in paths):
            problems.append(
                f"app not found in any of {paths}  ({a.get('purpose','')}) — install: {a.get('install_hint','')}"
            )

    # 5. daemons (probe + optional ensure)
    for d in pdeps.get("daemons") or []:
        name = d.get("name", "?")
        probe = d.get("probe", "")
        expected = str(d.get("expected", "")).strip()
        if not probe:
            continue
        try:
            res = subprocess.run(
                ["sh", "-c", probe], capture_output=True, text=True, check=False, timeout=10
            )
            actual = res.stdout.strip()
        except Exception:
            actual = ""
        if actual == expected:
            continue
        # Daemon not responding as expected — print diagnostic so the user
        # doesn't have to manually run probe to figure out why.
        print(
            f"[install_deps] daemon {name} probe failed:\n"
            f"  cmd:    {probe}\n"
            f"  stdout: {actual!r}\n"
            f"  stderr: {(res.stderr or '').strip()!r}\n"
            f"  exit:   {res.returncode}\n"
            f"  expect: {expected!r}",
            file=sys.stderr,
        )
        if not ensure:
            problems.append(f"daemon not running: {name} (see probe diagnostic above)")
            continue
        ensure_cmd = d.get("ensure_macos") if is_macos() else d.get("ensure_linux")
        if not ensure_cmd:
            problems.append(f"daemon not running: {name} (no ensure command for this platform)")
            continue
        print(f"[install_deps] starting {name} via: {ensure_cmd}", file=sys.stderr)
        ret = subprocess.run(["sh", "-c", ensure_cmd], check=False)
        if ret.returncode != 0:
            problems.append(f"failed to start daemon {name}: {ensure_cmd}")
            continue
        # Re-probe after ensure (give daemon a couple seconds to come up).
        import time
        last_res = res
        for _ in range(5):
            time.sleep(1)
            last_res = subprocess.run(
                ["sh", "-c", probe], capture_output=True, text=True, check=False, timeout=10
            )
            if last_res.stdout.strip() == expected:
                break
        else:
            print(
                f"[install_deps] daemon {name} STILL not responding after ensure:\n"
                f"  stdout: {last_res.stdout.strip()!r}\n"
                f"  stderr: {(last_res.stderr or '').strip()!r}\n"
                f"  exit:   {last_res.returncode}",
                file=sys.stderr,
            )
            problems.append(f"daemon {name} still not responding after ensure")
    return problems


def print_provider_problems(provider: str, problems: list[str]) -> None:
    print(f"[install_deps] Provider '{provider}' dependency issues:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)


def action_check(deps: dict, deps_file: Path) -> int:
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
    mac_bash_problems = check_mac_brew_packages(ensure=False)
    if mac_bash_problems:
        print("[install_deps] macOS bash issues:", file=sys.stderr)
        for p in mac_bash_problems:
            print(f"  - {p}", file=sys.stderr)
        rc = 1
    provider = get_active_provider(deps_file)
    if provider:
        prov_problems = check_provider_deps(deps, provider, ensure=False)
        if prov_problems:
            print_provider_problems(provider, prov_problems)
            print(
                "  fix with: bash scripts/install_deps.sh install   # (will sudo to start daemons)",
                file=sys.stderr,
            )
            rc = 1
    if rc == 0:
        print("[install_deps] All dependencies satisfied.")
    return rc


def install_seed_requirements(deps_file: Path) -> int:
    """If seed-emulator's own requirements.txt exists at the parent of
    vagrant-deploy/, pip-install it. SEED's seedemu package imports a long
    tail of 3rd-party libs (requests, geopy, eth_account, web3, ...) at
    module load; rather than enumerate them individually we just install
    upstream's pinned set."""
    # deps_file = vagrant-deploy/configs/deps.yaml
    # → vagrant-deploy/.. = seed-emulator/
    seed_root = deps_file.parent.parent.parent
    req = seed_root / "requirements.txt"
    if not req.is_file():
        print(f"[install_deps] note: SEED requirements.txt not found at {req}; skipping", file=sys.stderr)
        return 0
    print(f"[install_deps] pip install -r {req} (SEED upstream deps)", file=sys.stderr)
    base = [sys.executable, "-m", "pip", "install", "--user", "--quiet", "-r", str(req)]
    ret = subprocess.run(base, check=False, capture_output=True, text=True)
    if ret.returncode != 0 and "externally-managed-environment" in (ret.stderr or ""):
        ret = subprocess.run(base + ["--break-system-packages"], check=False)
    elif ret.returncode != 0:
        sys.stderr.write(ret.stderr or "")
    return ret.returncode


def action_install(deps: dict, deps_file: Path) -> int:
    pkgs = deps.get("python_packages") or []
    if pkgs:
        # --break-system-packages bypasses PEP 668 (Homebrew/Debian "externally
        # managed" python). Combined with --user it stays in the user's
        # site-packages dir, so it doesn't actually touch the system python.
        # Older pip (< 23.0) doesn't know the flag — try without first, fall
        # back to --break-system-packages if pip rejects.
        base = [sys.executable, "-m", "pip", "install", "--user", "--quiet"]
        print(f"[install_deps] pip install --user {' '.join(map(str, pkgs))}")
        ret = subprocess.run(base + list(map(str, pkgs)), check=False, capture_output=True, text=True)
        if ret.returncode != 0 and "externally-managed-environment" in (ret.stderr or ""):
            print("[install_deps] retrying with --break-system-packages (Homebrew/PEP 668)")
            ret = subprocess.run(
                base + ["--break-system-packages", *map(str, pkgs)],
                check=False,
            )
        elif ret.returncode != 0:
            sys.stderr.write(ret.stderr or "")
        if ret.returncode != 0:
            print("[install_deps] pip install failed.", file=sys.stderr)
            return ret.returncode
    # Also pip-install SEED upstream's own requirements.txt (long tail of
    # 3rd-party deps imported by seedemu/* modules at load time).
    rc = install_seed_requirements(deps_file)
    if rc != 0:
        print("[install_deps] pip install -r seed requirements.txt failed.", file=sys.stderr)
        return rc
    cli_missing = check_cli(deps)
    if cli_missing:
        print_cli_missing(cli_missing, deps.get("install_hints") or {})
        print(
            "[install_deps] CLI tools are NOT auto-installed; install them then re-run check.",
            file=sys.stderr,
        )
        return 1
    mac_bash_problems = check_mac_brew_packages(ensure=True)
    if mac_bash_problems:
        print("[install_deps] macOS bash issues:", file=sys.stderr)
        for p in mac_bash_problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    provider = get_active_provider(deps_file)
    if provider:
        prov_problems = check_provider_deps(deps, provider, ensure=True)
        if prov_problems:
            print_provider_problems(provider, prov_problems)
            print(
                "[install_deps] Provider deps could not be auto-fixed; install missing items then re-run.",
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
        return action_check(deps, deps_file)
    if action == "install":
        return action_install(deps, deps_file)
    print(f"[install_deps] unknown action: {action} (try check|install)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
