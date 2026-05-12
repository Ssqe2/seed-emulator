#!/usr/bin/env python3
# encoding: utf-8

# K8s adaptation of examples/internet/B02_mini_internet_with_dns
# Combines mini_internet topology (B00) with DNS infrastructure (B01)
# using KubernetesCompiler instead of DockerCompiler.

from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.core import Emulator, Binding, Filter, Action
from seedemu.utilities import Makers
from seedemu.services import DomainNameService, DomainNameCachingService
from seedemu.services.DomainNameCachingService import DomainNameCachingServer
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


def run(hosts_per_as=2):
    # -------------------------------------------------------------------------
    # Initialize Emulator and layers
    # -------------------------------------------------------------------------
    emu  = Emulator()
    base = Base()
    ebgp = Ebgp()

    # -------------------------------------------------------------------------
    # Internet Exchanges  (same as B00 mini_internet)
    # -------------------------------------------------------------------------
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
    ix105.getPeeringLan().setDisplayName('Huston-105')

    # -------------------------------------------------------------------------
    # Transit ASes
    # -------------------------------------------------------------------------
    Makers.makeTransitAs(base, 2, [100, 101, 102, 105],
           [(100, 101), (101, 102), (100, 105)])

    Makers.makeTransitAs(base, 3, [100, 103, 104, 105],
           [(100, 103), (100, 105), (103, 105), (103, 104)])

    Makers.makeTransitAs(base, 4, [100, 102, 104],
           [(100, 104), (102, 104)])

    Makers.makeTransitAs(base, 11, [102, 105], [(102, 105)])
    Makers.makeTransitAs(base, 12, [101, 104], [(101, 104)])

    # -------------------------------------------------------------------------
    # Stub ASes  (same as B00 mini_internet)
    # -------------------------------------------------------------------------
    Makers.makeStubAsWithHosts(emu, base, 150, 100, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 151, 100, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 152, 101, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 153, 101, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 154, 102, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 160, 103, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 161, 103, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 162, 103, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 163, 104, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 164, 104, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 170, 105, hosts_per_as)
    Makers.makeStubAsWithHosts(emu, base, 171, 105, hosts_per_as)

    # -------------------------------------------------------------------------
    # BGP Peering  (same as B00 mini_internet)
    # -------------------------------------------------------------------------
    ebgp.addPrivatePeerings(100, [2], [3, 4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(100, [3], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(102, [2], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(104, [3], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(105, [2], [3], PeerRelationship.Peer)

    ebgp.addPrivatePeerings(100, [2],  [150, 151], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(100, [3],  [150],      PeerRelationship.Provider)

    ebgp.addPrivatePeerings(101, [2],  [12],        PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [12], [152, 153],  PeerRelationship.Provider)

    ebgp.addPrivatePeerings(102, [2, 4], [11, 154], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(102, [11],   [154],     PeerRelationship.Provider)

    ebgp.addPrivatePeerings(103, [3], [160, 161, 162], PeerRelationship.Provider)

    ebgp.addPrivatePeerings(104, [3, 4], [12],  PeerRelationship.Provider)
    ebgp.addPrivatePeerings(104, [4],    [163], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(104, [12],   [164], PeerRelationship.Provider)

    ebgp.addPrivatePeerings(105, [3],  [11, 170], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(105, [11], [171],     PeerRelationship.Provider)

    # -------------------------------------------------------------------------
    # DNS Infrastructure  (from B01 dns_component)
    # -------------------------------------------------------------------------
    dns = DomainNameService()

    # Root zone (two servers)
    dns.install('a-root-server').addZone('.').setMaster()
    dns.install('b-root-server').addZone('.')

    # TLD zones
    dns.install('a-com-server').addZone('com.').setMaster()
    dns.install('b-com-server').addZone('com.')
    dns.install('a-net-server').addZone('net.')
    dns.install('a-edu-server').addZone('edu.')

    # Second-level zones
    dns.install('ns-twitter-com').addZone('twitter.com.')
    dns.install('ns-google-com').addZone('google.com.')
    dns.install('ns-example-net').addZone('example.net.')
    dns.install('ns-syr-edu').addZone('syr.edu.')

    # DNS records
    dns.getZone('twitter.com.').addRecord('@ A 1.1.1.1')
    dns.getZone('google.com.').addRecord('@ A 2.2.2.2')
    dns.getZone('example.net.').addRecord('@ A 3.3.3.3')
    dns.getZone('syr.edu.').addRecord('@ A 128.230.18.63')

    # Display names (for visualization)
    emu.getVirtualNode('a-root-server').setDisplayName('Root-A')
    emu.getVirtualNode('b-root-server').setDisplayName('Root-B')
    emu.getVirtualNode('a-com-server').setDisplayName('COM-A')
    emu.getVirtualNode('b-com-server').setDisplayName('COM-B')
    emu.getVirtualNode('a-net-server').setDisplayName('NET')
    emu.getVirtualNode('a-edu-server').setDisplayName('EDU')
    emu.getVirtualNode('ns-twitter-com').setDisplayName('twitter.com')
    emu.getVirtualNode('ns-google-com').setDisplayName('google.com')
    emu.getVirtualNode('ns-example-net').setDisplayName('example.net')
    emu.getVirtualNode('ns-syr-edu').setDisplayName('syr.edu')

    # Bind DNS virtual nodes to physical nodes in mini_internet ASes
    # (mirrors the bindings in B02 mini_internet_with_dns.py)
    emu.addBinding(Binding('a-root-server', filter=Filter(asn=171), action=Action.FIRST))
    emu.addBinding(Binding('b-root-server', filter=Filter(asn=150), action=Action.FIRST))
    emu.addBinding(Binding('a-com-server',  filter=Filter(asn=151), action=Action.FIRST))
    emu.addBinding(Binding('b-com-server',  filter=Filter(asn=152), action=Action.FIRST))
    emu.addBinding(Binding('a-net-server',  filter=Filter(asn=152), action=Action.FIRST))
    emu.addBinding(Binding('a-edu-server',  filter=Filter(asn=153), action=Action.FIRST))
    emu.addBinding(Binding('ns-twitter-com', filter=Filter(asn=161), action=Action.FIRST))
    emu.addBinding(Binding('ns-google-com',  filter=Filter(asn=162), action=Action.FIRST))
    emu.addBinding(Binding('ns-example-net', filter=Filter(asn=163), action=Action.FIRST))
    emu.addBinding(Binding('ns-syr-edu',     filter=Filter(asn=164), action=Action.FIRST))

    # -------------------------------------------------------------------------
    # Local DNS Caching Servers  (from B02)
    # Create new hosts in AS-152 and AS-153 to host local caching resolvers.
    # -------------------------------------------------------------------------
    ldns = DomainNameCachingService()
    global_dns_1: DomainNameCachingServer = ldns.install('global-dns-1')
    global_dns_2: DomainNameCachingServer = ldns.install('global-dns-2')

    emu.getVirtualNode('global-dns-1').setDisplayName('Global DNS-1')
    emu.getVirtualNode('global-dns-2').setDisplayName('Global DNS-2')

    base_layer: Base = base  # alias for clarity
    as152 = base_layer.getAutonomousSystem(152)
    as152.createHost('local-dns-1').joinNetwork('net0', address='10.152.0.53')
    as153 = base_layer.getAutonomousSystem(153)
    as153.createHost('local-dns-2').joinNetwork('net0', address='10.153.0.53')

    emu.addBinding(Binding('global-dns-1', filter=Filter(asn=152, nodeName='local-dns-1')))
    emu.addBinding(Binding('global-dns-2', filter=Filter(asn=153, nodeName='local-dns-2')))

    # Set resolvers: DNS-1 serves AS-160 and AS-170; DNS-2 is the default for all nodes
    global_dns_1.setNameServerOnNodesByAsns(asns=[160, 170])
    global_dns_2.setNameServerOnAllNodes()

    # -------------------------------------------------------------------------
    # Assemble layers
    # -------------------------------------------------------------------------
    emu.addLayer(base)
    emu.addLayer(Routing())
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())
    emu.addLayer(dns)
    emu.addLayer(ldns)

    emu.render()

    # -------------------------------------------------------------------------
    # Kubernetes Compilation  (same pattern as k8s_mini_internet.py)
    # -------------------------------------------------------------------------
    registry_prefix      = os.environ.get("SEED_REGISTRY", "localhost:5001")
    namespace            = os.environ.get("SEED_NAMESPACE", "seedemu")
    cluster_name         = os.environ.get("SEED_CLUSTER_NAME", "seedemu-kvtest")
    cni_type             = os.environ.get("SEED_CNI_TYPE", "bridge").strip().lower()
    cni_master_interface = os.environ.get("SEED_CNI_MASTER_INTERFACE", "eth0").strip()
    image_pull_policy    = os.environ.get("SEED_IMAGE_PULL_POLICY", "Always").strip()
    scheduling_strategy  = os.environ.get("SEED_SCHEDULING_STRATEGY", SchedulingStrategy.AUTO).strip().lower()
    node_labels          = _parse_node_labels_json(os.environ.get("SEED_NODE_LABELS_JSON", ""))
    force_colocate       = os.environ.get("SEED_FORCE_COLOCATE", "false").strip().lower() in {"1", "true", "yes"}

    if force_colocate and not node_labels and cni_type in {"bridge", "host-local"}:
        single_node = os.environ.get("SEED_SINGLE_NODE", f"{cluster_name}-control-plane").strip()
        colocate_asns = (
            list(range(100, 106))
            + [2, 3, 4, 11, 12]
            + [150, 151, 152, 153, 154, 160, 161, 162, 163, 164, 170, 171]
        )
        node_labels = {str(asn): {"kubernetes.io/hostname": single_node} for asn in colocate_asns}
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
        output_dir = os.path.join(os.path.dirname(__file__), 'output_mini_internet_with_dns')
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(__file__), output_dir)

    emu.compile(k8s, output_dir, override=True)

    print(f"Compilation complete. Output generated in {output_dir}")
    print(f"Registry prefix: {registry_prefix}")
    print(f"Namespace: {namespace}")


if __name__ == "__main__":
    hosts_per_as_env = os.environ.get("SEED_HOSTS_PER_AS", "2")
    try:
        hosts_per_as = int(hosts_per_as_env)
    except ValueError as exc:
        raise ValueError(f"Invalid SEED_HOSTS_PER_AS: {hosts_per_as_env}") from exc
    run(hosts_per_as=hosts_per_as)
