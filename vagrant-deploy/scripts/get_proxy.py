#!/usr/bin/env python3
"""Print proxy environment exports from configs/proxy_settings.yaml.

Output is shell-eval friendly:
    export HTTP_PROXY='...'
    export http_proxy='...'
    export HTTPS_PROXY='...'
    export https_proxy='...'
    export NO_PROXY='...'
    export no_proxy='...'

Empty fields are skipped. Exit 1 if file missing or unparseable so the
caller (load_provider_path.sh) silently no-ops in that case.
"""
import sys
import shlex
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit(1)

cfg = Path(__file__).parent.parent / "configs" / "proxy_settings.yaml"
if not cfg.is_file():
    sys.exit(1)
try:
    ps = yaml.safe_load(cfg.read_text()) or {}
except Exception:
    sys.exit(1)

out: list[str] = []
for key, env_upper in [
    ("http", "HTTP_PROXY"),
    ("https", "HTTPS_PROXY"),
    ("no_proxy", "NO_PROXY"),
]:
    val = (ps.get(key) or "").strip()
    if not val:
        continue
    q = shlex.quote(val)
    out.append(f"export {env_upper}={q}")
    out.append(f"export {env_upper.lower()}={q}")

if out:
    print("\n".join(out))
