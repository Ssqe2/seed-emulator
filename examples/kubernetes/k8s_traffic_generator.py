#!/usr/bin/env python3
# encoding: utf-8

# Adapted from examples/internet/B28_traffic_generator/0-iperf-traffic-generator/iperf-traffic-generator.py
# Uses KubernetesCompiler instead of DockerCompiler

from seedemu.compiler import KubernetesCompiler, SchedulingStrategy
from seedemu.core import Emulator, Binding, Filter
from seedemu.services import TrafficService, TrafficServiceType
from seedemu.layers import EtcHosts
from examples.internet.B00_mini_internet import mini_internet
import os
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


def run(dumpfile=None):
    emu = Emulator()

    # Run the pre-built mini internet component and dump to bin
    mini_internet.run(dumpfile='./base_internet.bin')

    # Load the pre-built component
    emu.load('./base_internet.bin')

    base = emu.getLayer("Base")

    etc_hosts = EtcHosts()

    traffic_service = TrafficService()
    traffic_service.install("iperf-receiver-1", TrafficServiceType.IPERF_RECEIVER,
                            log_file="/root/iperf3_receiver.log")
    traffic_service.install("iperf-receiver-2", TrafficServiceType.IPERF_RECEIVER,
                            log_file="/root/iperf3_receiver.log")
    traffic_service.install(
        "iperf-generator",
        TrafficServiceType.IPERF_GENERATOR,
        log_file="/root/iperf3_generator.log",
        protocol="TCP",
        duration=60,
        rate=0
    ).addReceivers(hosts=["iperf-receiver-1", "iperf-receiver-2"])

    # Add hosts to AS-150
    as150 = base.getAutonomousSystem(150)
    as150.createHost("iperf-generator").joinNetwork("net0")

    # Add hosts to AS-162
    as162 = base.getAutonomousSystem(162)
    as162.createHost("iperf-receiver-1").joinNetwork("net0")

    # Add hosts to AS-171
    as171 = base.getAutonomousSystem(171)
    as171.createHost("iperf-receiver-2").joinNetwork("net0")

    # Binding virtual nodes to physical nodes
    emu.addBinding(
        Binding("iperf-generator", filter=Filter(asn=150, nodeName="iperf-generator"))
    )
    emu.addBinding(
        Binding("iperf-receiver-1", filter=Filter(asn=162, nodeName="iperf-receiver-1"))
    )
    emu.addBinding(
        Binding("iperf-receiver-2", filter=Filter(asn=171, nodeName="iperf-receiver-2"))
    )

    # Add the layers
    emu.addLayer(traffic_service)
    emu.addLayer(etc_hosts)

    if dumpfile is not None:
        emu.dump(dumpfile)
    else:
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
            output_dir = os.path.join(os.path.dirname(__file__), 'output_traffic_generator')
        elif not os.path.isabs(output_dir):
            output_dir = os.path.join(os.path.dirname(__file__), output_dir)

        emu.compile(k8s, output_dir, override=True)

        print(f"Compilation complete. Output generated in {output_dir}")
        print(f"Registry prefix: {registry_prefix}")
        print(f"Namespace: {namespace}")


if __name__ == "__main__":
    run()
