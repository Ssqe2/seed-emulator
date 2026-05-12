#!/usr/bin/env python3
# encoding: utf-8

# Adapted from examples/internet/B26_ipfs_kubo/kubo.py
# Uses KubernetesCompiler instead of DockerCompiler

from seedemu import *
from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
import os
import sys
import json


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


def run():
    # Add the B26 source directory to path for base_component import
    # __file__ is examples/kubernetes/k8s_ipfs_kubo.py
    # so dirname(__file__) = examples/kubernetes
    # dirname(dirname(__file__)) = examples
    # then join with internet/B26_ipfs_kubo
    b26_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'internet', 'B26_ipfs_kubo'
    )
    sys.path.insert(0, b26_dir)
    import base_component

    emu = Emulator()
    base_component.run(dumpfile='./base_component.bin')

    # Load the pre-built components and merge them
    emu.load('./base_component.bin')

    # Use X64 architecture by default for K8s (amd64 cluster)
    arch = Architecture.X64

    ipfs = KuboService(arch=arch)

    # Iterate through hosts from base component and install Kubo on them
    numHosts = 3
    i = 0
    for asNum in range(150, 172):
        try:
            curAS = emu.getLayer('Base').getAutonomousSystem(asNum)
        except Exception:
            print(f'AS {asNum} does\'t appear to exist.')
        else:
            for h in range(numHosts):
                vnode = f'kubo-{i}'
                displayName = f'Kubo-{i}_'
                cur = ipfs.install(vnode)
                if i % 5 == 0:
                    cur.setBootNode()
                    displayName += 'Boot'
                else:
                    displayName += 'Peer'

                emu.getVirtualNode(vnode).setDisplayName(displayName)
                emu.addBinding(Binding(vnode, filter=Filter(asn=asNum, nodeName=f'host_{h}')))
                i += 1

    emu.addLayer(ipfs)

    emu.render()

    ###########################################################################
    # Kubernetes Compilation
    registry_prefix      = os.environ.get("SEED_REGISTRY", "localhost:5001")
    namespace            = os.environ.get("SEED_NAMESPACE", "seedemu")
    cluster_name         = os.environ.get("SEED_CLUSTER_NAME", "seedemu-kvtest")
    cni_type             = os.environ.get("SEED_CNI_TYPE", "bridge").strip().lower()
    cni_master_interface = os.environ.get("SEED_CNI_MASTER_INTERFACE", "eth0").strip()
    image_pull_policy    = os.environ.get("SEED_IMAGE_PULL_POLICY", "Always").strip()
    scheduling_strategy  = os.environ.get("SEED_SCHEDULING_STRATEGY", SchedulingStrategy.AUTO).strip().lower()
    node_labels          = _parse_node_labels_json(os.environ.get("SEED_NODE_LABELS_JSON", ""))

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
        output_dir = os.path.join(os.path.dirname(__file__), 'output_ipfs_kubo')
    elif not os.path.isabs(output_dir):
        output_dir = os.path.join(os.path.dirname(__file__), output_dir)

    emu.compile(k8s, output_dir, override=True)

    print(f"Compilation complete. Output generated in {output_dir}")
    print(f"Registry prefix: {registry_prefix}")
    print(f"Namespace: {namespace}")


if __name__ == "__main__":
    run()
