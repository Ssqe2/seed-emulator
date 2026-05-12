#!/usr/bin/env python3
# encoding: utf-8

# Tor/Darknet demonstration on Kubernetes
# Adapted from examples/internet/B23_darknet_tor/darknet_tor.py
#
# NOTE ON K8S COMPATIBILITY:
# TorService uses addBuildCommand() to install Tor packages during image build.
# KubernetesCompiler is a subclass of Docker and emits the same Dockerfile, so
# addBuildCommand() IS supported. The Tor daemon binds to pod IPs provided by
# the overlay network, which works the same as Docker bridge networking.
#
# CAVEATS:
# 1. Tor build requires outbound Internet access at image build time to
#    download packages (tor, obfs4proxy, etc.).
# 2. The hidden-service node needs to reach the webserver pod; in K8s this
#    works via the overlay network just like Docker.
# 3. Directory-authority (DA) nodes exchange keys during build; all DA nodes
#    must be compiled into the same image set so their fingerprints are
#    consistent. This is handled automatically by TorService.install().
# 4. No Docker-specific features (expose ports, docker networks) are used —
#    all Tor communication uses TCP sockets over the pod overlay network.

import random
from seedemu.core import Emulator, Binding, Filter, Action
from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.services import WebService, TorService, TorNodeType
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.utilities import Makers
import os


def run():
    emu  = Emulator()
    base = Base()
    ebgp = Ebgp()

    ###########################################################################
    # Mini-internet topology (mirrors B00 / k8s_botnet topology)
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

    Makers.makeTransitAs(base, 2, [100, 101, 102, 105],
           [(100, 101), (101, 102), (100, 105)])
    Makers.makeTransitAs(base, 3, [100, 103, 104, 105],
           [(100, 103), (100, 105), (103, 105), (103, 104)])
    Makers.makeTransitAs(base, 4, [100, 102, 104],
           [(100, 104), (102, 104)])
    Makers.makeTransitAs(base, 11, [102, 105], [(102, 105)])
    Makers.makeTransitAs(base, 12, [101, 104], [(101, 104)])

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

    ebgp.addPrivatePeerings(100, [2], [3, 4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(100, [3], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(102, [2], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(104, [3], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(105, [2], [3], PeerRelationship.Peer)

    ebgp.addPrivatePeerings(100, [2, 3, 4], [150, 151], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2],        [152, 153], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(102, [2, 4],     [154],      PeerRelationship.Provider)
    ebgp.addPrivatePeerings(103, [3],        [160, 161], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(104, [3, 4],     [162, 163], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(105, [2, 3],     [164, 170, 171], PeerRelationship.Provider)

    ###########################################################################
    # Secret web server (hidden behind Tor hidden service)
    html = """
<html><body>
<h1>This is the secret web server!</h1>
</body></html>
"""
    web = WebService()
    web.install("webserver")
    emu.getVirtualNode('webserver').setDisplayName('Tor-webserver') \
            .setFile(content=html, path="/var/www/html/hello.html")

    ###########################################################################
    # Tor service layer
    tor = TorService()

    vnodes = {
        "da-1":          TorNodeType.DA,
        "da-2":          TorNodeType.DA,
        "da-3":          TorNodeType.DA,
        "da-4":          TorNodeType.DA,
        "da-5":          TorNodeType.DA,
        "client-1":      TorNodeType.CLIENT,
        "client-2":      TorNodeType.CLIENT,
        "relay-1":       TorNodeType.RELAY,
        "relay-2":       TorNodeType.RELAY,
        "relay-3":       TorNodeType.RELAY,
        "relay-4":       TorNodeType.RELAY,
        "exit-1":        TorNodeType.EXIT,
        "exit-2":        TorNodeType.EXIT,
        "hidden-service": TorNodeType.HS,
    }

    for name, nodeType in vnodes.items():
        if nodeType == TorNodeType.HS:
            tor.install(name).setRole(nodeType).linkByVnode("webserver", 80)
        else:
            tor.install(name).setRole(nodeType)
        emu.getVirtualNode(name).setDisplayName("Tor-{}".format(name))

    ###########################################################################
    # Bind virtual nodes to physical nodes
    as_list = [150, 151, 152, 153, 154, 160, 161, 162, 163, 164, 170, 171]
    random.seed(42)  # reproducible
    for name in vnodes:
        asn = random.choice(as_list)
        emu.addBinding(Binding(name, filter=Filter(asn=asn), action=Action.NEW))

    emu.addBinding(Binding("webserver", filter=Filter(asn=170), action=Action.NEW))

    ###########################################################################
    emu.addLayer(base)
    emu.addLayer(Routing())
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())
    emu.addLayer(web)
    emu.addLayer(tor)

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

    output_dir = os.environ.get("SEED_OUTPUT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output_darknet_tor")
    emu.compile(k8s, output_dir, override=True)
    print(f'[k8s_darknet_tor] Compiled to {output_dir}')


if __name__ == '__main__':
    run()
