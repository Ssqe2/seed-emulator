#!/usr/bin/env python3
# encoding: utf-8

# BGP Prefix Hijacking demonstration on Kubernetes
# Simplified from Y01_bgp_prefix_hijacking for K8s deployment
#
# Topology:
#   IX100 -- AS2 (transit) -- IX101
#   IX100: AS150 (victim, announces 10.150.0.0/24)
#   IX101: AS151, AS152 (legitimate peers)
#   IX101: AS199 (attacker, hijacks 10.150.0.0/24)

from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.core import Emulator
from seedemu.utilities import Makers
import os, sys

def run():
    emu  = Emulator()
    base = Base()
    ebgp = Ebgp()

    # Internet Exchanges
    ix100 = base.createInternetExchange(100)
    ix101 = base.createInternetExchange(101)
    ix100.getPeeringLan().setDisplayName('IX-West-100')
    ix101.getPeeringLan().setDisplayName('IX-East-101')

    # Transit AS (AS2): connects both IXes
    Makers.makeTransitAs(base, 2, [100, 101], [(100, 101)])

    # Stub ASes: victim and normal peers
    Makers.makeStubAsWithHosts(emu, base, 150, 100, 2)  # victim
    Makers.makeStubAsWithHosts(emu, base, 151, 101, 2)
    Makers.makeStubAsWithHosts(emu, base, 152, 101, 2)

    # Attacker AS (AS199): will hijack AS150's prefix
    as199 = base.createAutonomousSystem(199)
    as199.createNetwork('net0')
    as199.createHost('host-0').joinNetwork('net0')
    as199.createRouter('router0').joinNetwork('net0').joinNetwork('ix101')

    # Peering
    # Transit AS-2 peers at both IXPs (handled via private peerings below)
    ebgp.addPrivatePeerings(100, [2], [150], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2], [151, 152], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2], [199], PeerRelationship.Provider)

    # Layers
    emu.addLayer(base)
    emu.addLayer(Routing())
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())

    emu.render()

    registry_prefix     = os.environ.get("SEED_REGISTRY", "localhost:5001").strip()
    namespace           = os.environ.get("SEED_NAMESPACE", "seedemu").strip()
    cni_type            = os.environ.get("SEED_CNI_TYPE", "bridge").strip().lower()
    scheduling_strategy = os.environ.get("SEED_SCHEDULING_STRATEGY", "auto").strip().lower()
    image_pull_policy   = os.environ.get("SEED_IMAGE_PULL_POLICY", "Always").strip()

    k8s = KubernetesCompiler(
        registry_prefix=registry_prefix,
        namespace=namespace,
        use_multus=True,
        internetMapEnabled=False,
        scheduling_strategy=scheduling_strategy,
        cni_type=cni_type,
        image_pull_policy=image_pull_policy,
    )

    output_dir = os.environ.get("SEED_OUTPUT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output_bgp_hijacking")
    emu.compile(k8s, output_dir, override=True)
    print(f'Compiled to {output_dir}')

if __name__ == '__main__':
    run()
