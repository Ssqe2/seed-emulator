# Source-only helper. Configures local environment from user's declarative
# configs:
#   1. WSL auto-detect → VAGRANT_WSL_ENABLE_WINDOWS_ACCESS=1
#   2. configs/provider_settings.yaml → prepend hypervisor bin_dir to PATH
# install_deps.sh validates the underlying files separately.

# 1. WSL auto-detect (vagrant requires this env to use Windows-side hypervisors).
if grep -qi microsoft /proc/version 2>/dev/null; then
  export VAGRANT_WSL_ENABLE_WINDOWS_ACCESS=1
fi

__seed_helper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 2. Hypervisor bin_dir → PATH
__seed_bin_dir="$(python3 "${__seed_helper_dir}/get_provider_bin_dir.py" 2>/dev/null || true)"
if [ -n "${__seed_bin_dir}" ]; then
  case ":${PATH}:" in *":${__seed_bin_dir}:"*) ;; *) PATH="${__seed_bin_dir}:${PATH}" ;; esac
  export PATH
fi

unset __seed_helper_dir __seed_bin_dir
