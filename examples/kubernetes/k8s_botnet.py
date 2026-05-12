#!/usr/bin/env python3
# encoding: utf-8

# Botnet demonstration on Kubernetes
# Adapted from examples/internet/B22_botnet/botnet_basic.py
#
# NOTE ON K8S COMPATIBILITY:
# BotnetService/BotnetClientService use addBuildCommand() (pip3 install byob
# during image build) which IS supported by KubernetesCompiler (it is a
# subclass of Docker and emits the same Dockerfile). The main caveat is that
# BYOB (the underlying C2 framework) requires outbound Internet access during
# image build to install Python packages; ensure your registry/build host has
# Internet access. No Docker-specific features (docker networks, expose ports)
# are used — the service relies only on TCP sockets, which work in K8s pods.
#
# KNOWN LIMITATION:
# BotnetServer opens a raw TCP control port. In K8s the controller IP is
# assigned via pod overlay network, not a static host IP. The Botnet server
# resolves the controller address at runtime from the emulator's virtual node
# binding, so as long as the virtual node 'bot-controller' is bound to a
# physical host in the emulator, this works the same way as in Docker.

import random
from seedemu.core import Emulator, Binding, Filter, Action
from seedemu.services import BotnetService, BotnetClientService
from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.utilities import Makers
import os

def run():
    emu  = Emulator()
    base = Base()
    ebgp = Ebgp()

    ###########################################################################
    # Mini-internet topology (mirrors B00)
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
    # Botnet service layers
    bot       = BotnetService()
    botClient = BotnetClientService()

    # Bot controller virtual node
    bot.install('bot-controller')
    emu.getVirtualNode('bot-controller').setDisplayName('Bot-Controller')

    # Install ddos helper script (optional; skip if file not present)
    ddos_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', 'internet', 'B22_botnet', 'ddos.py')
    if os.path.exists(ddos_path):
        with open(ddos_path, 'r') as f:
            emu.getVirtualNode('bot-controller').setFile(
                content=f.read(), path='/tmp/ddos.py')

    # 6 bot client nodes
    for counter in range(6):
        vname = 'bot-node-%.3d' % counter
        botClient.install(vname).setServer('bot-controller')
        emu.getVirtualNode(vname).setDisplayName('Bot-%.3d' % counter)

    ###########################################################################
    # Bind virtual nodes to physical nodes
    # Controller: bind to a specific host in AS150
    emu.addBinding(Binding('bot-controller',
                   filter=Filter(asn=150), action=Action.NEW))

    as_list = [150, 151, 152, 153, 154, 160, 161, 162, 163, 164, 170, 171]
    random.seed(42)  # reproducible
    for counter in range(6):
        vname = 'bot-node-%.3d' % counter
        asn = random.choice(as_list)
        emu.addBinding(Binding(vname, filter=Filter(asn=asn), action=Action.NEW))

    ###########################################################################
    emu.addLayer(base)
    emu.addLayer(Routing())
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())
    emu.addLayer(bot)
    emu.addLayer(botClient)

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

    output_dir = os.environ.get("SEED_OUTPUT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output_botnet")
    emu.compile(k8s, output_dir, override=True)
    print(f'[k8s_botnet] Compiled to {output_dir}')

if __name__ == '__main__':
    run()
