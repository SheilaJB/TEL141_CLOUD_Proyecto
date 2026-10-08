import networkx as nx
from typing import List, Dict, Tuple
from app.schemas import LinkInput, LinkAllocationDetail

class TopologyGraphProcessor:
    """
    Motor de procesamiento de red basado en NetworkX.
    Calcula vlan_inner, is_remote y clasifica las 5 topologías exigidas:
    Lineal, Anillo, Árbol, Malla y Bus.
    """

    def __init__(self, links: List[LinkInput], placement_map: Dict[int, int]):
        self.raw_links = links
        self.placement_map = placement_map
        self.graph = nx.Graph()
        self._build_graph()

    def _build_graph(self):
        """Construye el grafo NetworkX con los nodos de cómputo y enlaces."""
        for link in self.raw_links:
            self.graph.add_edge(
                link.vm_a_id,
                link.vm_b_id,
                link_name=link.link_name,
                iface_a=link.iface_a,
                iface_b=link.iface_b
            )

    def detect_topology_type(self) -> Tuple[str, bool]:
        """
        Analiza el grafo con NetworkX para clasificar las 5 topologías del sistema.
        Excluye el nodo 0 (Internet/WAN) para evaluar solo la topología virtual de las VMs.
        """
        vm_nodes = [node for node in self.graph.nodes() if node != 0]
        if not vm_nodes:
            return "Vacía", False

        subgraph = self.graph.subgraph(vm_nodes)

        # 1. Verificar si todas las VMs están interconectadas
        if not nx.is_connected(subgraph):
            return "Desconectada (Inválida)", False

        num_nodes = subgraph.number_of_nodes()
        num_edges = subgraph.number_of_edges()
        degrees = [d for n, d in subgraph.degree()]
        max_degree = max(degrees, default=0)

        # Si solo hay 1 VM, es una topología trivial válida
        if num_nodes == 1:
            return "Individual", True

        # A. Topología MALLA (Mesh): Totalmente conectada N*(N-1)/2 o alta densidad de aristas
        max_possible_edges = (num_nodes * (num_nodes - 1)) // 2
        if num_edges == max_possible_edges and num_nodes > 2:
            return "Malla", True

        # B. Topología ANILLO (Ring): N nodos y N aristas, todos los nodos con grado 2
        if num_nodes >= 3 and num_edges == num_nodes and all(d == 2 for d in degrees):
            return "Anillo", True

        # C. Topologías ÁRBOLES / LINEALES / BUS (Sin ciclos)
        if nx.is_tree(subgraph):
            # Lineal: Grado máximo es 2 (cadena de nodos)
            if max_degree <= 2:
                return "Lineal", True
            
            # Bus: En L2 virtual, un Bus se modela como un canal/troncal central donde las VMs
            # se conectan (nodo concentrador o switch virtual distribuido)
            if max_degree >= (num_nodes - 1) and num_nodes > 3:
                return "Bus", True

            # Árbol: Estructura jerárquica con bifurcaciones
            return "Árbol", True

        # Si tiene ciclos parciales (Malla incompleta / Híbrida)
        if num_edges > num_nodes:
            return "Malla", True

        return "Personalizada", True

    def process_link_allocations(self) -> List[LinkAllocationDetail]:
        """
        Recorre las aristas con NetworkX y calcula los datos requeridos para QinQ:
        - vlan_inner: C-TAG (VLAN interna por enlace).
        - is_remote: Determina si cruza hosts físicos (requiere S-TAG vlan_slice).
        - is_internet: Identifica salientes hacia br-inet.
        """
        allocated_links: List[LinkAllocationDetail] = []
        vlan_counter = 100

        for u, v, data in self.graph.edges(data=True):
            link_name = data["link_name"]
            iface_a = data["iface_a"]
            iface_b = data["iface_b"]

            is_internet = (u == 0 or v == 0)

            if is_internet:
                is_remote = False
                vlan_inner = 0
            else:
                worker_a = self.placement_map.get(u)
                worker_b = self.placement_map.get(v)
                
                # is_remote determina si el Adaptador Linux aplicará QinQ en el bridge br-wk
                is_remote = (worker_a != worker_b) if (worker_a and worker_b) else False
                vlan_inner = vlan_counter
                vlan_counter += 100

            allocated_links.append(
                LinkAllocationDetail(
                    link_name=link_name,
                    vm_a_id=u,
                    iface_a=iface_a,
                    vm_b_id=v,
                    iface_b=iface_b,
                    vlan_inner=vlan_inner,
                    is_remote=is_remote,
                    is_internet=is_internet
                )
            )

        return allocated_links
