#!/usr/bin/env python3
# encoding: utf-8

"""
k8s_multinode_demo.py - Demonstrates multi-node Kubernetes deployment features

This example shows how to use the new KubernetesCompiler features:
- Scheduling strategies (by_as, by_role, custom)
- Resource limits (requests/limits)
- CNI type configuration (bridge, macvlan, ipvlan)
- Service generation

Usage:
    python3 k8s_multinode_demo.py [macvlan|ipvlan|bridge]
"""

import json
import os
import sys

from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.core import Binding, Emulator, Filter
from seedemu.layers import Base, Ebgp, Ibgp, Ospf, Routing
from seedemu.layers.Ebgp import PeerRelationship
from seedemu.services import WebService


def _env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, "").strip() or default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return default


def _env_json(key: str, default):
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def run(cni_type_arg: str = "bridge"):
    # Initialize the emulator and layers
    emu = Emulator()
    base = Base()
    routing = Routing()
    ebgp = Ebgp()
    web = WebService()

    ###############################################################################
    # Create Internet Exchanges
    base.createInternetExchange(100)
    base.createInternetExchange(101)

    ###############################################################################
    # Create and set up AS-150 (on node1)
    as150 = base.createAutonomousSystem(150)
    as150.createNetwork('net0')
    as150.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    as150.createHost('web').joinNetwork('net0')
    web.install('web150')
    emu.addBinding(Binding('web150', filter=Filter(nodeName='web', asn=150)))

    ###############################################################################
    # Create and set up AS-151 (on node2)
    as151 = base.createAutonomousSystem(151)
    as151.createNetwork('net0')
    as151.createRouter('router0').joinNetwork('net0').joinNetwork('ix101')
    as151.createHost('web').joinNetwork('net0')
    web.install('web151')
    emu.addBinding(Binding('web151', filter=Filter(nodeName='web', asn=151)))

    ###############################################################################
    # Create and set up Transit AS-2
    as2 = base.createAutonomousSystem(2)
    as2.createNetwork('net0')
    as2.createNetwork('net1')
    as2.createRouter('r1').joinNetwork('net0').joinNetwork('ix100')
    as2.createRouter('r2').joinNetwork('net0').joinNetwork('net1')
    as2.createRouter('r3').joinNetwork('net1').joinNetwork('ix101')

    ###############################################################################
    # Peering
    ebgp.addPrivatePeerings(100, [2], [150], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2], [151], PeerRelationship.Provider)

    ###############################################################################
    # Rendering
    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())
    emu.addLayer(web)

    emu.render()

    ###############################################################################
    # Kubernetes Compilation with Multi-Node Features

    # Cluster infrastructure (env-driven, preserved from refactor - injected by deployment framework)
    registry_prefix       = _env_str("SEED_REGISTRY", "127.0.0.1:5001")
    namespace             = _env_str("SEED_NAMESPACE", "seedemu")
    # CLI-passed cni_type overrides default; SEED_CNI_TYPE env still wins
    # over the CLI arg to keep yaml-driven deploys deterministic.
    cni_type              = _env_str("SEED_CNI_TYPE", cni_type_arg).lower()
    cni_master_interface  = _env_str("SEED_CNI_MASTER_INTERFACE", "eth0")
    image_pull_policy     = _env_str("SEED_IMAGE_PULL_POLICY", "Always")

    # Topology-author decisions (hardcoded, from the original file).
    # This demo pins AS-150/151/2 to specific nodes (node1/node2/node3)
    # via BY_AS scheduling and applies a default resource budget.
    scheduling_strategy = SchedulingStrategy.BY_AS
    node_labels = {
        "150": {"kubernetes.io/hostname": "node1"},
        "151": {"kubernetes.io/hostname": "node2"},
        "2": {"kubernetes.io/hostname": "node3"},  # Transit AS on node3
    }
    default_resources = {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "500m", "memory": "512Mi"},
    }
    use_multus = True
    internet_map_enabled = True
    generate_services = True
    service_type = "ClusterIP"
    local_link_cni_type = None

    output_dir = os.environ.get("SEED_OUTPUT_DIR")
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(__file__), f"output_multinode_{cni_type}")
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(__file__), output_dir)

    k8s = KubernetesCompiler(
        registry_prefix=registry_prefix,
        namespace=namespace,
        use_multus=use_multus,
        internetMapEnabled=internet_map_enabled,
        scheduling_strategy=scheduling_strategy,
        node_labels=node_labels,
        default_resources=default_resources,
        cni_type=cni_type,
        local_link_cni_type=local_link_cni_type,
        cni_master_interface=cni_master_interface,
        generate_services=generate_services,
        service_type=service_type,
        image_pull_policy=image_pull_policy,
    )

    # Generate internet-map Deployment + NodePort Service (K8s compiler
    # requires explicit attachInternetMap() call, unlike Docker which does
    # it automatically when internetMapEnabled=True).
    # Must be called BEFORE emu.compile() so the manifest/build-command appends
    # get serialized into k8s.yaml and build_images.sh during _doCompile().
    k8s.attachInternetMap()

    # Compile
    emu.compile(k8s, output_dir, override=True)

    print(f"""
================================================================================
Multi-Node Kubernetes Deployment Generated!
================================================================================

Output Directory: {output_dir}
Registry Prefix: {registry_prefix}
Namespace: {namespace}
CNI Type: {cni_type}
Scheduling Strategy: CUSTOM (by AS number)

Node Placement:
  - AS150 (web + router) -> node1
  - AS151 (web + router) -> node2
  - AS2 (transit)        -> node3

Resource Limits:
  - CPU: 100m-500m per pod
  - Memory: 128Mi-512Mi per pod

Next Steps:
  1. Label your K8s nodes:
     kubectl label node node1 kubernetes.io/hostname=node1
     kubectl label node node2 kubernetes.io/hostname=node2
     kubectl label node node3 kubernetes.io/hostname=node3

  2. Build and push images:
     cd {output_dir} && ./build_images.sh

  3. Deploy to K8s:
     kubectl create ns {namespace}
     kubectl apply -n {namespace} -f k8s.yaml

  4. Verify pod placement:
     kubectl get pods -n {namespace} -o wide

================================================================================
""")


if __name__ == '__main__':
    cni_type_cli = "bridge"
    if len(sys.argv) > 1:
        cni_type_cli = sys.argv[1].lower()
        if cni_type_cli not in ["bridge", "macvlan", "ipvlan"]:
            print(f"Unknown CNI type: {cni_type_cli}")
            print("Usage: python3 k8s_multinode_demo.py [macvlan|ipvlan|bridge]")
            sys.exit(1)

    run(cni_type_cli)
