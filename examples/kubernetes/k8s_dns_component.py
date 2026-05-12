#!/usr/bin/env python3
# encoding: utf-8

from seedemu.core import Emulator, Binding, Filter, Action
from seedemu.compiler import KubernetesCompiler
from seedemu.services import DomainNameService, DomainNameCachingService
from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
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

    # Transit AS-3
    as3 = base.createAutonomousSystem(3)
    as3.createNetwork('net0')
    as3.createRouter('r1').joinNetwork('net0').joinNetwork('ix100')
    as3.createRouter('r2').joinNetwork('net0').joinNetwork('ix101')

    # Stub ASes
    stub_config = {
        150: 'ix100', 151: 'ix100', 152: 'ix100', 153: 'ix100',
        160: 'ix101', 161: 'ix101', 162: 'ix101', 163: 'ix101', 164: 'ix101',
        165: 'ix101',  # extra for ns-syr-edu
    }
    for asn, ix in stub_config.items():
        a = base.createAutonomousSystem(asn)
        a.createNetwork('net0')
        a.createRouter('router0').joinNetwork('net0').joinNetwork(ix)
        a.createHost('host0').joinNetwork('net0')

    # Extra hosts
    base.getAutonomousSystem(152).createHost('local-dns-1').joinNetwork('net0', address='10.152.0.53')
    base.getAutonomousSystem(153).createHost('local-dns-2').joinNetwork('net0', address='10.153.0.53')
    base.getAutonomousSystem(160).createHost('client').joinNetwork('net0')

    # BGP Peering (private peering instead of RS peering for K8s)
    # Stub ASes at IX100 peer with AS3 as Provider
    ebgp.addPrivatePeerings(100, [3], [150, 151, 152, 153], PeerRelationship.Provider)
    # Stub ASes at IX101 peer with AS3 as Provider
    ebgp.addPrivatePeerings(101, [3], [160, 161, 162, 163, 164, 165], PeerRelationship.Provider)

    # DNS Layer (B01_dns_component)
    dns = DomainNameService()
    dns.install('a-root-server').addZone('.').setMaster()
    dns.install('b-root-server').addZone('.')
    dns.install('a-com-server').addZone('com.').setMaster()
    dns.install('b-com-server').addZone('com.')
    dns.install('a-net-server').addZone('net.')
    dns.install('a-edu-server').addZone('edu.')
    dns.install('ns-twitter-com').addZone('twitter.com.')
    dns.install('ns-google-com').addZone('google.com.')
    dns.install('ns-example-net').addZone('example.net.')
    dns.install('ns-syr-edu').addZone('syr.edu.')

    dns.getZone('twitter.com.').addRecord('@ A 1.1.1.1')
    dns.getZone('google.com.').addRecord('@ A 2.2.2.2')
    dns.getZone('example.net.').addRecord('@ A 3.3.3.3')
    dns.getZone('syr.edu.').addRecord('@ A 128.230.18.63')

    # Bind DNS nodes
    emu.addBinding(Binding('a-root-server', filter=Filter(asn=150, nodeName='host0'), action=Action.FIRST))
    emu.addBinding(Binding('b-root-server', filter=Filter(asn=151, nodeName='host0'), action=Action.FIRST))
    emu.addBinding(Binding('a-com-server', filter=Filter(asn=152, nodeName='host0'), action=Action.FIRST))
    emu.addBinding(Binding('b-com-server', filter=Filter(asn=153, nodeName='host0'), action=Action.FIRST))
    emu.addBinding(Binding('a-net-server', filter=Filter(asn=160, nodeName='host0'), action=Action.FIRST))
    emu.addBinding(Binding('a-edu-server', filter=Filter(asn=161, nodeName='host0'), action=Action.FIRST))
    emu.addBinding(Binding('ns-twitter-com', filter=Filter(asn=162, nodeName='host0'), action=Action.FIRST))
    emu.addBinding(Binding('ns-google-com', filter=Filter(asn=163, nodeName='host0'), action=Action.FIRST))
    emu.addBinding(Binding('ns-example-net', filter=Filter(asn=164, nodeName='host0'), action=Action.FIRST))
    emu.addBinding(Binding('ns-syr-edu', filter=Filter(asn=165, nodeName='host0'), action=Action.FIRST))

    # Local DNS caching
    ldns = DomainNameCachingService()
    global_dns_1 = ldns.install('global-dns-1')
    global_dns_2 = ldns.install('global-dns-2')
    emu.addBinding(Binding('global-dns-1', filter=Filter(asn=152, nodeName='local-dns-1')))
    emu.addBinding(Binding('global-dns-2', filter=Filter(asn=153, nodeName='local-dns-2')))
    global_dns_1.setNameServerOnNodesByAsns(asns=[160, 161, 162, 163, 164, 165])
    global_dns_2.setNameServerOnAllNodes()

    # Add layers
    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    emu.addLayer(dns)
    emu.addLayer(ldns)

    # Render and compile
    emu.render()

    from seedemu.compiler import SchedulingStrategy
    import json

    registry_prefix      = os.environ.get("SEED_REGISTRY", "localhost:5001")
    namespace            = os.environ.get("SEED_NAMESPACE", "seedemu")
    cluster_name         = os.environ.get("SEED_CLUSTER_NAME", "seedemu-kvtest")
    cni_type             = os.environ.get("SEED_CNI_TYPE", "bridge").strip().lower()
    cni_master_interface = os.environ.get("SEED_CNI_MASTER_INTERFACE", "eth0").strip()
    image_pull_policy    = os.environ.get("SEED_IMAGE_PULL_POLICY", "Always").strip()
    scheduling_strategy  = os.environ.get("SEED_SCHEDULING_STRATEGY", SchedulingStrategy.AUTO).strip().lower()
    force_colocate       = os.environ.get("SEED_FORCE_COLOCATE", "false").strip().lower() in {"1", "true", "yes"}

    node_labels = {}
    if force_colocate:
        single_node = os.environ.get("SEED_SINGLE_NODE", f"{cluster_name}-control-plane").strip()
        all_asns = [100, 101, 3, 150, 151, 152, 153, 160, 161, 162, 163, 164, 165]
        node_labels = {str(asn): {"kubernetes.io/hostname": single_node} for asn in all_asns}
        scheduling_strategy = SchedulingStrategy.CUSTOM

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
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output_dns_component')
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), output_dir)

    emu.compile(k8s, output_dir, override=True)
    print(f"Compilation complete. Output in {output_dir}")
    print(f"Registry prefix: {registry_prefix}")
    print(f"Namespace: {namespace}")

if __name__ == '__main__':
    run()
