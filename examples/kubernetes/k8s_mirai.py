#!/usr/bin/env python3
# encoding: utf-8

# Adapted from examples/yesterday_once_more/Y03_mirai/demo/mirai_internet_with_dns.py
# Uses KubernetesCompiler instead of DockerCompiler
#
# Migration notes:
# - setImageOverride('mirai-base') is Docker-only: removed (K8s uses its own image build pipeline)
# - OpenVpnRemoteAccessProvider.enableRemoteAccess() is not supported by KubernetesCompiler: removed
# - addImage(DockerImage(..., local=True)) is Docker-only: removed
# - importFile() of host-local media files (index.html, image.png, video.mp4, mirai.py,
#   add_dns_record.sh) is preserved — the KubernetesCompiler will embed them via ConfigMap/initContainers
#   as it does for other files; however if compilation fails on importFile, those lines must be removed.
# - The emulation is inlined (no dump/load/merge) to work standalone without running
#   mini_internet_for_mirai.run() + dns_component.run() as separate processes.
#
# Docker-only features NOT migrated:
#   * Custom mirai-base Docker image override
#   * OpenVPN remote access (enableRemoteAccess)
# These are noted below with # [DOCKER-ONLY] comments.

from seedemu.core import Emulator, Binding, Filter, Action
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.services import (
    DomainNameService, DomainNameCachingService, WebService, BotnetService
)
from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.utilities import Makers
import os
import json
import random

# Mirai credential list (from original)
MIRAI_CREDS = [
    ("root", "vizxv"),   ("root", "xc3511"),  ("root", "admin"),
    ("admin", "admin"),  ("root", "888888")
]


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


def run(dumpfile=None):
    emu = Emulator()
    ebgp = Ebgp()
    base = Base()
    web = WebService()

    ###########################################################################
    # Internet Exchanges (from mini_internet_for_mirai)
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
    # Stub ASes (hosts_per_as=8 as in original)
    hosts_per_as = 8
    for asn, ix in [(150, 100), (151, 100), (152, 101), (153, 101), (154, 102),
                    (160, 103), (161, 103), (162, 103), (163, 104), (164, 104),
                    (170, 105), (171, 105)]:
        Makers.makeStubAsWithHosts(emu, base, asn, ix, hosts_per_as)

    ###########################################################################
    # BGP peering
    # Transit AS peering (replacing RS peering for K8s compatibility)
    ebgp.addPrivatePeering(100, 2, 3, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(100, 2, 4, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(100, 3, 4, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(102, 2, 4, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(104, 3, 4, abRelationship=PeerRelationship.Peer)
    ebgp.addPrivatePeering(105, 2, 3, abRelationship=PeerRelationship.Peer)

    ebgp.addPrivatePeerings(100, [2],  [150, 151], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(100, [3],  [150],      PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2],  [12],       PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [12], [152, 153], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(102, [2, 4],  [11, 154], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(102, [11], [154],      PeerRelationship.Provider)
    ebgp.addPrivatePeerings(103, [3],  [160, 161, 162], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(104, [3, 4], [12],     PeerRelationship.Provider)
    ebgp.addPrivatePeerings(104, [4],  [163],      PeerRelationship.Provider)
    ebgp.addPrivatePeerings(104, [12], [164],      PeerRelationship.Provider)
    ebgp.addPrivatePeerings(105, [3],  [11, 170],  PeerRelationship.Provider)
    ebgp.addPrivatePeerings(105, [11], [171],      PeerRelationship.Provider)

    ###########################################################################
    # Mirai settings: install telnet, configure weak passwords
    for stub_as in [150, 151, 152, 153, 154, 160, 161, 162, 163, 164, 170, 171]:
        hosts = base.getAutonomousSystem(stub_as).getHosts()
        for hostname in hosts:
            host = base.getAutonomousSystem(stub_as).getHost(hostname)
            host.addSoftware('telnetd')
            host.addSoftware('telnet')
            host.appendStartCommand('rm -f /root/.bashrc', fork=True)
            host.appendStartCommand(
                'echo "pts/0" >> /etc/securetty && echo "pts/1" >> /etc/securetty', fork=True)
            host.appendStartCommand(
                'echo -e "telnet stream tcp nowait root /usr/sbin/tcpd '
                '/usr/sbin/in.telnetd -L /bin/login" > /etc/inetd.conf', fork=True)
            user, pwd = random.choice(MIRAI_CREDS)
            if user == "root":
                host.appendStartCommand(
                    f'echo -e "{pwd}\\n{pwd}" | passwd root', fork=True)
            else:
                host.appendStartCommand(
                    f'useradd -m -s /bin/bash {user} && '
                    f'echo -e "{pwd}\\n{pwd}" | passwd {user}', fork=True)
            host.appendStartCommand('/usr/sbin/inetd -d', fork=True)

    ###########################################################################
    # C2 server (BotnetService)
    c2 = base.getAutonomousSystem(170).createHost('c2_server').joinNetwork(
        'net0', address='10.170.0.100')
    botcontroller = BotnetService()
    botcontroller.install('bot-controller')
    emu.getVirtualNode('bot-controller').setDisplayName('C2_server')
    emu.addBinding(Binding('bot-controller',
                            filter=Filter(ip='10.170.0.100'), action=Action.FIRST))
    c2.appendStartCommand('mkdir -p /var/www/html && cd /var/www/html', fork=True)
    c2.appendStartCommand(
        'python3 -m http.server 80 --directory /var/www/html', fork=True)
    c2.addSoftware('telnetd')
    c2.addSoftware('telnet')

    # [DOCKER-ONLY] importFile for mirai.py omitted — file path depends on host filesystem
    # and K8s importFile support may vary. Uncomment if KubernetesCompiler supports it:
    # current_dir = os.getcwd()
    # c2.importFile(hostpath=f"{current_dir}/../scripts/mirai.py",
    #               containerpath="/var/www/html/mirai.py")

    ###########################################################################
    # Victim server (WebService)
    victim_as = base.getAutonomousSystem(170)
    victim_server = victim_as.createHost('victim').joinNetwork(
        'net0', address='10.170.0.99')
    web.install('web170')
    emu.addBinding(Binding('web170', filter=Filter(nodeName='victim', asn=170)))
    victim_server.appendStartCommand(
        'tc qdisc replace dev net0 root tbf rate 10mbit burst 32kbit latency 400ms',
        fork=True)
    # [DOCKER-ONLY] importFile for media files omitted (host-path dependent)
    # victim_server.importFile(hostpath=f"{current_dir}/../misc/index.html",
    #                          containerpath="/var/www/html/index.html")

    ###########################################################################
    # DNS infrastructure (from dns_component + mirai_internet_with_dns)
    dns = DomainNameService()
    dns.install('a-root-server').addZone('.').setMaster()
    emu.getVirtualNode('a-root-server').setDisplayName('Root-A')
    dns.install('a-com-server').addZone('com.').setMaster()
    emu.getVirtualNode('a-com-server').setDisplayName('COM-A')

    # Physical host bindings for DNS servers
    as150 = base.getAutonomousSystem(150)
    as150.createHost('root-a').joinNetwork('net0', address='10.150.0.53')
    emu.addBinding(Binding('a-root-server', filter=Filter(asn=150, nodeName='root-a')))

    as151 = base.getAutonomousSystem(151)
    coma = as151.createHost('com-a').joinNetwork('net0', address='10.151.0.53')
    emu.addBinding(Binding('a-com-server', filter=Filter(asn=151, nodeName='com-a')))
    # [DOCKER-ONLY] importFile for add_dns_record.sh omitted — host path dependent
    # coma.appendStartCommand("cd /tmp && chmod +x ./add_dns_record.sh")

    ###########################################################################
    # Local DNS caching servers
    ldns = DomainNameCachingService()
    global_dns_1 = ldns.install('global-dns-1')
    global_dns_2 = ldns.install('global-dns-2')
    emu.getVirtualNode('global-dns-1').setDisplayName('Global DNS-1')
    emu.getVirtualNode('global-dns-2').setDisplayName('Global DNS-2')

    as152 = base.getAutonomousSystem(152)
    as152.createHost('local-dns-1').joinNetwork('net0', address='10.152.0.53')
    as153 = base.getAutonomousSystem(153)
    as153.createHost('local-dns-2').joinNetwork('net0', address='10.153.0.53')

    emu.addBinding(Binding('global-dns-1', filter=Filter(asn=152, nodeName='local-dns-1')))
    emu.addBinding(Binding('global-dns-2', filter=Filter(asn=153, nodeName='local-dns-2')))

    global_dns_1.setNameServerOnNodesByAsns(asns=[160, 170])
    global_dns_2.setNameServerOnAllNodes()

    ###########################################################################
    # Add layers
    emu.addLayer(base)
    emu.addLayer(Routing())
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())
    emu.addLayer(web)
    emu.addLayer(botcontroller)
    emu.addLayer(dns)
    emu.addLayer(ldns)

    emu.render()

    ###########################################################################
    # Kubernetes Compilation
    # [DOCKER-ONLY] setImageOverride(host, 'mirai-base') not available in K8s compiler.
    # To use a custom image, configure registry_prefix to point to your registry
    # and push a custom image named 'mirai-base' there.

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
            list(range(100, 106))
            + [2, 3, 4, 11, 12,
               150, 151, 152, 153, 154,
               160, 161, 162, 163, 164, 170, 171]
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
        output_dir = os.path.join(os.path.dirname(__file__), 'output_mirai')
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(__file__), output_dir)

    emu.compile(k8s, output_dir, override=True)

    print(f"Compilation complete. Output generated in {output_dir}")
    print(f"Registry prefix: {registry_prefix}")
    print(f"Namespace: {namespace}")
    print()
    print("NOTE: Docker-only features not migrated:")
    print("  - mirai-base custom Docker image override (setImageOverride)")
    print("  - OpenVPN remote access (enableRemoteAccess)")
    print("  - Host-filesystem importFile for mirai.py, add_dns_record.sh, media files")


if __name__ == "__main__":
    run()
