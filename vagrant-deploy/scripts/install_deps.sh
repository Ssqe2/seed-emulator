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
# Hard constraints on macOS:
#   - SEED upstream uses `match` (Python 3.10+) — rules out Apple's /usr/bin/python3 (3.9)
#   - SEED's pinned reqs (rpds-py, eth-*, web3) only ship wheels for 3.10-3.13
#     — rules out brew's default `python3` (currently 3.14)
# Sweet spot is brew python@3.12. Auto-install if missing.
if [ "$(uname)" = "Darwin" ]; then
  PYTHON="/opt/homebrew/opt/python@3.12/bin/python3.12"
  if [ ! -x "${PYTHON}" ]; then
    if command -v brew &>/dev/null; then
      echo "[install_deps] installing brew python@3.12 (need 3.10-3.13 for SEED)" >&2
      HOMEBREW_BOTTLE_DOMAIN="${HOMEBREW_BOTTLE_DOMAIN:-https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles}" \
        brew install python@3.12 || die "brew install python@3.12 failed"
    else
      die "Need brew python@3.12 for SEED (rules out /usr/bin/python3 3.9 + brew default 3.14). Install brew first."
    fi
  fi
else
  PYTHON="$(command -v python3 || true)"
fi
[[ -x "${PYTHON}" ]] || die "python3 is required.  sudo apt install python3 python3-yaml"

# Bootstrap: install_deps.sh itself depends on PyYAML to read deps.yaml.
if ! "${PYTHON}" -c "import yaml" 2>/dev/null; then
  die "Python module 'yaml' missing.  ${PYTHON} -m pip install --user pyyaml  (or: sudo apt install python3-yaml)"
fi

ACTION="${1:-check}"
case "${ACTION}" in
  -h|--help|help) sed -n '4,15p' "$0"; exit 0 ;;
  check|install)  ;;
  *) die "Unknown action: ${ACTION} (try check|install)" ;;
esac

DEPS_FILE="${DEPS_FILE}" exec "${PYTHON}" "${SCRIPT_DIR}/_install_deps.py" "${ACTION}"
