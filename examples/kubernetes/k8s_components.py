#!/usr/bin/env python3
# encoding: utf-8

# Kubernetes adaptation of examples/basic/A05_components/components.py
# Demonstrates: loading a pre-built component (A01 transit AS topology),
# extending it with new hosts/AS/IX, and compiling with KubernetesCompiler.

from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.services import WebService
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.core import Emulator, Binding, Filter
import os
import sys
import json


def _parse_node_labels_json(raw: str):
    """Parse SEED_NODE_LABELS_JSON into the format expected by KubernetesCompiler."""
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
    ###############################################################################
    # Initialize emulator and layers
    emu     = Emulator()
    base    = Base()
    routing = Routing()
    ebgp    = Ebgp()
    ibgp    = Ibgp()
    ospf    = Ospf()
    web     = WebService()

    ###############################################################################
    # Inline the pre-built A01 component topology
    # (equivalent to loading examples/basic/A01_transit_as component)

    # Create two Internet Exchanges
    base.createInternetExchange(100)
    base.createInternetExchange(101)

    # Transit AS-2
    as2 = base.createAutonomousSystem(2)
    as2.createNetwork('net0')
    as2.createNetwork('net1')
    as2.createNetwork('net2')
    # ix100 <--> r1 <--> r2 <--> r3 <--> r4 <--> ix101
    as2.createRouter('r1').joinNetwork('net0').joinNetwork('ix100')
    as2.createRouter('r2').joinNetwork('net0').joinNetwork('net1')
    as2.createRouter('r3').joinNetwork('net1').joinNetwork('net2')
    as2.createRouter('r4').joinNetwork('net2').joinNetwork('ix101')

    # Stub AS-151 (connects to ix100)
    as151 = base.createAutonomousSystem(151)
    as151.createNetwork('net0')
    as151.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    as151.createHost('host0').joinNetwork('net0')

    # Stub AS-152 (connects to ix101)
    as152 = base.createAutonomousSystem(152)
    as152.createNetwork('net0')
    as152.createRouter('router0').joinNetwork('net0').joinNetwork('ix101')
    as152.createHost('host0').joinNetwork('net0')

    # Stub AS-153 (connects to ix101)
    as153 = base.createAutonomousSystem(153)
    as153.createNetwork('net0')
    as153.createRouter('router0').joinNetwork('net0').joinNetwork('ix101')
    as153.createHost('host0').joinNetwork('net0')

    # A01 BGP peerings
    ebgp.addPrivatePeering(100, 2, 151, abRelationship=PeerRelationship.Provider)
    ebgp.addPrivatePeering(101, 2, 152, abRelationship=PeerRelationship.Provider)
    ebgp.addPrivatePeering(101, 2, 153, abRelationship=PeerRelationship.Provider)
    ebgp.addPrivatePeering(101, 152, 153, abRelationship=PeerRelationship.Peer)

    ###############################################################################
    # A05 Extension 1: Add a new host to AS-151

    as151.createHost('web-2').joinNetwork('net0')
    web.install('web151-2')
    emu.addBinding(Binding('web151-2', filter=Filter(nodeName='web-2', asn=151)))

    ###############################################################################
    # A05 Extension 2: Add a new autonomous system (AS-154)

    as154 = base.createAutonomousSystem(154)
    as154.createNetwork('net0')

    as154.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    as154.createRouter('router1').joinNetwork('net0').joinNetwork('ix101')

    as154.createHost('web').joinNetwork('net0')
    web.install('web154')
    emu.addBinding(Binding('web154', filter=Filter(nodeName='web', asn=154)))

    # Peer AS-154 with AS-151 (Provider) and AS-152 (Peer)
    ebgp.addPrivatePeering(100, 151, 154, abRelationship=PeerRelationship.Provider)
    ebgp.addPrivatePeering(101, 152, 154, abRelationship=PeerRelationship.Peer)

    ###############################################################################
    # A05 Extension 3: Add a new Internet Exchange (IX-102)
    #   and connect AS-152 and AS-154 to it

    base.createInternetExchange(102)

    # Add new BGP router to AS-152, connect to IX-102
    as152.createRouter('router1').joinNetwork('net0').joinNetwork('ix102')

    # Add new BGP router to AS-154, connect to IX-102
    as154.createRouter('router2').joinNetwork('net0').joinNetwork('ix102')

    # Peer AS-152 and AS-154 at IX-102
    ebgp.addPrivatePeering(102, 152, 154, abRelationship=PeerRelationship.Peer)

    ###############################################################################
    # Add all layers

    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    emu.addLayer(web)

    emu.render()

    ###############################################################################
    # Kubernetes Compilation

    registry_prefix      = os.environ.get("SEED_REGISTRY", "localhost:5001")
    namespace            = os.environ.get("SEED_NAMESPACE", "seedemu")
    cluster_name         = os.environ.get("SEED_CLUSTER_NAME", "seedemu-kvtest")
    cni_type             = os.environ.get("SEED_CNI_TYPE", "bridge").strip().lower()
    cni_master_interface = os.environ.get("SEED_CNI_MASTER_INTERFACE", "eth0").strip()
    image_pull_policy    = os.environ.get("SEED_IMAGE_PULL_POLICY", "Always").strip()
    scheduling_strategy  = os.environ.get("SEED_SCHEDULING_STRATEGY", SchedulingStrategy.AUTO).strip().lower()
    node_labels          = _parse_node_labels_json(os.environ.get("SEED_NODE_LABELS_JSON", ""))
    force_colocate       = os.environ.get("SEED_FORCE_COLOCATE", "false").strip().lower() in {"1", "true", "yes"}

    # Co-locate all ASes onto a single node when using bridge CNI without explicit placement
    if force_colocate and not node_labels and cni_type in {"bridge", "host-local"}:
        single_node = os.environ.get("SEED_SINGLE_NODE", f"{cluster_name}-control-plane").strip()
        colocate_asns = [100, 101, 102, 2, 151, 152, 153, 154]
        node_labels = {str(asn): {"kubernetes.io/hostname": single_node} for asn in colocate_asns}
        scheduling_strategy = SchedulingStrategy.CUSTOM

    k8s = KubernetesCompiler(
        registry_prefix=registry_prefix,
        namespace=namespace,
        use_multus=True,
        internetMapEnabled=True,
        scheduling_strategy=scheduling_strategy,
        node_labels=node_labels,
        cni_type=cni_type,
        cni_master_interface=cni_master_interface,
        generate_services=True,
        image_pull_policy=image_pull_policy,
    )

    output_dir = os.environ.get("SEED_OUTPUT_DIR")
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(__file__), 'output_components')
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(__file__), output_dir)

    emu.compile(k8s, output_dir, override=True)

    print(f"Compilation complete. Output generated in: {output_dir}")
    print(f"Registry prefix : {registry_prefix}")
    print(f"Namespace       : {namespace}")
    print()
    print("Topology summary:")
    print("  (A01 base) Transit AS-2 via IX100↔IX101")
    print("  (A01 base) Stub AS-151 @ IX100, AS-152 @ IX101, AS-153 @ IX101")
    print("  (A05 ext)  AS-151: added host web-2 with WebService")
    print("  (A05 ext)  New AS-154 @ IX100 + IX101 + IX102, host web with WebService")
    print("  (A05 ext)  New IX-102 connecting AS-152 and AS-154")


if __name__ == '__main__':
    run()
