#!/usr/bin/env python3
# encoding: utf-8

# Adapted from examples/basic/A21_shadow_internet/shadow_internet.py
# Uses KubernetesCompiler instead of DockerCompiler

from seedemu import *
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
import os
import json


def _parse_node_labels_json(raw: str):
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid SEED_NODE_LABELS_JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("SEED_NODE_LABELS_JSON must be a JSON object")
    normalized = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            raise ValueError(f"SEED_NODE_LABELS_JSON['{key}'] must be an object of label->value")
        normalized[str(key)] = {str(k): str(v) for k, v in value.items()}
    return normalized


def run():
    # Create the Emulator
    emu = Emulator()

    # Create the base layer
    base = Base()

    # Create a web service layer
    web = WebService()

    ###########################################################################
    # Create Internet exchanges
    ix100 = base.createInternetExchange(100)
    ix101 = base.createInternetExchange(101)
    ix102 = base.createInternetExchange(102)
    ix100.getPeeringLan().setDisplayName('New York-100')
    ix101.getPeeringLan().setDisplayName('Chicago-101')
    ix102.getPeeringLan().setDisplayName('Houston-102')

    ###########################################################################
    # Create transit and stub ASes
    Makers.makeTransitAs(base, 4, [100, 101, 102],
           [(100, 101), (101, 102), (100, 102)]
    )

    Makers.makeStubAs(emu, base, 160, 100, [web, None])
    Makers.makeStubAs(emu, base, 161, 101, [None, web])
    Makers.makeStubAs(emu, base, 162, 102, [None, None])
    Makers.makeStubAs(emu, base, 163, 102, [None, None])

    ###########################################################################
    # Allow outside computer to VPN into AS-162's network
    ovpn = OpenVpnRemoteAccessProvider()
    as162 = base.getAutonomousSystem(162)
    as162.getNetwork('net0').enableRemoteAccess(ovpn)

    ###########################################################################
    # Create real-world AS.
    # AS11872 is the Syracuse University's autonomous system
    as11872 = base.createAutonomousSystem(11872)
    as11872.createRealWorldRouter('rw').joinNetwork('ix100', '10.100.0.118')

    ###########################################################################
    # BGP peering
    ebgp = Ebgp()

    ebgp.addPrivatePeerings(100, [4],  [160, 11872], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [4],  [161], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(102, [4],  [162, 163], PeerRelationship.Provider)
    ebgp.addPrivatePeering(102, 162, 163, PeerRelationship.Peer)

    ###########################################################################
    emu.addLayer(base)
    emu.addLayer(ebgp)
    emu.addLayer(web)
    emu.addLayer(Routing())
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())

    ###########################################################################
    # Rendering
    emu.render()

    ###########################################################################
    # Kubernetes Compilation
    registry_prefix      = os.environ.get("SEED_REGISTRY", "localhost:5001")
    namespace            = os.environ.get("SEED_NAMESPACE", "seedemu")
    cluster_name         = os.environ.get("SEED_CLUSTER_NAME", "seedemu-kvtest")
    cni_type             = os.environ.get("SEED_CNI_TYPE", "bridge").strip().lower()
    cni_master_interface = os.environ.get("SEED_CNI_MASTER_INTERFACE", "eth0").strip()
    image_pull_policy    = os.environ.get("SEED_IMAGE_PULL_POLICY", "Always").strip()
    scheduling_strategy  = os.environ.get("SEED_SCHEDULING_STRATEGY", SchedulingStrategy.AUTO).strip().lower()
    node_labels          = _parse_node_labels_json(os.environ.get("SEED_NODE_LABELS_JSON", ""))

    k8s = KubernetesCompiler(
        registry_prefix=registry_prefix,
        namespace=namespace,
        use_multus=True,
        internetMapEnabled=False,
        scheduling_strategy=scheduling_strategy,
        node_labels=node_labels,
        cni_type=cni_type,
        cni_master_interface=cni_master_interface,
        generate_services=True,
        image_pull_policy=image_pull_policy,
    )

    output_dir = os.environ.get("SEED_OUTPUT_DIR")
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(__file__), 'output_shadow_internet')
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(__file__), output_dir)

    emu.compile(k8s, output_dir, override=True)

    print(f"Compilation complete. Output generated in {output_dir}")
    print(f"Registry prefix: {registry_prefix}")
    print(f"Namespace: {namespace}")


if __name__ == "__main__":
    run()
