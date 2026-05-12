#!/usr/bin/env python3
"""Print the bin_dir of the provider selected in cluster.yaml.

Reads:
  configs/cluster.yaml          (provider field)
  configs/provider_settings.yaml (per-provider bin_dir)

Stdout: bin_dir, or nothing (with exit 1) if the file/field is missing.
Used by scripts/load_provider_path.sh to prepend the right hypervisor
binary directory to PATH before vagrant/ansible spawn anything.
"""
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit(1)

cfg_dir = Path(__file__).parent.parent / "configs"
try:
    cluster = yaml.safe_load((cfg_dir / "cluster.yaml").read_text()) or {}
except Exception:
    sys.exit(1)
provider = cluster.get("provider", "")
if not provider:
    sys.exit(1)
ps_path = cfg_dir / "provider_settings.yaml"
if not ps_path.is_file():
    sys.exit(1)
try:
    ps = yaml.safe_load(ps_path.read_text()) or {}
except Exception:
    sys.exit(1)
bin_dir = (ps.get(provider) or {}).get("bin_dir", "").strip()
if not bin_dir:
    sys.exit(1)
print(bin_dir)
