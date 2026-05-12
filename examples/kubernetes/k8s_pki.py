#!/usr/bin/env python3
# encoding: utf-8

# PKI (Certificate Authority) demonstration on Kubernetes
# Adapted from examples/internet/B25_pki/pki.py
#
# NOTE ON K8S COMPATIBILITY:
# CAService uses addBuildCommand() to install CA tooling (step-ca / openssl)
# during image build. KubernetesCompiler (a Docker subclass) supports
# addBuildCommand() and emits the same Dockerfile, so CAService IS compatible.
#
# CAVEATS:
# 1. CAService uses BuildtimeDockerImage internally to pre-generate CA
#    certificates at compile time. This is a pure build-time action that does
#    not depend on Docker daemon features; it works with KubernetesCompiler.
# 2. WebService.enableHTTPS() calls addBuildCommand() to install nginx and
#    obtain certs from the CA at boot time. This works on K8s pods.
# 3. Static IP addresses (10.151.0.7 / 10.151.0.8) assigned in the emulator
#    refer to the pod overlay network addresses, consistent with Docker behavior.

import os
from seedemu.core import Emulator, Binding, Filter, Action
from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship, EtcHosts
from seedemu.services import CAService, CAServer, WebService, WebServer, RootCAStore
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy


def run():
    emu     = Emulator()
    base    = Base()
    routing = Routing()
    ebgp    = Ebgp()
    ibgp    = Ibgp()
    ospf    = Ospf()

    ###########################################################################
    # Base internet topology (mirrors B25 base_internet.py)
    ix100 = base.createInternetExchange(100)
    ix101 = base.createInternetExchange(101)

    ix100.getPeeringLan().setDisplayName('NYC-100')
    ix101.getPeeringLan().setDisplayName('San Jose-101')

    as2 = base.createAutonomousSystem(2)
    as2.createNetwork('net0')
    as2.createRouter('r1').joinNetwork('net0').joinNetwork('ix100')
    as2.createRouter('r2').joinNetwork('net0').joinNetwork('ix101')

    as150 = base.createAutonomousSystem(150)
    as150.createNetwork('net0')
    as150.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    for i in range(6):
        as150.createHost('host_{}'.format(i)).joinNetwork('net0')

    as151 = base.createAutonomousSystem(151)
    as151.createNetwork('net0')
    as151.createRouter('router0').joinNetwork('net0').joinNetwork('ix101')
    for i in range(2):
        as151.createHost('host_{}'.format(i)).joinNetwork('net0')

    ebgp.addPrivatePeering(100, 2, 150, abRelationship=PeerRelationship.Provider)
    ebgp.addPrivatePeering(101, 2, 151, abRelationship=PeerRelationship.Provider)

    ###########################################################################
    # Create physical nodes for CA and web servers (mirrors pki.py)
    as150.createHost('ca1').joinNetwork('net0').addHostName('seedCA.net')
    as150.createHost('ca2').joinNetwork('net0').addHostName('seedCA.com')

    as151.createHost('web1').joinNetwork('net0', address='10.151.0.7') \
                            .addHostName('example32.com')
    as151.createHost('web2').joinNetwork('net0', address='10.151.0.8') \
                            .addHostName('bank32.com')

    ###########################################################################
    # PKI service layers
    caStore1 = RootCAStore(caDomain='seedCA.net')
    caStore2 = RootCAStore(caDomain='seedCA.com')

    ca = CAService()

    caServer1: CAServer = ca.install('ca1-vnode')
    caServer1.setCAStore(caStore1)
    caServer1.setCertDuration("2160h")
    caServer1.installCACert()

    caServer2: CAServer = ca.install('ca2-vnode')
    caServer2.setCAStore(caStore2)
    caServer2.setCertDuration("2160h")
    caServer2.installCACert()

    web = WebService()

    webServer1: WebServer = web.install('web1-vnode')
    webServer1.setServerNames(['example32.com'])
    webServer1.setCAServer(caServer1).enableHTTPS()
    webServer1.setIndexContent("<h1>Web server at example32.com</h1>")

    webServer2: WebServer = web.install('web2-vnode')
    webServer2.setServerNames(['bank32.com'])
    webServer2.setCAServer(caServer2).enableHTTPS()
    webServer2.setIndexContent("<h1>Web server at bank32.com</h1>")

    ###########################################################################
    # Bind vnodes to physical nodes
    emu.addBinding(Binding('ca1-vnode',  filter=Filter(nodeName='ca1'),  action=Action.FIRST))
    emu.addBinding(Binding('ca2-vnode',  filter=Filter(nodeName='ca2'),  action=Action.FIRST))
    emu.addBinding(Binding('web1-vnode', filter=Filter(nodeName='web1'), action=Action.FIRST))
    emu.addBinding(Binding('web2-vnode', filter=Filter(nodeName='web2'), action=Action.FIRST))

    ###########################################################################
    emu.addLayer(base)
    emu.addLayer(EtcHosts())
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    emu.addLayer(ca)
    emu.addLayer(web)

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

    output_dir = os.environ.get("SEED_OUTPUT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output_pki")
    emu.compile(k8s, output_dir, override=True)
    print(f'[k8s_pki] Compiled to {output_dir}')


if __name__ == '__main__':
    run()
