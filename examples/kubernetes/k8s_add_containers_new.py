#!/usr/bin/env python3
# encoding: utf-8

# Migrated from examples/basic/A11_add_containers_new/method_1/add_container.py
# Adapted for KubernetesCompiler
#
# Key differences from Docker version:
#   - DockerCompiler  → KubernetesCompiler
#   - attachCustomContainer (Docker compose raw entry) does NOT exist in K8s compiler;
#     equivalent busybox pods are written as raw Kubernetes Pod manifests and appended
#     to the output k8s.yaml after compilation.
#   - attachInternetMap  → k8s.attachInternetMap()  (same name, built-in to K8s compiler)

import os
import sys

from seedemu.layers import Base, Routing, Ebgp, Ibgp, Ospf
from seedemu.layers.Ebgp import PeerRelationship
from seedemu.services import WebService
from seedemu.compiler import KubernetesCompiler
from seedemu.core import Emulator, Binding, Filter

# ---------------------------------------------------------------------------
# Helper: generate a minimal busybox Pod manifest for K8s,
# replacing Docker's DOCKER_COMPOSE_ENTRY / attachCustomContainer pattern.
# ---------------------------------------------------------------------------
BUSYBOX_POD_TEMPLATE = """\
---
apiVersion: v1
kind: Pod
metadata:
  name: {name}
  namespace: {namespace}
  labels:
    app: {name}
    emulator: seedemu
    custom-container: "true"
  annotations:
    k8s.v1.cni.cncf.io/networks: '{multus_network}'
spec:
  containers:
  - name: {name}
    image: busybox:latest
    command: ["/bin/sh", "-c"]
    args:
      - |
        ip route del default 2>/dev/null || true
        ip route add default via {default_route}
        tail -f /dev/null
    securityContext:
      privileged: true
    env:
{env_entries}
"""

# ---------------------------------------------------------------------------
# Helper: generate a NodePort Service for a custom pod (port forwarding)
# ---------------------------------------------------------------------------
NODEPORT_SERVICE_TEMPLATE = """\
---
apiVersion: v1
kind: Service
metadata:
  name: {name}-svc
  namespace: {namespace}
spec:
  type: NodePort
  selector:
    app: {name}
  ports:
  - name: forwarded
    port: {target_port}
    targetPort: {target_port}
    nodePort: {node_port}
"""


def _make_env_entries(env_list, indent=6):
    """Convert ['a=1', 'b=2'] to YAML env list lines."""
    if not env_list:
        return ' ' * indent + '[] # no extra env vars'
    lines = []
    for item in env_list:
        key, _, val = item.partition('=')
        lines.append('{}- name: {}\n{}  value: "{}"'.format(
            ' ' * indent, key.strip(), ' ' * indent, val.strip()))
    return '\n'.join(lines)


def _attach_custom_pod(extra_manifests: list, name: str,
                       namespace: str, multus_net_name: str,
                       default_route: str,
                       env: list = None,
                       port_forwarding: str = '',
                       show_on_map: bool = False):
    """
    K8s equivalent of Docker.attachCustomContainer().

    Generates a Pod manifest and (optionally) a NodePort Service for port
    forwarding.  The manifests are collected in *extra_manifests* and written
    to a separate file after compilation.

    :param name:            pod / container name
    :param namespace:       K8s namespace
    :param multus_net_name: NetworkAttachmentDefinition name for the target net
    :param default_route:   gateway IP for the pod's default route
    :param env:             list of 'KEY=value' strings
    :param port_forwarding: Docker-style "hostPort:containerPort/proto" (optional)
    :param show_on_map:     if True, add a 'show-on-map: "true"' label (informational)
    """
    env_yaml = _make_env_entries(env or [])
    pod_yaml = BUSYBOX_POD_TEMPLATE.format(
        name=name,
        namespace=namespace,
        multus_network=multus_net_name,
        default_route=default_route,
        env_entries=env_yaml,
    )
    if show_on_map:
        pod_yaml = pod_yaml.replace(
            '    custom-container: "true"',
            '    custom-container: "true"\n    show-on-map: "true"'
        )
    extra_manifests.append(pod_yaml)

    # Port forwarding → NodePort Service
    if port_forwarding:
        # Expected format: "hostPort:containerPort/proto"  e.g. "9090:80/tcp"
        parts = port_forwarding.split(':')
        if len(parts) == 2:
            node_port = int(parts[0])
            target_port_proto = parts[1]
            target_port = int(target_port_proto.split('/')[0])
            svc_yaml = NODEPORT_SERVICE_TEMPLATE.format(
                name=name,
                namespace=namespace,
                node_port=node_port,
                target_port=target_port,
            )
            extra_manifests.append(svc_yaml)


def run():
    # -----------------------------------------------------------------------
    # Initialize emulator and layers  (same as Docker version)
    # -----------------------------------------------------------------------
    emu     = Emulator()
    base    = Base()
    routing = Routing()
    ebgp    = Ebgp()
    web     = WebService()

    # -----------------------------------------------------------------------
    # Topology: Internet Exchange + 3 ASes  (identical to Docker version)
    # -----------------------------------------------------------------------
    base.createInternetExchange(100)

    # AS-150
    as150 = base.createAutonomousSystem(150)
    as150.createNetwork('net0')
    as150.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    as150.createHost('web').joinNetwork('net0')
    web.install('web150')
    emu.addBinding(Binding('web150', filter=Filter(nodeName='web', asn=150)))

    # AS-151
    as151 = base.createAutonomousSystem(151)
    as151.createNetwork('net0')
    as151.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    as151.createHost('web').joinNetwork('net0')
    web.install('web151')
    emu.addBinding(Binding('web151', filter=Filter(nodeName='web', asn=151)))

    # AS-152
    as152 = base.createAutonomousSystem(152)
    as152.createNetwork('net0')
    as152.createRouter('router0').joinNetwork('net0').joinNetwork('ix100')
    as152.createHost('web').joinNetwork('net0')
    web.install('web152')
    emu.addBinding(Binding('web152', filter=Filter(nodeName='web', asn=152)))

    # BGP peering at IX-100
    ebgp.addPrivatePeerings(100, [150], [151, 152], PeerRelationship.Peer)
    ebgp.addPrivatePeerings(100, [151], [152], PeerRelationship.Peer)

    # -----------------------------------------------------------------------
    # Render
    # -----------------------------------------------------------------------
    emu.addLayer(base)
    emu.addLayer(routing)
    emu.addLayer(ebgp)
    emu.addLayer(Ibgp())
    emu.addLayer(Ospf())
    emu.addLayer(web)
    emu.render()

    # -----------------------------------------------------------------------
    # Kubernetes Compiler  (replaces DockerCompiler)
    # -----------------------------------------------------------------------
    registry_prefix     = os.environ.get("SEED_REGISTRY", "localhost:5001").strip()
    namespace           = os.environ.get("SEED_NAMESPACE", "seedemu").strip()
    cni_type            = os.environ.get("SEED_CNI_TYPE", "bridge").strip().lower()
    scheduling_strategy = os.environ.get("SEED_SCHEDULING_STRATEGY", "auto").strip().lower()
    image_pull_policy   = os.environ.get("SEED_IMAGE_PULL_POLICY", "Always").strip()

    k8s = KubernetesCompiler(
        registry_prefix=registry_prefix,
        namespace=namespace,
        use_multus=True,
        internetMapEnabled=True,
        generate_services=False,
        scheduling_strategy=scheduling_strategy,
        cni_type=cni_type,
        image_pull_policy=image_pull_policy,
    )

    output_dir = os.environ.get("SEED_OUTPUT_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output_add_containers_new")
    # --- Internet Map  (built-in to KubernetesCompiler; mirrors Docker.attachInternetMap) ---
    k8s.attachInternetMap(asn=151, net='net0', ip_address='10.151.0.90',
                          port_forwarding='8080:8080/tcp')

    emu.compile(k8s, output_dir, override=True)

    # -----------------------------------------------------------------------
    # Equivalent of Docker.attachCustomContainer() for Kubernetes
    #
    # The Multus NetworkAttachmentDefinition name follows the pattern:
    #   net-{asn}-{netname}
    # (matches what KubernetesCompiler generates for each AS network)
    # -----------------------------------------------------------------------
    extra_manifests = []

    default_route_150 = emu.getDefaultRouterByAsnAndNetwork(150, 'net0')

    # --- busybox (AS-150 net0, IP 10.150.0.80, port-forward 9090→80) ---
    _attach_custom_pod(
        extra_manifests,
        name='busybox',
        namespace=namespace,
        multus_net_name='net-150-net0',
        default_route=str(default_route_150),
        env=['a=1', 'b=2'],
        port_forwarding='30090:80/tcp',
    )

    # --- busybox2 (AS-150 net0, IP 10.150.0.81, show on map) ---
    _attach_custom_pod(
        extra_manifests,
        name='busybox2',
        namespace=namespace,
        multus_net_name='net-150-net0',
        default_route=str(default_route_150),
        port_forwarding='30091:80/tcp',
        show_on_map=True,
    )

    # -----------------------------------------------------------------------
    # Write extra custom-pod manifests to a separate file in output_dir
    # -----------------------------------------------------------------------
    custom_manifest_path = os.path.join(output_dir, 'k8s_custom_pods.yaml')
    with open(custom_manifest_path, 'w') as fh:
        fh.write('# Custom pod manifests — K8s equivalent of Docker attachCustomContainer\n')
        for m in extra_manifests:
            fh.write(m)

    print("=" * 70)
    print("K8s Add-Containers-New compilation complete.")
    print("=" * 70)
    print(f"Output directory : {output_dir}")
    print(f"Namespace        : {namespace}")
    print(f"Custom pods YAML : {custom_manifest_path}")
    print()
    print("Next steps:")
    print(f"  cd {output_dir}")
    print("  ./build_images.sh")
    print(f"  kubectl create ns {namespace} --dry-run=client -o yaml | kubectl apply -f -")
    print(f"  kubectl apply -n {namespace} -f k8s.yaml")
    print(f"  kubectl apply -n {namespace} -f k8s_custom_pods.yaml")
    print()
    print("Port mappings (NodePort Services):")
    print("  busybox  → nodePort 30090  (container port 80)")
    print("  busybox2 → nodePort 30091  (container port 80)")
    print("  internet-map → nodePort 8080")


if __name__ == '__main__':
    run()
