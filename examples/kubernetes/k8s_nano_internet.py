#!/usr/bin/env python3
# encoding: utf-8

# Copied from examples/basic/A20_nano_internet/nano_internet.py
# Adapted for KubernetesCompiler

import json
import os
import sys

from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.core import Binding, Emulator, Filter
from seedemu.layers import Base, Ebgp, Ibgp, Ospf, Routing
from seedemu.layers.Ebgp import PeerRelationship
from seedemu.services import DomainNameService, WebService


def _env_str(key: str, default: str = "") -> str:
    return os.environ.get(key, "").strip() or default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key, "").strip().lower()
    if raw in ("true", "1", "yes"):
        return True
    if raw in ("false", "0", "no"):
        return False
    return default


def _env_json(key: str, default):
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def run():
    # Initialize the emulator and layers
    emu     = Emulator()
    base    = Base()
    routing = Routing()
    ebgp    = Ebgp()
    web     = WebService()
    dns     = DomainNameService()

    ###############################################################################
    # Create Internet exchanges

    ix100 = base.createInternetExchange(100)
    ix101 = base.createInternetExchange(101)
    ix100.getPeeringLan().setDisplayName('New York-100')
    ix101.getPeeringLan().setDisplayName('Chicago-101')

    ###############################################################################
    # Create and set up a transit AS (AS-3)

    as3 = base.createAutonomousSystem(3)

    # Create 3 internal networks
    as3.createNetwork('net0')
    as3.createNetwork('net1')
    as3.createNetwork('net2')

    # Create four routers and link them in a linear structure:
    # ix100 <--> r1 <--> r2 <--> r3 <--> r4 <--> ix101
    # r1 and r4 are BGP routers because they are connected to Internet exchanges
    as3.createRouter('r1').joinNetwork('net0').joinNetwork('ix100')
    as3.createRouter('r2').joinNetwork('net0').joinNetwork('net1')
    as3.createRouter('r3').joinNetwork('net1').joinNetwork('net2')
    as3.createRouter('r4').joinNetwork('net2').joinNetwork('ix101')

    ###############################################################################
    # Create and set up a stub AS (AS-150) connecting to ix100

    as150 = base.createAutonomousSystem(150)
    as150.createNetwork('net0')
    as150.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    as150.createHost('web').joinNetwork('net0')
    as150.createHost('dns').joinNetwork('net0')
    web.install('web150')
    dns.install('dns150')
    emu.addBinding(Binding('web150', filter = Filter(nodeName = 'web', asn = 150)))
    emu.addBinding(Binding('dns150', filter = Filter(nodeName = 'dns', asn = 150)))

    ###############################################################################
    # Create and set up another stub AS (AS-151) connecting to ix101

    as151 = base.createAutonomousSystem(151)
    as151.createNetwork('net0')
    as151.createRouter('router0').joinNetwork('net0').joinNetwork('ix101')
    as151.createHost('web').joinNetwork('net0')
    as151.createHost('dns').joinNetwork('net0')
    web.install('web151')
    dns.install('dns151')
    emu.addBinding(Binding('web151', filter = Filter(nodeName = 'web', asn = 151)))
    emu.addBinding(Binding('dns151', filter = Filter(nodeName = 'dns', asn = 151)))

    ###############################################################################
    # Create and set up another stub AS (AS-152) connecting to ix101

    as152 = base.createAutonomousSystem(152)
    as152.createNetwork('net0')
    as152.createRouter('router0').joinNetwork('net0').joinNetwork('ix101')
    as152.createHost('web').joinNetwork('net0')
    web.install('web152')
    emu.addBinding(Binding('web152', filter = Filter(nodeName = 'web', asn = 152)))

    ###############################################################################
    # Peering at Internet Exchanges

    ebgp.addPrivatePeering(100, 3, 150, abRelationship=PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [3], [151, 152], abRelationship=PeerRelationship.Provider)
    ebgp.addPrivatePeering(101, 151, 152, abRelationship=PeerRelationship.Peer)

    ###############################################################################
    # Rendering

    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(web)
    emu.addLayer(dns)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())

    emu.render()

    ###############################################################################
    # Kubernetes Compilation

    # === YAML-overridable knobs (vagrant-deploy deploy.yaml -> SEED_* env) ===
    # Empty/missing env -> topology-author default below.

    # Cluster infrastructure (must be injected from cluster inventory)
    registry_prefix       = _env_str("SEED_REGISTRY", "localhost:5000")
    namespace             = _env_str("SEED_NAMESPACE", "seedemu")
    cni_type              = _env_str("SEED_CNI_TYPE", "bridge").lower()
    cni_master_interface  = _env_str("SEED_CNI_MASTER_INTERFACE", "eth0")

    # Deployment switches (yaml-defined)
    use_multus            = _env_bool("SEED_USE_MULTUS", True)
    internet_map_enabled  = _env_bool("SEED_INTERNET_MAP_ENABLED", False)
    image_pull_policy     = _env_str("SEED_IMAGE_PULL_POLICY", "Always")

    # defined-by-topology (yaml empty -> topology-author default)
    # BY_AS_HARD + 显式 node_labels:强制 AS150/AS151/AS152 落不同节点,
    # 这样 host pod 跨节点 ping 真实经过 hypervisor 虚拟网络 + vxlan-overlay,
    # 是 provider 性能对比 benchmark 的关键。
    # 非 3 节点 cluster 时,外部 SEED_NODE_LABELS_JSON 整体覆盖。
    scheduling_strategy   = _env_str("SEED_SCHEDULING_STRATEGY", SchedulingStrategy.BY_AS_HARD).lower()
    _default_node_labels = {
        # master:transit AS3(4 router)+ 两个 IX rs
        "3":   {"kubernetes.io/hostname": "master"},
        "100": {"kubernetes.io/hostname": "master"},
        "101": {"kubernetes.io/hostname": "master"},
        # worker1:AS 150(host_0 web/dns)
        "150": {"kubernetes.io/hostname": "worker1"},
        # worker2:AS 151 + AS 152
        "151": {"kubernetes.io/hostname": "worker2"},
        "152": {"kubernetes.io/hostname": "worker2"},
    }
    node_labels           = _env_json("SEED_NODE_LABELS_JSON", _default_node_labels)
    default_resources     = _env_json("SEED_DEFAULT_RESOURCES", None)
    local_link_cni_type   = _env_str("SEED_LOCAL_LINK_CNI_TYPE", "") or None

    # K8s Service exposure
    _gen_yaml             = _env_str("SEED_GENERATE_SERVICES", "auto").lower()
    generate_services     = _gen_yaml != "false"   # auto/true -> True; false -> False
    service_type          = _env_str("SEED_SERVICE_TYPE", "NodePort")

    k8s = KubernetesCompiler(
        registry_prefix=registry_prefix,
        namespace=namespace,
        use_multus=use_multus,
        internetMapEnabled=internet_map_enabled,
        scheduling_strategy=scheduling_strategy,
        node_labels=node_labels,
        default_resources=default_resources,
        cni_type=cni_type,
        local_link_cni_type=local_link_cni_type,
        cni_master_interface=cni_master_interface,
        generate_services=generate_services,
        service_type=service_type,
        image_pull_policy=image_pull_policy,
    )

    output_dir = os.environ.get("SEED_OUTPUT_DIR")
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(__file__), 'output_nano_internet')
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(__file__), output_dir)
    # Generate internet-map Deployment + NodePort Service if requested (K8s
    # compiler requires explicit attachInternetMap() call, unlike Docker which
    # does it automatically when internetMapEnabled=True).
    # Must be called BEFORE emu.compile() so the manifest/build-command appends
    # get serialized into k8s.yaml and build_images.sh during _doCompile().
    if internet_map_enabled:
        k8s.attachInternetMap()

    emu.compile(k8s, output_dir, override=True)

    print(f"Compilation complete. Output generated in {output_dir}")
    print(f"Registry prefix: {registry_prefix}")
    print(f"Namespace: {namespace}")

if __name__ == '__main__':
    run()
