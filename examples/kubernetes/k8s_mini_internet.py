#!/usr/bin/env python3
# encoding: utf-8

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


def run(
    registry_prefix: str = "localhost:5001",
    namespace: str = "seedemu",
    cluster_name: str = "seedemu-kvtest",
    cni_type: str = "bridge",
    cni_master_interface: str = "eth0",
    image_pull_policy: str = "Always",
    scheduling_strategy: str = SchedulingStrategy.BY_AS_HARD,
    node_labels: dict = None,
    force_colocate: bool = False,
    single_node: str = None,
    hosts_per_as: int = 2,
    output_dir: str = None,
    dumpfile: str = None,
):
    """!
    @brief Build the mini_internet topology and compile it for Kubernetes.

    @param registry_prefix (optional) where to push compiled images. Default
        to "localhost:5001".
    @param namespace (optional) K8s namespace to deploy into. Default to
        "seedemu".
    @param cluster_name (optional) cluster name used by the force_colocate
        branch to derive the default single_node hostname. Default to
        "seedemu-kvtest".
    @param cni_type (optional) CNI plugin type the compiler emits in NADs:
        "vxlan-overlay" | "macvlan" | "ipvlan" | "bridge" | "host-local".
        Default to "bridge".
    @param cni_master_interface (optional) parent interface for macvlan/ipvlan.
        Default to "eth0".
    @param image_pull_policy (optional) K8s imagePullPolicy. Default to "Always".
    @param scheduling_strategy (optional) SEED scheduling strategy. Default to
        SchedulingStrategy.BY_AS_HARD.
    @param node_labels (optional) explicit per-ASN node labels dict for the
        compiler's scheduling layer. None = empty (compiler may compute
        defaults). Driver reads this from advanced.yaml's
        scheduling.node_labels (already a dict in yaml — no JSON middle step).
    @param force_colocate (optional) when True AND node_labels is empty AND
        cni_type is bridge/host-local, force all 17 mini_internet ASNs onto
        the single_node host and switch scheduling_strategy to CUSTOM.
        Useful for one-node Kind / dev setups. Default to False.
    @param single_node (optional) target hostname when force_colocate is on.
        None = derive as f"{cluster_name}-control-plane".
    @param hosts_per_as (optional) how many host pods to create per stub AS.
        Default to 2.
    @param output_dir (optional) where to write compiled k8s.yaml +
        build_images.sh. None = sibling "output_mini_internet/" dir.
    @param dumpfile (optional) if given, dump the emulator state to this file
        instead of compiling. Default to None (compile).
    """
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

    if dumpfile is not None:
        emu.dump(dumpfile)
        return

    emu.render()

    ###############################################################################
    # Kubernetes Compilation

    # Normalize string-typed inputs the same way the old env-reading code did,
    # so callers that pass arbitrary case (e.g. "Bridge") still hit the same
    # downstream comparisons.
    cni_type = str(cni_type).strip().lower()
    cni_master_interface = str(cni_master_interface).strip()
    image_pull_policy = str(image_pull_policy).strip()
    scheduling_strategy = str(scheduling_strategy).strip().lower()
    node_labels = dict(node_labels) if node_labels else {}

    # force_colocate branch: pin every AS to a single K8s node. Used by
    # one-node Kind setups / dev playgrounds where cross-node scheduling
    # is unwanted. Triggers only when caller has not already supplied
    # node_labels AND cni_type is single-node-only (bridge/host-local).
    if force_colocate and not node_labels and cni_type in {"bridge", "host-local"}:
        if not single_node:
            single_node = f"{cluster_name}-control-plane"
        colocate_asns = list(range(100, 106)) + [2, 3, 4, 11, 12, 150, 151, 152, 153, 154, 160, 161, 162, 163, 164, 170, 171]
        node_labels = {str(asn): {"kubernetes.io/hostname": single_node} for asn in colocate_asns}
        scheduling_strategy = SchedulingStrategy.CUSTOM

    # Configure the compiler
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

    # Resolve output directory: None = sibling dir of this script.
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(__file__), 'output_mini_internet')
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(__file__), output_dir)
    emu.compile(k8s, output_dir, override=True)

    print(f"Compilation complete. Output generated in {output_dir}")
    print(f"Registry prefix: {registry_prefix}")
    print(f"Namespace: {namespace}")

if __name__ == "__main__":
    # Direct invocation runs with defaults — sibling output_mini_internet/
    # under examples/kubernetes/. Configuration (cni_type, namespace,
    # registry_prefix, hosts_per_as, force_colocate, node_labels, ...) is
    # sourced from a caller (vagrant-deploy/scripts/run_topology.py reads
    # our yaml configs and invokes run() with explicit kwargs).
    run()
