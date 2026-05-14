#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# SEED Emulator — Dependency installer / preflight checker
#
# Reads configs/deps.yaml and either:
#   check     verify deps are present (exit 0 if OK, 1 otherwise)
#   install   pip install --user the python packages, then verify CLI
#
# CLI tools (vagrant/kubectl/etc) are NOT auto-installed; install hints are
# printed when something is missing.
#
# Usage:
#   install_deps.sh check
#   install_deps.sh install
# ============================================================================

# macOS: bash sub-shells (and SSH non-login sessions) don't auto-source
# /opt/homebrew shellenv, so brew-installed CLIs (vagrant, ansible-playbook,
# kubectl, python3) end up "not in PATH" even though the user's interactive
# zsh sees them. Prepend brew prefixes so the rest of the framework finds them.
if [ "$(uname)" = "Darwin" ]; then
  for _brew_prefix in /opt/homebrew/bin /usr/local/bin; do
    [ -d "${_brew_prefix}" ] || continue
    case ":${PATH}:" in *":${_brew_prefix}:"*) ;; *) PATH="${_brew_prefix}:${PATH}" ;; esac
  done
  export PATH
  unset _brew_prefix
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPS_FILE="${REPO_ROOT}/configs/deps.yaml"

die() { echo "[install_deps] ERROR: $*" >&2; exit 1; }

[[ -f "${DEPS_FILE}" ]] || die "deps.yaml not found at ${DEPS_FILE}"

# Choose the Python interpreter for installing SEED's pinned requirements.
# Version constraint (every platform): 3.10-3.13.
#   - SEED upstream uses `match` (Python 3.10+) — rules out 3.9 and older.
#   - SEED's pinned reqs (rpds-py, eth-*, web3) only ship wheels for 3.10-3.13
#     — rules out 3.14+ (source-build chain may also be missing).
#
# Selection order:
#   1. $PYTHON env var (if set) — user-supplied override; lets pyenv/conda
#      users point at an exact binary. Still version-gated below.
#   2. macOS: brew python@3.12 (the only sweet spot — Apple system python is
#      3.9, brew default is 3.14). Auto-install via brew if missing.
#   3. Linux/WSL: whatever `command -v python3` resolves to. Distro python
#      is usually in range (Ubuntu 22.04=3.10, 24.04=3.12, Debian 12=3.11).
if [ -n "${PYTHON:-}" ]; then
  : # user override
elif [ "$(uname)" = "Darwin" ]; then
  PYTHON="/opt/homebrew/opt/python@3.12/bin/python3.12"
  if [ ! -x "${PYTHON}" ]; then
    if command -v brew &>/dev/null; then
      echo "[install_deps] installing brew python@3.12 (need 3.10-3.13 for SEED)" >&2
      HOMEBREW_BOTTLE_DOMAIN="${HOMEBREW_BOTTLE_DOMAIN:-https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles}" \
        brew install python@3.12 || die "brew install python@3.12 failed"
    else
      die "Need brew python@3.12 for SEED (rules out /usr/bin/python3 3.9 + brew default 3.14). Install brew first, or set PYTHON=/path/to/python3.{10,11,12,13}."
    fi
  fi
else
  PYTHON="$(command -v python3 || true)"
fi
[[ -x "${PYTHON}" ]] || die "python3 is required.  sudo apt install python3 python3-yaml  (or set PYTHON=/path/to/python3)"

# Version range gate: catch out-of-range early so SEED upstream's `match`
# syntax or SEED reqs' missing wheels don't surface later as a confusing
# SyntaxError / "no matching distribution" from pip.
if ! "${PYTHON}" -c 'import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) else 1)' 2>/dev/null; then
  pyver="$("${PYTHON}" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null || echo unknown)"
  die "Python ${pyver} at ${PYTHON} is out of supported range (3.10-3.13).
       SEED upstream needs >=3.10 (match syntax); SEED reqs lack 3.14+ wheels.
       Fix: install a 3.10-3.13 via apt/pyenv, or set PYTHON=/path/to/python3.{10,11,12,13} and re-run."
fi

# Bootstrap: install_deps.sh itself depends on PyYAML to read deps.yaml.
# When PYTHON was just freshly brew-installed (eg python@3.12 above), it
# starts empty — auto-install pyyaml so we can proceed.
if ! "${PYTHON}" -c "import yaml" 2>/dev/null; then
  echo "[install_deps] bootstrapping pyyaml for ${PYTHON}" >&2
  "${PYTHON}" -m pip install --user --quiet pyyaml 2>/dev/null \
    || "${PYTHON}" -m pip install --user --break-system-packages --quiet pyyaml 2>/dev/null \
    || die "Failed to install pyyaml for ${PYTHON}.  Run: ${PYTHON} -m pip install --user --break-system-packages pyyaml"
  "${PYTHON}" -c "import yaml" 2>/dev/null \
    || die "pyyaml still not importable after install attempt"
fi

ACTION="${1:-check}"
case "${ACTION}" in
  -h|--help|help) sed -n '4,15p' "$0"; exit 0 ;;
  check|install)  ;;
  *) die "Unknown action: ${ACTION} (try check|install)" ;;
esac

DEPS_FILE="${DEPS_FILE}" exec "${PYTHON}" "${SCRIPT_DIR}/_install_deps.py" "${ACTION}"
