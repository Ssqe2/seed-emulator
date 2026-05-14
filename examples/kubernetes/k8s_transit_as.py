#!/usr/bin/env python3
# encoding: utf-8

# Copied from examples/basic/A01_transit_as/transit_as.py
# Adapted for KubernetesCompiler

import json
import os

from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.services import WebService
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy, Platform
from seedemu.core import Emulator, Binding, Filter


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
    ibgp    = Ibgp()
    ospf    = Ospf()
    web     = WebService()

    ###############################################################################
    # Create two Internet Exchanges, where BGP routers peer with one another.
    base.createInternetExchange(100)
    base.createInternetExchange(101)

    ###############################################################################
    # Create and configure a transit autonomous system

    as2 = base.createAutonomousSystem(2)

    # Create 3 internal networks
    as2.createNetwork('net0')
    as2.createNetwork('net1')
    as2.createNetwork('net2')

    # Create four routers and link them in a linear structure:
    # ix100 <--> r1 <--> r2 <--> r3 <--> r4 <--> ix101
    # r1 and r4 are BGP routers because they are connected to Internet exchanges
    as2.createRouter('r1').joinNetwork('net0').joinNetwork('ix100')
    as2.createRouter('r2').joinNetwork('net0').joinNetwork('net1')
    as2.createRouter('r3').joinNetwork('net1').joinNetwork('net2')
    as2.createRouter('r4').joinNetwork('net2').joinNetwork('ix101')

    ###############################################################################
    # Create and configure two stub autonomous systems

    # AS-150 connects to ix100
    as150 = base.createAutonomousSystem(150)
    as150.createNetwork('net0')
    as150.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    as150.createHost('web').joinNetwork('net0')
    web.install('web150')
    emu.addBinding(Binding('web150', filter = Filter(nodeName = 'web', asn = 150)))

    # AS-151 connects to ix101
    as151 = base.createAutonomousSystem(151)
    as151.createNetwork('net0')
    as151.createRouter('router0').joinNetwork('net0').joinNetwork('ix101')
    as151.createHost('web').joinNetwork('net0')
    web.install('web151')
    emu.addBinding(Binding('web151', filter = Filter(nodeName = 'web', asn = 151)))

    ###############################################################################
    # Peering at Internet Exchanges

    ebgp.addPrivatePeerings(100, [2], [150], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [2], [151], PeerRelationship.Provider)

    ###############################################################################
    # Rendering

    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    # Transit topology needs IGP + iBGP to propagate reachability across
    # internal routers (r1-r4) and distribute external prefixes within AS2.
    emu.addLayer(ibgp)
    emu.addLayer(ospf)
    emu.addLayer(web)

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
    scheduling_strategy   = _env_str("SEED_SCHEDULING_STRATEGY", SchedulingStrategy.AUTO).lower()
    node_labels           = _env_json("SEED_NODE_LABELS_JSON", {})
    default_resources     = _env_json("SEED_DEFAULT_RESOURCES", None)
    local_link_cni_type   = _env_str("SEED_LOCAL_LINK_CNI_TYPE", "") or None

    # Optional force-colocate (preserved from original): when explicitly enabled
    # via SEED_FORCE_COLOCATE and no explicit node_labels were provided, pin all
    # ASes to a single node. Used for deterministic local kind runs.
    cluster_name          = _env_str("SEED_CLUSTER_NAME", "seedemu-kvtest")
    force_colocate        = _env_bool("SEED_FORCE_COLOCATE", False)
    if force_colocate and not node_labels and cni_type in {"bridge", "host-local"}:
        single_node = _env_str("SEED_SINGLE_NODE", f"{cluster_name}-control-plane")
        # Transit topology ASNs: IX(100/101), transit(2), stubs(150/151)
        colocate_asns = [100, 101, 2, 150, 151]
        node_labels = {str(asn): {"kubernetes.io/hostname": single_node} for asn in colocate_asns}
        scheduling_strategy = SchedulingStrategy.CUSTOM

    # K8s Service exposure
    _gen_yaml             = _env_str("SEED_GENERATE_SERVICES", "auto").lower()
    generate_services     = _gen_yaml != "false"   # auto/true -> True; false -> False
    service_type          = _env_str("SEED_SERVICE_TYPE", "NodePort")

    # Configure the compiler
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

    # Compile to the output directory.
    output_dir = os.environ.get("SEED_OUTPUT_DIR")
    if not output_dir:
        output_dir = os.path.join(os.path.dirname(__file__), 'output_transit_as')
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
