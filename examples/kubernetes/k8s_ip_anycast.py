#!/usr/bin/env python3
# encoding: utf-8

# IP Anycast demonstration on Kubernetes
# Adapted from examples/internet/B24_ip_anycast/ip_anycast.py
#
# Topology: based on mini_internet (B00), with AS180 added to demonstrate anycast.
# AS180 has two disjoint networks both using prefix 10.180.0.0/24,
# each hosted in different IXes, so packets get routed to the nearest one.

from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.core import Emulator
from seedemu.utilities import Makers
import os

def run():
    emu   = Emulator()
    base  = Base()
    ebgp  = Ebgp()

    ###########################################################################
    # Internet Exchanges (mirrors B00 mini_internet topology)
    ix100 = base.createInternetExchange(100)
    ix101 = base.createInternetExchange(101)
    ix102 = base.createInternetExchange(102)
    ix103 = base.createInternetExchange(103)
    ix104 = base.createInternetExchange(104)
    ix105 = base.createInternetExchange(105)

    ix100.getPeeringLan().setDisplayName('NYC-100')
    ix101.getPeeringLan().setDisplayName('San Jose-101')
    ix102.getPeeringLan().setDisplayName('Chicago-102')
    ix103.getPeeringLan().setDisplayName('Miami-103')
    ix104.getPeeringLan().setDisplayName('Boston-104')
    ix105.getPeeringLan().setDisplayName('Houston-105')

    ###########################################################################
    # Transit ASes
    Makers.makeTransitAs(base, 2, [100, 101, 102, 105],
           [(100, 101), (101, 102), (100, 105)])
    Makers.makeTransitAs(base, 3, [100, 103, 104, 105],
           [(100, 103), (100, 105), (103, 105), (103, 104)])
    Makers.makeTransitAs(base, 4, [100, 102, 104],
           [(100, 104), (102, 104)])
    Makers.makeTransitAs(base, 11, [102, 105], [(102, 105)])
    Makers.makeTransitAs(base, 12, [101, 104], [(101, 104)])

    ###########################################################################
    # Stub ASes
    Makers.makeStubAsWithHosts(emu, base, 150, 100, 2)
    Makers.makeStubAsWithHosts(emu, base, 151, 100, 2)
    Makers.makeStubAsWithHosts(emu, base, 152, 101, 2)
    Makers.makeStubAsWithHosts(emu, base, 153, 101, 2)
    Makers.makeStubAsWithHosts(emu, base, 154, 102, 2)
    Makers.makeStubAsWithHosts(emu, base, 160, 103, 2)
    Makers.makeStubAsWithHosts(emu, base, 161, 103, 2)
    Makers.makeStubAsWithHosts(emu, base, 162, 104, 2)
    Makers.makeStubAsWithHosts(emu, base, 163, 104, 2)
    Makers.makeStubAsWithHosts(emu, base, 164, 105, 2)
    Makers.makeStubAsWithHosts(emu, base, 170, 105, 2)
    Makers.makeStubAsWithHosts(emu, base, 171, 105, 2)

    ###########################################################################
    # BGP peering
    # Transit AS peering (replacing RS peering for K8s compatibility)
    ebgp.addPrivatePeering(100, 2, 3, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(100, 2, 4, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(100, 3, 4, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(102, 2, 4, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(104, 3, 4, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(105, 2, 3, abRelationship=PeerRelationship.Peer)

    ebgp.addPrivatePeerings(100, [2, 3, 4], [150, 151], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2],        [152, 153], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(102, [2, 4],     [154],      PeerRelationship.Provider)
    ebgp.addPrivatePeerings(103, [3],        [160, 161], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(104, [3, 4],     [162, 163], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(105, [2, 3],     [164, 170, 171], PeerRelationship.Provider)

    ###########################################################################
    # AS180 — Anycast demonstration
    # Two disjoint networks with the SAME prefix 10.180.0.0/24
    # Each announces the same prefix from a different location in the internet.
    as180 = base.createAutonomousSystem(180)
    as180.createNetwork('net0', '10.180.0.0/24')
    as180.createNetwork('net1', '10.180.0.0/24')

    # Same IP on both hosts — anycast!
    as180.createHost('host-0').joinNetwork('net0', address='10.180.0.100')
    as180.createHost('host-1').joinNetwork('net1', address='10.180.0.100')

    # Router0 at IX100 — peers with AS3 and AS4
    as180.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    ebgp.addPrivatePeerings(100, [3, 4], [180], PeerRelationship.Provider)

    # Router1 at IX105 — peers with AS2 and AS3
    as180.createRouter('router1').joinNetwork('net1').joinNetwork('ix105')
    ebgp.addPrivatePeerings(105, [2, 3], [180], PeerRelationship.Provider)

    ###########################################################################
    # Layers
    emu.addLayer(base)
    emu.addLayer(Routing())
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())

    emu.render()

    # KubernetesCompiler automatically sets selfManagedNetwork=True internally
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

    output_dir = os.environ.get("SEED_OUTPUT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output_ip_anycast")
    emu.compile(k8s, output_dir, override=True)
    print(f'[k8s_ip_anycast] Compiled to {output_dir}')

if __name__ == '__main__':
    run()
