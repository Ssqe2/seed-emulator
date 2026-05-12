#!/usr/bin/env python3
# encoding: utf-8

"""
K8s DHCP Migration - Migrate B20_dhcp example to Kubernetes compiler.
Uses a simplified mini-internet topology with DHCP servers and clients.
"""

from seedemu.core import Emulator, Binding, Filter, Action
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.services import DHCPService
from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.utilities import Makers
import os

def run():
    emu   = Emulator()
    base  = Base()
    routing = Routing()
    ebgp  = Ebgp()
    ibgp  = Ibgp()
    ospf  = Ospf()

    # Internet Exchanges
    base.createInternetExchange(100)
    base.createInternetExchange(101)

    # Transit AS-2
    Makers.makeTransitAs(base, 2, [100, 101], [(100, 101)])

    # Stub ASes: AS-151 at IX100, AS-161 at IX101
    Makers.makeStubAsWithHosts(emu, base, 151, 100, 2)
    Makers.makeStubAsWithHosts(emu, base, 161, 101, 2)

    # BGP Peering
    ebgp.addPrivatePeerings(100, [2], [151], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2], [161], PeerRelationship.Provider)

    # ---- DHCP Service ----
    dhcp = DHCPService()

    # Install DHCP servers
    dhcp.install('dhcp-01').setIpRange(125, 140)
    dhcp.install('dhcp-02')

    emu.getVirtualNode('dhcp-01').setDisplayName('DHCP Server 1')
    emu.getVirtualNode('dhcp-02').setDisplayName('DHCP Server 2')

    # Create DHCP server hosts
    as151 = base.getAutonomousSystem(151)
    as151.createHost('dhcp-server-01').joinNetwork('net0')

    as161 = base.getAutonomousSystem(161)
    as161.createHost('dhcp-server-02').joinNetwork('net0')

    # Bind DHCP servers
    emu.addBinding(Binding('dhcp-01', filter=Filter(asn=151, nodeName='dhcp-server-01')))
    emu.addBinding(Binding('dhcp-02', filter=Filter(asn=161, nodeName='dhcp-server-02')))

    # Create DHCP clients (address="dhcp")
    as151.createHost('dhcp-client-01').joinNetwork('net0', address='dhcp')
    as151.createHost('dhcp-client-02').joinNetwork('net0', address='dhcp')

    as161.createHost('dhcp-client-03').joinNetwork('net0', address='dhcp')
    as161.createHost('dhcp-client-04').joinNetwork('net0', address='dhcp')

    # Add layers
    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    emu.addLayer(dhcp)

    # Render and compile
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

    output_dir = os.environ.get("SEED_OUTPUT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output_dhcp")
    emu.compile(k8s, output_dir, override=True)
    print(f"Compilation complete. Output in {output_dir}")

if __name__ == '__main__':
    run()
