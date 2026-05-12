#!/usr/bin/env python3
# encoding: utf-8

# Adapted from examples/basic/A09_node_customization/node_customization.py
# Uses KubernetesCompiler instead of Docker
# Depends on A01_transit_as topology (loaded via transit_as.run())

from seedemu import *
from examples.basic.A01_transit_as import transit_as
import os

###############################################################################
# Load the pre-built component from example A01_transit_as
# Use /tmp to store the bin file to avoid directory issues
transit_as.run(dumpfile='/tmp/base_component_node_customization.bin')

emu = Emulator()
emu.load('/tmp/base_component_node_customization.bin')

###############################################################################
# Demonstrating how to customize a node

base  = emu.getLayer('Base')

# Get the instances of the AS and node objects
as152 = base.getAutonomousSystem(152)
node  = as152.getHost('host0')

# Dockerfile: RUN apt-get update && apt-get install -y 
#                     --no-install-recommends python3
node.addSoftware("python3")

# Dockerfile: RUN curl http://example.com
node.addBuildCommand("curl http://example.com")

# Create a file on the node; file content come from hostpath.
# Use the original myprog.py from A09_node_customization directory
_this_dir = os.path.dirname(os.path.abspath(__file__))
_myprog_path = os.path.join(
    os.path.dirname(_this_dir),
    'basic', 'A09_node_customization', 'myprog.py'
)
node.importFile(hostpath=_myprog_path, containerpath="/myprog.py")

# Create a file on the node; file content is the provided string
node.setFile(path="/file.txt", content="hello world")

# Add "ping 1.2.3.4" to start.sh
node.insertStartCommand(0, "ping 1.2.3.4")

# Add "python3 /myprog.py &" to start.sh
node.appendStartCommand("python3 /myprog.py", fork=True)

###############################################################################
# Render and compile with KubernetesCompiler

emu.render()

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
    output_dir = os.path.join(os.path.dirname(__file__), 'output_node_customization')
elif not os.path.isabs(output_dir):
    output_dir = os.path.join(os.path.dirname(__file__), output_dir)

emu.compile(k8s, output_dir, override=True)

print(f"Compilation complete. Output generated in {output_dir}")
print(f"Registry prefix: {registry_prefix}")
print(f"Namespace: {namespace}")
