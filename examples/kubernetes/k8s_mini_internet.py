#!/usr/bin/env python3
# encoding: utf-8

import json
import os
import sys

# Copied from examples/internet/B00_mini_internet/mini_internet.py
# Adapted for KubernetesCompiler

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
cleaned_sys_path = []
for entry in sys.path:
    normalized = os.path.abspath(entry or os.getcwd())
    if normalized == REPO_ROOT:
        continue
    if os.path.isfile(os.path.join(normalized, "seedemu", "__init__.py")):
        continue
    cleaned_sys_path.append(entry)
sys.path[:] = [REPO_ROOT, *cleaned_sys_path]

from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf, PeerRelationship
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy, Platform
from seedemu.core import Emulator
from seedemu.utilities import Makers


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


def run(dumpfile=None, hosts_per_as=2):
    # Initialize Emulator
    emu   = Emulator()
    ebgp  = Ebgp()
    base  = Base()

    ###############################################################################
    # Create internet exchanges
    ix100 = base.createInternetExchange(100)
    ix101 = base.createInternetExchange(101)
    ix102 = base.createInternetExchange(102)
    ix103 = base.createInternetExchange(103)
    ix104 = base.createInternetExchange(104)
    ix105 = base.createInternetExchange(105)

    # Customize names (for visualization purpose)
    ix100.getPeeringLan().setDisplayName('NYC-100')
    ix101.getPeeringLan().setDisplayName('San Jose-101')
    ix102.getPeeringLan().setDisplayName('Chicago-102')
    ix103.getPeeringLan().setDisplayName('Miami-103')
    ix104.getPeeringLan().setDisplayName('Boston-104')
    ix105.getPeeringLan().setDisplayName('Huston-105')


    ###############################################################################
    # Create Transit Autonomous Systems

    ## Tier 1 ASes
    Makers.makeTransitAs(base, 2, [100, 101, 102, 105],
           [(100, 101), (101, 102), (100, 105)]
    )

    Makers.makeTransitAs(base, 3, [100, 103, 104, 105],
           [(100, 103), (100, 105), (103, 105), (103, 104)]
    )

    Makers.makeTransitAs(base, 4, [100, 102, 104],
           [(100, 104), (102, 104)]
    )

    ## Tier 2 ASes
    Makers.makeTransitAs(base, 11, [102, 105], [(102, 105)])
    Makers.makeTransitAs(base, 12, [101, 104], [(101, 104)])


    ###############################################################################
    # Create single-homed stub ASes.
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

    # An example to show how to add a host with customized IP address
    as154 = base.getAutonomousSystem(154)
    new_host = as154.createHost('host_new').joinNetwork('net0', address = '10.154.0.129')
    from seedemu.core import OptionRegistry, OptionMode


    o = OptionRegistry().sysctl_netipv4_conf_rp_filter({'all': False, 'default': False, 'net0': False}, mode = OptionMode.RUN_TIME)
    new_host.setOption(o)

    o = OptionRegistry().sysctl_netipv4_udp_rmem_min(5000, mode = OptionMode.RUN_TIME)
    new_host.setOption(o)

    ###############################################################################
    # Peering via RS (route server). The default peering mode for RS is PeerRelationship.Peer,
    # which means each AS will only export its customers and their own prefixes.
    # We will use this peering relationship to peer all the ASes in an IX.
    # None of them will provide transit service for others.

    ebgp.addPrivatePeerings(100, [2], [3, 4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(100, [3], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(102, [2], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(104, [3], [4], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(105, [2], [3], PeerRelationship.Peer)

    # Also register the same ASes as Route Server peers on IX100. The mini_internet
    # comment block above ("Peering via RS") promised RS-mediated sessions but the
    # original code only added direct private peerings, leaving rs100's BIRD with
    # zero BGP protocols — and the upstream verify check
    # (validate_k3s_mini_internet_multinode.sh:run_verify_bgp) explicitly greps
    # bird_ix100.txt for `p_as2|p_as3|p_as4 BGP Established`, so it would always
    # report `bgp_not_established`. Adding RS peerings makes the RS actually peer
    # with these ASes (next to the direct peerings) and the verify pass.
    ebgp.addRsPeers(100, [2, 3, 4])

    # To buy transit services from another autonomous system,
    # we will use private peering

    ebgp.addPrivatePeerings(100, [2],  [150, 151], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(100, [3],  [150], PeerRelationship.Provider)

    ebgp.addPrivatePeerings(101, [2],  [12], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(101, [12], [152, 153], PeerRelationship.Provider)

    ebgp.addPrivatePeerings(102, [2, 4],  [11, 154], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(102, [11], [154], PeerRelationship.Provider)

    ebgp.addPrivatePeerings(103, [3],  [160, 161, 162], PeerRelationship.Provider)

    ebgp.addPrivatePeerings(104, [3, 4], [12], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(104, [4],  [163], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(104, [12], [164], PeerRelationship.Provider)

    ebgp.addPrivatePeerings(105, [3],  [11, 170], PeerRelationship.Provider)
    ebgp.addPrivatePeerings(105, [11], [171], PeerRelationship.Provider)


    ###############################################################################
    # Add layers to the emulator

    emu.addLayer(base)
    emu.addLayer(Routing())
    emu.addLayer(ebgp)
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
    scheduling_strategy   = _env_str("SEED_SCHEDULING_STRATEGY", SchedulingStrategy.BY_AS_HARD).lower()
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
        colocate_asns = list(range(100, 106)) + [2, 3, 4, 11, 12, 150, 151, 152, 153, 154, 160, 161, 162, 163, 164, 170, 171]
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
        output_dir = os.path.join(os.path.dirname(__file__), 'output_mini_internet')
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

if __name__ == "__main__":
    hosts_per_as_env = os.environ.get("SEED_HOSTS_PER_AS", "2")
    try:
        hosts_per_as = int(hosts_per_as_env)
    except ValueError as exc:
        raise ValueError(f"Invalid SEED_HOSTS_PER_AS: {hosts_per_as_env}") from exc
    run(hosts_per_as=hosts_per_as)
