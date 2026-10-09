"""
network_logic.py — Fachada de compatibilidad para el motor matemático NetworkX.
Expone funciones requeridas por los módulos existentes del proyecto:
- build_graph
- classify_topology
- calculate_network_plan
"""
import networkx as nx
from typing import List, Dict, Any
from app.topology_engine import TopologyEngine
from app.schemas import NodeRequest, LinkRequest

def build_graph(nodes_data: list, links_data: list) -> nx.Graph:
    """Construye un grafo de NetworkX a partir del Draft."""
    nodes = []
    for n in nodes_data:
        vm_id = getattr(n, "vm_id", str(n))
        worker_id = getattr(n, "worker_id", None)
        nodes.append(NodeRequest(vm_id=vm_id, worker_id=worker_id))

    links = []
    for l in links_data:
        src = getattr(l, "source_vm", getattr(l, "vm_a_id", ""))
        dst = getattr(l, "target_vm", getattr(l, "vm_b_id", ""))
        name = getattr(l, "link_name", None)
        links.append(LinkRequest(source_vm=src, target_vm=dst, link_name=name))

    engine = TopologyEngine(nodes=nodes, links=links)
    return engine.graph

def classify_topology(G: nx.Graph) -> str:
    """Dictamina automáticamente el tipo de topología usando el TopologyEngine."""
    # Instanciamos TopologyEngine con el grafo dado
    engine = TopologyEngine()
    engine.graph = G
    top_type, _ = engine.classify_topology()
    return top_type

def calculate_network_plan(G: nx.Graph, base_vlan_inner: int = 10) -> list:
    """
    Calcula la lista de enlaces con su vlan_inner e is_remote.
    """
    engine = TopologyEngine()
    engine.graph = G
    
    # Extraemos placement_map de los atributos de nodo en G
    for node, data in G.nodes(data=True):
        if "worker_id" in data and data["worker_id"] is not None:
            engine.placement_map[str(node)] = data["worker_id"]

    plan = engine.calculate_plan(slice_id=1, base_vlan=base_vlan_inner)
    
    return [
        {
            "source_vm": l.source_vm,
            "target_vm": l.target_vm,
            "vlan_inner": l.vlan_inner,
            "is_remote": l.is_remote,
            "is_internet": l.is_internet,
            "subnet_cidr": l.subnet_cidr
        }
        for l in plan.links_configured
    ]
