"""
topology_engine.py — Motor de análisis y clasificación matemática de grafos con NetworkX.
Cumple con la Rúbrica R1B (clasificación automática) y R5 (aislamiento QinQ y cálculo de túneles).
"""
import networkx as nx
from typing import List, Dict, Tuple, Any, Optional, Union
from app.schemas import (
    NodeRequest, LinkRequest, LinkResponse, NetworkPlanResponse,
    LinkInput, LinkAllocationDetail
)
from app.config import config

class TopologyEngine:
    """
    Motor matemático que procesa topologías virtuales para el Slice Manager y Network Manager.
    """

    INTERNET_KEYWORDS = {"0", 0, "INTERNET", "INET", "WAN", "EXTERNAL"}

    @classmethod
    def is_internet_node(cls, node_id: Any) -> bool:
        if isinstance(node_id, str):
            return node_id.strip().upper() in cls.INTERNET_KEYWORDS
        return node_id in cls.INTERNET_KEYWORDS

    def __init__(self, nodes: List[NodeRequest] = None, links: List[LinkRequest] = None, placement_map: Dict[str, Any] = None):
        self.raw_nodes = nodes or []
        self.raw_links = links or []
        self.placement_map = placement_map or {}
        
        # Poblamos placement_map con los worker_id que vengan en NodeRequest si los hay
        for node in self.raw_nodes:
            if node.worker_id is not None:
                self.placement_map[str(node.vm_id)] = node.worker_id

        self.graph = nx.Graph()
        self._build_graph()

    def _build_graph(self):
        """Construye el grafo con nodos de VM y aristas de enlaces."""
        for node in self.raw_nodes:
            worker = self.placement_map.get(str(node.vm_id), node.worker_id)
            self.graph.add_node(str(node.vm_id), worker_id=worker)

        for link in self.raw_links:
            u = str(link.source_vm)
            v = str(link.target_vm)
            self.graph.add_edge(u, v, link_name=link.link_name or f"link_{u}_{v}")

    def classify_topology(self) -> Tuple[str, bool]:
        """
        Dictamina matemáticamente el tipo de topología exigida en R1B:
        Lineal, Anillo, Árbol, Malla (Full Mesh o Parcial), Bus, Punto Único.
        Excluye el nodo de Internet para evaluar solo la red de computo virtual.
        """
        # Filtrar nodos excluyendo Internet
        vm_nodes = [n for n in self.graph.nodes() if not self.is_internet_node(n)]
        
        if not vm_nodes:
            return ("Vacía", False)

        subgraph = self.graph.subgraph(vm_nodes)
        num_nodes = subgraph.number_of_nodes()
        num_edges = subgraph.number_of_edges()

        if num_nodes == 1:
            return ("Punto Único", True)

        is_connected = nx.is_connected(subgraph)
        if not is_connected:
            return ("Desconectada", False)

        degrees = [d for _, d in subgraph.degree()]
        max_degree = max(degrees, default=0)

        # 1. Malla Completa (Full Mesh): N*(N-1)/2 enlaces
        max_possible_edges = (num_nodes * (num_nodes - 1)) // 2
        if num_edges == max_possible_edges and num_nodes >= 3:
            return ("Malla (Full Mesh)", True)

        # 2. Anillo (Ring): N aristas, todos grado 2
        if num_nodes >= 3 and num_edges == num_nodes and all(d == 2 for d in degrees):
            return ("Anillo", True)

        # 3. Topologías sin ciclos (Árboles)
        if nx.is_tree(subgraph):
            if max_degree <= 2:
                return ("Lineal", True)
            if max_degree >= (num_nodes - 1) and num_nodes >= 4:
                return ("Bus / Estrella Concentrada", True)
            return ("Árbol (Jerárquico)", True)

        # 4. Malla Parcial (con ciclos pero no completa ni anillo simple)
        if num_edges > num_nodes:
            return ("Malla Parcial", True)

        return ("Topología Personalizada", True)

    def calculate_plan(self, slice_id: int, base_vlan: int = None) -> NetworkPlanResponse:
        """
        Calcula el plan completo de enlaces:
        - vlan_inner (C-TAG interna)
        - is_remote (detección de servidores físicos distintos)
        - is_internet (enlaces hacia la WAN)
        - subnet_cidr (/30 para P2P privado)
        """
        vlan_current = base_vlan or config.vlan_inner_base
        step = config.vlan_inner_step
        
        topology_type, is_connected = self.classify_topology()
        configured_links: List[LinkResponse] = []
        subnet_index = 0

        for u, v, data in self.graph.edges(data=True):
            is_u_inet = self.is_internet_node(u)
            is_v_inet = self.is_internet_node(v)
            is_inet = is_u_inet or is_v_inet

            link_name = data.get("link_name", f"link_{u}_{v}")

            if is_inet:
                vlan_inner = 0
                is_remote = False
                subnet_cidr = config.internet_subnet
            else:
                worker_u = self.placement_map.get(str(u))
                worker_v = self.placement_map.get(str(v))
                
                # is_remote activa la necesidad de QinQ entre workers
                is_remote = (worker_u != worker_v) if (worker_u is not None and worker_v is not None) else False
                vlan_inner = vlan_current
                vlan_current += step
                
                # Asignación de subred privada /30 para enlaces P2P (192.168.1.0/30, .4/30, .8/30...)
                base_fourth_octet = subnet_index * 4
                subnet_cidr = f"192.168.1.{base_fourth_octet}/30"
                subnet_index += 1

            configured_links.append(
                LinkResponse(
                    source_vm=u,
                    target_vm=v,
                    link_name=link_name,
                    vlan_inner=vlan_inner,
                    is_remote=is_remote,
                    is_internet=is_inet,
                    subnet_cidr=subnet_cidr
                )
            )

        vm_nodes = [n for n in self.graph.nodes() if not self.is_internet_node(n)]
        
        return NetworkPlanResponse(
            slice_id=slice_id,
            topology_type=topology_type,
            is_connected=is_connected,
            node_count=len(vm_nodes),
            edge_count=len(configured_links),
            links_configured=configured_links,
            message="Plan de red procesado con éxito."
        )

# Clase de compatibilidad con topology_graph legacy
class TopologyGraphProcessor:
    def __init__(self, links: List[LinkInput], placement_map: Dict[Union[int, str], Union[int, str]]):
        self.raw_links = links
        self.placement_map = {str(k): v for k, v in placement_map.items()}
        self.engine = TopologyEngine(
            nodes=[],
            links=[LinkRequest(source_vm=l.vm_a_id, target_vm=l.vm_b_id, link_name=l.link_name) for l in links],
            placement_map=self.placement_map
        )

    def detect_topology_type(self) -> Tuple[str, bool]:
        return self.engine.classify_topology()

    def process_link_allocations(self) -> List[LinkAllocationDetail]:
        plan = self.engine.calculate_plan(slice_id=1)
        details = []
        for i, raw in enumerate(self.raw_links):
            calc = plan.links_configured[i] if i < len(plan.links_configured) else None
            vlan = calc.vlan_inner if calc else 100 * (i + 1)
            is_remote = calc.is_remote if calc else False
            is_inet = calc.is_internet if calc else False
            details.append(
                LinkAllocationDetail(
                    link_name=raw.link_name,
                    vm_a_id=raw.vm_a_id,
                    iface_a=raw.iface_a,
                    vm_b_id=raw.vm_b_id,
                    iface_b=raw.iface_b,
                    vlan_inner=vlan,
                    is_remote=is_remote,
                    is_internet=is_inet,
                    subnet_cidr=calc.subnet_cidr if calc else None
                )
            )
        return details
