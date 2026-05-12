#!/usr/bin/env python3
"""Generate Ansible inventory from configs/cluster.yaml + configs/k3s.yaml + output/vagrant_ssh_config.

The inventory has:
  - Two groups (master, workers) populated from cluster.yaml roles.
  - Per-host vars: ansible_host / port / user / private key (from ssh_config), node_ip,
    flannel_iface (left empty here; the playbook detects it at runtime).
  - Group-level vars resolved from k3s.yaml, including the URL set chosen by china_mirror.

Single source of truth = the two YAML files. This generator only adapts them to Ansible's
inventory format.
"""
import argparse
import os
import sys
from pathlib import Path

import yaml

# Local import: scripts/ contains both this file and normalize_cluster.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_cluster import normalize_cluster  # noqa: E402


def parse_ssh_config(path: Path) -> dict:
    """Return {host: {key: value, ...}} parsed from a Vagrant-style ssh-config."""
    hosts, current = {}, None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("Host "):
            current = line.split(None, 1)[1]
            hosts[current] = {}
            continue
        if current is None:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            hosts[current][parts[0]] = parts[1]
    return hosts


def build_host_vars(ssh_entry: dict, ip: str, role: str) -> dict:
    """Map ssh-config fields + node info to Ansible host vars."""
    host_vars = {
        "node_ip": ip,
        "k3s_role": "server" if role == "master" else "agent",
    }

    if ssh_entry:
        # When Vagrant exports forwarded ports (HostName 127.0.0.1 + Port 2222),
        # Ansible should connect via the forward, not the cluster IP.
        if ssh_entry.get("HostName") == "127.0.0.1" and "Port" in ssh_entry:
            host_vars["ansible_host"] = "127.0.0.1"
            host_vars["ansible_port"] = int(ssh_entry["Port"])
        elif "HostName" in ssh_entry:
            host_vars["ansible_host"] = ssh_entry["HostName"]
            if "Port" in ssh_entry and ssh_entry["Port"] != "22":
                host_vars["ansible_port"] = int(ssh_entry["Port"])
        if "User" in ssh_entry:
            host_vars["ansible_user"] = ssh_entry["User"]
        if "IdentityFile" in ssh_entry:
            host_vars["ansible_ssh_private_key_file"] = ssh_entry["IdentityFile"]
    else:
        host_vars["ansible_host"] = ip
        host_vars["ansible_user"] = "vagrant"

    return host_vars


def build_registries_yaml(registry_host: str, registry_port: int, mirrors: dict) -> str:
    """Render the K3s containerd registries.yaml as a YAML string.

    Generated here (not in the playbook) so jinja loop indentation isn't a
    concern — the playbook simply writes this string to disk.

    The local in-cluster registry (registry_host:registry_port) is auto-injected
    as the FIRST endpoint of every declared source. containerd will request the
    image there; a 404 or connection failure falls through to the user's mirrors.
    This lets SEED build push images to the local registry on master and have
    workers pull them over the LAN, without needing to rebuild on every node.
    """
    local_endpoint = f"http://{registry_host}:{registry_port}"
    local_key = f"{registry_host}:{registry_port}"

    doc: dict = {"mirrors": {}}
    # Direct lookups against the local registry (e.g. an image pushed there
    # explicitly under the local-registry prefix) keep working.
    doc["mirrors"][local_key] = {"endpoint": [local_endpoint]}
    # User mirrors with the local registry prepended.
    for source, endpoints in (mirrors or {}).items():
        chain = [local_endpoint] + [ep for ep in endpoints if ep != local_endpoint]
        doc["mirrors"][source] = {"endpoint": chain}
    # The local registry uses plain HTTP; containerd must skip TLS verification.
    doc["configs"] = {local_key: {"tls": {"insecure_skip_verify": True}}}
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def build_group_vars(k3s_cfg: dict, master_ip: str) -> dict:
    """Resolve URLs and parameters used by the playbook."""
    k3s = k3s_cfg.get("k3s", {})
    registry = k3s_cfg.get("registry", {})
    multus = k3s_cfg.get("multus", {})
    cni = k3s_cfg.get("cni", {})
    china_mirror = bool(k3s_cfg.get("china_mirror", False))

    # If user declared system_proxy=true in proxy_settings.yaml (host has
    # TUN-mode mihomo/clash or similar), bypass all the mirror logic — the
    # VM traffic transparently goes through the proxy, so canonical sources
    # (docker.io / ghcr.io / get.k3s.io / github.com) are both reachable
    # and more reliable than the third-party mirrors.
    try:
        proxy_cfg = yaml.safe_load(
            (Path(__file__).parent.parent / "configs" / "proxy_settings.yaml").read_text()
        ) or {}
    except Exception:
        proxy_cfg = {}
    system_proxy = bool(proxy_cfg.get("system_proxy", False))
    if system_proxy:
        china_mirror = False

    # Proxy passed through from host shell, originally loaded from
    # configs/proxy_settings.yaml by load_provider_path.sh. May all be empty
    # (= direct connect; framework just leaves env untouched downstream).
    proxy_http  = os.environ.get("HTTP_PROXY")  or os.environ.get("http_proxy")  or ""
    proxy_https = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    proxy_no    = os.environ.get("NO_PROXY")    or os.environ.get("no_proxy")    or ""
    # Dict for ansible `environment:` block on plays that hit the internet.
    # Empty dict when nothing is set = no-op for the task.
    http_proxy_env: dict = {}
    if proxy_http:
        http_proxy_env["HTTP_PROXY"] = proxy_http
        http_proxy_env["http_proxy"] = proxy_http
    if proxy_https:
        http_proxy_env["HTTPS_PROXY"] = proxy_https
        http_proxy_env["https_proxy"] = proxy_https
    if proxy_no:
        http_proxy_env["NO_PROXY"] = proxy_no
        http_proxy_env["no_proxy"] = proxy_no

    if china_mirror:
        urls = {
            "k3s_install_url": "https://rancher-mirror.rancher.cn/k3s/k3s-install.sh",
            "k3s_install_mirror_env": "INSTALL_K3S_MIRROR=cn",
            "multus_manifest_url": (
                "https://cdn.jsdelivr.net/gh/k8snetworkplumbingwg/"
                "multus-cni@master/deployments/multus-daemonset.yml"
            ),
            "cni_plugins_base": (
                "https://gh-proxy.com/https://github.com/"
                "containernetworking/plugins/releases/download"
            ),
            "docker_install_method": "aliyun",
        }
    else:
        urls = {
            "k3s_install_url": "https://get.k3s.io",
            "k3s_install_mirror_env": "",
            "multus_manifest_url": (
                "https://raw.githubusercontent.com/k8snetworkplumbingwg/"
                "multus-cni/master/deployments/multus-daemonset.yml"
            ),
            "cni_plugins_base": (
                "https://github.com/containernetworking/plugins/releases/download"
            ),
            "docker_install_method": "official",
        }

    registry_port = int(registry.get("port", 5000))
    if system_proxy:
        # Direct-to-canonical mode — proxy handles reachability for us.
        mirrors = {}
        docker_io_mirrors = []
    else:
        mirrors = registry.get("mirrors") or {
            "docker.io": ["https://docker.m.daocloud.io"],
        }
        docker_io_mirrors = list(mirrors.get("docker.io") or [])

    return {
        "ansible_python_interpreter": "/usr/bin/python3",
        "ansible_ssh_common_args": (
            "-o StrictHostKeyChecking=no "
            "-o UserKnownHostsFile=/dev/null "
            "-o ConnectTimeout=10"
        ),
        # K3s
        "k3s_version": k3s.get("version", "v1.28.5+k3s1"),
        "k3s_cluster_cidr": k3s.get("cluster_cidr", "10.42.0.0/16"),
        "k3s_service_cidr": k3s.get("service_cidr", "10.43.0.0/16"),
        "k3s_node_cidr_mask_size_ipv4": k3s.get("node_cidr_mask_size_ipv4", 24),
        "k3s_max_pods": k3s.get("max_pods", 110),
        "k3s_flannel_backend": k3s.get("flannel_backend", "host-gw"),
        # Registry — the playbook writes registries_yaml_content directly to
        # /etc/rancher/k3s/registries.yaml. Per-source mirrors come from
        # k3s.yaml.registry.mirrors.
        "registry_host": master_ip,
        "registry_port": registry_port,
        "registries_yaml_content": build_registries_yaml(master_ip, registry_port, mirrors),
        # Docker daemon registry-mirrors only applies to docker.io. We pass the
        # whole list; the playbook serialises it as a JSON array.
        "docker_daemon_mirrors": docker_io_mirrors,
        # CNI plugins
        "cni_type": cni.get("type", "macvlan"),
        "cni_plugins_version": "v1.6.2",
        # Multus
        "multus_install": bool(multus.get("install", True)),
        "multus_rbac_patch": bool(multus.get("rbac_patch", True)),
        # Proxy passed through to ansible — see configs/proxy_settings.yaml.
        # http_proxy_env is a dict (possibly empty) ready for `environment:`.
        "http_proxy_env": http_proxy_env,
        "proxy_http": proxy_http,
        "proxy_https": proxy_https,
        "proxy_no_proxy": proxy_no,
        # URL set
        **urls,
    }


def build_inventory(cluster_cfg: dict, k3s_cfg: dict, ssh_hosts: dict) -> dict:
    nodes = cluster_cfg.get("nodes", [])
    master_ip = next((n["ip"] for n in nodes if n.get("role") == "master"), "")

    inventory = {
        "all": {
            "vars": build_group_vars(k3s_cfg, master_ip),
            "children": {
                "master": {"hosts": {}},
                "workers": {"hosts": {}},
            },
        }
    }

    for node in nodes:
        name = node["name"]
        ip = node["ip"]
        role = node.get("role", "worker")
        host_vars = build_host_vars(ssh_hosts.get(name, {}), ip, role)
        group = "master" if role == "master" else "workers"
        inventory["all"]["children"][group]["hosts"][name] = host_vars

    return inventory


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cluster", required=True, type=Path)
    p.add_argument("--k3s", required=True, type=Path)
    p.add_argument("--ssh-config", type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()

    with open(args.cluster) as f:
        cluster_cfg = normalize_cluster(yaml.safe_load(f))
    with open(args.k3s) as f:
        k3s_cfg = yaml.safe_load(f)

    ssh_hosts = {}
    if args.ssh_config and args.ssh_config.exists():
        ssh_hosts = parse_ssh_config(args.ssh_config)

    inventory = build_inventory(cluster_cfg, k3s_cfg, ssh_hosts)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        yaml.safe_dump(inventory, f, sort_keys=False, default_flow_style=False)

    print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
