#!/usr/bin/env python3
# encoding: utf-8

# Adapted from examples/internet/B29_email_dns/email_realistic.py
# Uses KubernetesCompiler instead of DockerCompiler
#
# NOTE: The original email_realistic.py uses EmailService which is a Docker-only utility
# that generates docker-compose entries for mailserver/docker-mailserver containers.
# The network topology (BGP, DNS) is fully migrated to Kubernetes.
# The EmailService (actual mail server containers) is NOT migrated as it relies on
# Docker-specific compose template injection (attach_to_docker) with no K8s equivalent.

from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.services import DomainNameService, DomainNameCachingService
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.core import Emulator, Binding, Filter, Action
from seedemu.utilities import Makers
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


def create_realistic_network(emu):
    """Create network topology (adapted from email_realistic.py)"""

    base = Base()

    # Internet Exchanges
    ix_beijing = base.createInternetExchange(100)
    ix_shanghai = base.createInternetExchange(101)
    ix_guangzhou = base.createInternetExchange(102)
    ix_overseas = base.createInternetExchange(103)

    ix_beijing.getPeeringLan().setDisplayName('Beijing-IX-100')
    ix_shanghai.getPeeringLan().setDisplayName('Shanghai-IX-101')
    ix_guangzhou.getPeeringLan().setDisplayName('Guangzhou-IX-102')
    ix_overseas.getPeeringLan().setDisplayName('Global-IX-103')

    # Transit ASes (ISPs)
    Makers.makeTransitAs(base, 2, [100, 101, 102, 103],
                         [(100, 101), (101, 102), (102, 103), (100, 103)])
    Makers.makeTransitAs(base, 3, [100, 101], [(100, 101)])
    Makers.makeTransitAs(base, 4, [100, 102], [(100, 102)])

    # Mail provider stub ASes
    Makers.makeStubAsWithHosts(emu, base, 200, 102, 3)
    Makers.makeStubAsWithHosts(emu, base, 201, 101, 3)
    Makers.makeStubAsWithHosts(emu, base, 202, 103, 3)
    Makers.makeStubAsWithHosts(emu, base, 203, 103, 3)
    Makers.makeStubAsWithHosts(emu, base, 204, 101, 2)
    Makers.makeStubAsWithHosts(emu, base, 205, 100, 2)

    # Client networks
    Makers.makeStubAsWithHosts(emu, base, 150, 100, 7)
    Makers.makeStubAsWithHosts(emu, base, 151, 101, 4)
    Makers.makeStubAsWithHosts(emu, base, 152, 102, 4)
    Makers.makeStubAsWithHosts(emu, base, 153, 100, 5)

    return base


def configure_bgp_peering(ebgp):
    """Configure BGP peerings"""
    ebgp.addPrivatePeerings(100, [2], [3, 4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(100, [3], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(101, [2], [3], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(102, [2], [4], PeerRelationship.Peer)

    ebgp.addPrivatePeerings(102, [2], [200], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2], [201], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(103, [2], [202], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(103, [2], [203], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2], [204], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(100, [2], [205], PeerRelationship.Provider)

    ebgp.addPrivatePeerings(100, [2, 3], [150], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2], [151], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(102, [4], [152], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(100, [2], [153], PeerRelationship.Provider)


def configure_dns_system(emu, base):
    """Configure DNS system (authoritative + caches)"""

    dns = DomainNameService()

    # Root DNS
    dns.install('a-root-server').addZone('.').setMaster()
    dns.install('b-root-server').addZone('.')

    # TLD
    dns.install('ns-com').addZone('com.').setMaster()
    dns.install('ns-net').addZone('net.').setMaster()
    dns.install('ns-cn').addZone('cn.').setMaster()

    # Mail domains
    for domain, asn, ip in [
        ('qq.com',      200, '10.200.0.10'),
        ('163.com',     201, '10.201.0.10'),
        ('gmail.com',   202, '10.202.0.10'),
        ('outlook.com', 203, '10.203.0.10'),
        ('company.cn',  204, '10.204.0.10'),
        ('startup.net', 205, '10.205.0.10'),
    ]:
        short = domain.split('.')[0]
        ns_name = f'ns-{short}-{"com" if domain.endswith(".com") else ("cn" if domain.endswith(".cn") else "net")}'
        dns.install(ns_name).addZone(f'{domain}.').setMaster()
        dns.getZone(f'{domain}.').addRecord(f'@ A {ip}')
        dns.getZone(f'{domain}.').addRecord(f'@ MX 10 mail.{domain}.')
        dns.getZone(f'{domain}.').addRecord(f'mail A {ip}')

    # Bindings for authoritative servers
    emu.addBinding(Binding('^a-root-server$', action=Action.NEW, filter=Filter(asn=150, nodeName='dns-auth-root-a')))
    emu.addBinding(Binding('^b-root-server$', action=Action.NEW, filter=Filter(asn=150, nodeName='dns-auth-root-b')))
    emu.addBinding(Binding('^ns-com$',        action=Action.NEW, filter=Filter(asn=150, nodeName='dns-auth-com')))
    emu.addBinding(Binding('^ns-net$',        action=Action.NEW, filter=Filter(asn=150, nodeName='dns-auth-net')))
    emu.addBinding(Binding('^ns-cn$',         action=Action.NEW, filter=Filter(asn=150, nodeName='dns-auth-cn')))
    emu.addBinding(Binding('^ns-qq-com$',      action=Action.NEW, filter=Filter(asn=200, nodeName='dns-auth-qq')))
    emu.addBinding(Binding('^ns-163-com$',     action=Action.NEW, filter=Filter(asn=201, nodeName='dns-auth-163')))
    emu.addBinding(Binding('^ns-gmail-com$',   action=Action.NEW, filter=Filter(asn=202, nodeName='dns-auth-gmail')))
    emu.addBinding(Binding('^ns-outlook-com$', action=Action.NEW, filter=Filter(asn=203, nodeName='dns-auth-outlook')))
    emu.addBinding(Binding('^ns-company-cn$',  action=Action.NEW, filter=Filter(asn=204, nodeName='dns-auth-company')))
    emu.addBinding(Binding('^ns-startup-net$', action=Action.NEW, filter=Filter(asn=205, nodeName='dns-auth-startup')))

    # DNS caching layer
    ldns = DomainNameCachingService()
    cache = ldns.install('global-dns-cache')
    domain_forwarders = [
        ('qq.com.',      'ns-qq-com'),
        ('163.com.',     'ns-163-com'),
        ('gmail.com.',   'ns-gmail-com'),
        ('outlook.com.', 'ns-outlook-com'),
        ('company.cn.',  'ns-company-cn'),
        ('startup.net.', 'ns-startup-net'),
    ]
    cache.addForwardZone('com.', 'ns-com')
    cache.addForwardZone('net.', 'ns-net')
    cache.addForwardZone('cn.', 'ns-cn')
    for z, nsname in domain_forwarders:
        cache.addForwardZone(z, nsname)

    base.getAutonomousSystem(150).createHost('dns-cache').joinNetwork('net0', address='10.150.0.53')
    emu.addBinding(Binding('global-dns-cache', filter=Filter(asn=150, nodeName='dns-cache')))

    for asn in [200, 201, 202, 203, 204, 205]:
        vname = f'dns-cache-{asn}'
        c = ldns.install(vname)
        for z, nsname in domain_forwarders:
            c.addForwardZone(z, nsname)
        base.getAutonomousSystem(asn).createHost('dns-cache').joinNetwork('net0', address=f'10.{asn}.0.53')
        emu.addBinding(Binding(vname, filter=Filter(asn=asn, nodeName='dns-cache')))

    base.setNameServers(['10.150.0.53'])

    return dns, ldns


def run(dumpfile=None):
    emu = Emulator()

    base = create_realistic_network(emu)
    emu.addLayer(base)
    emu.addLayer(Routing())

    ebgp = Ebgp()
    configure_bgp_peering(ebgp)
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())

    dns, ldns = configure_dns_system(emu, base)
    emu.addLayer(dns)
    emu.addLayer(ldns)

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
    force_colocate       = os.environ.get("SEED_FORCE_COLOCATE", "false").strip().lower() in {"1", "true", "yes"}

    if force_colocate and not node_labels and cni_type in {"bridge", "host-local"}:
        single_node = os.environ.get(
            "SEED_SINGLE_NODE", f"{cluster_name}-control-plane"
        ).strip()
        colocate_asns = (
            list(range(100, 104))
            + [2, 3, 4,
               150, 151, 152, 153,
               200, 201, 202, 203, 204, 205]
        )
        node_labels = {
            str(asn): {"kubernetes.io/hostname": single_node}
            for asn in colocate_asns
        }
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
        output_dir = os.path.join(os.path.dirname(__file__), 'output_email_dns')
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(__file__), output_dir)

    emu.compile(k8s, output_dir, override=True)

    print(f"Compilation complete. Output generated in {output_dir}")
    print(f"Registry prefix: {registry_prefix}")
    print(f"Namespace: {namespace}")
    print()
    print("NOTE: EmailService (actual mail server containers) is Docker-only.")
    print("      The network topology (BGP routing + DNS) has been fully migrated.")
    print("      To run email servers on K8s, a dedicated K8s-native mail deployment")
    print("      (e.g., stalwart-mail or Helm chart) should be used instead.")


if __name__ == "__main__":
    run()
