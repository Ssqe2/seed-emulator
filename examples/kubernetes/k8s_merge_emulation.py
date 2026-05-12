#!/usr/bin/env python3
# encoding: utf-8

# Adapted from examples/basic/A06_merge_emulation/merge_emulation.py
# Uses KubernetesCompiler instead of Docker

from seedemu.core import Emulator
from seedemu.mergers import DEFAULT_MERGERS
from seedemu.layers import Base, Routing, Ebgp, PeerRelationship
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
import os


###############################################################################
# Create Emulation A
# We can also load this emulation for a pre-built component

emuA     = Emulator()
baseA    = Base()
ebgpA    = Ebgp()
routingA = Routing()

# Create an internet exchange ix100
baseA.createInternetExchange(100)

# Create an autonomous system AS-150
as150 = baseA.createAutonomousSystem(150)
as150.createNetwork('net0')
as150.createHost('host0').joinNetwork('net0')
as150.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')

# Peer with others at ix100
ebgpA.addPrivatePeering(100, 150, 151, abRelationship=PeerRelationship.Peer)

# Add these layers to the emulation A
emuA.addLayer(baseA)
emuA.addLayer(routingA)
emuA.addLayer(ebgpA)

###############################################################################
# Create Emulation B
# We can also load this emulation for a pre-built component

baseB = Base()
ebgpB = Ebgp()
routingB = Routing()

# Create an autonomous system AS-151
as151 = baseB.createAutonomousSystem(151)
as151.createNetwork('net0')
as151.createHost('host0').joinNetwork('net0')
as151.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')

# Peer with others at ix100
# Peering already set up in emulation A (addPrivatePeering is bidirectional after merge)

# Add these layers to the emulation B
emuB = Emulator()
emuB.addLayer(baseB)
emuB.addLayer(routingB)
emuB.addLayer(ebgpB)


###############################################################################
# Merge these two emulations

emu_merged = emuA.merge(emuB, DEFAULT_MERGERS)


###############################################################################
# Render and compile with KubernetesCompiler

emu_merged.render()

###############################################################################
# Kubernetes Compilation

registry_prefix      = os.environ.get("SEED_REGISTRY", "localhost:5001")
namespace            = os.environ.get("SEED_NAMESPACE", "seedemu")
cni_type             = os.environ.get("SEED_CNI_TYPE", "bridge").strip().lower()
cni_master_interface = os.environ.get("SEED_CNI_MASTER_INTERFACE", "eth0").strip()
image_pull_policy    = os.environ.get("SEED_IMAGE_PULL_POLICY", "Always").strip()
scheduling_strategy  = os.environ.get("SEED_SCHEDULING_STRATEGY", SchedulingStrategy.AUTO).strip().lower()

k8s = KubernetesCompiler(
    registry_prefix=registry_prefix,
    namespace=namespace,
    use_multus=True,
    internetMapEnabled=False,
    scheduling_strategy=scheduling_strategy,
    cni_type=cni_type,
    cni_master_interface=cni_master_interface,
    generate_services=True,
    image_pull_policy=image_pull_policy,
)

output_dir = os.environ.get("SEED_OUTPUT_DIR")
if not output_dir:
    output_dir = os.path.join(os.path.dirname(__file__), 'output_merge_emulation')
elif not os.path.isabs(output_dir):
    output_dir = os.path.join(os.path.dirname(__file__), output_dir)

emu_merged.compile(k8s, output_dir, override=True)

print(f"Compilation complete. Output generated in {output_dir}")
print(f"Registry prefix: {registry_prefix}")
print(f"Namespace: {namespace}")
