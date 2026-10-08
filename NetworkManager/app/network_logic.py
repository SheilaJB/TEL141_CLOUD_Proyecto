"""
network_logic.py — Motor matemático usando NetworkX

Cumple el requisito R1B: "dictaminar automáticamente si la red es Lineal, Malla, Árbol, Anillo..."
Cumple el requisito R5 (Parcial): Asignación matemática de `vlan_inner` y detección de `is_remote`.
"""
import networkx as nx
from typing import Tuple, Dict

def build_graph(nodes_data: list, links_data: list) -> nx.Graph:
    """Construye un grafo de NetworkX a partir del Draft JSON."""
    G = nx.Graph()
    
    # Agregar nodos y guardar en qué worker físico están como atributo
    for node in nodes_data:
        G.add_node(node.vm_id, worker_id=node.worker_id)
        
    # Agregar enlaces (aristas)
    for link in links_data:
        G.add_edge(link.source_vm, link.target_vm)
        
    return G

def classify_topology(G: nx.Graph) -> str:
    """
    Analiza matemáticamente el grafo para dictaminar el tipo de topología (Rúbrica R1B).
    """
    if len(G.nodes) <= 1:
        return "Punto Único"

    num_nodes = G.number_of_nodes()
    num_edges = G.number_of_edges()

    # 1. ¿Es una Malla Completa (Full Mesh)?
    # En una malla completa, el número de enlaces es n*(n-1)/2
    max_edges = (num_nodes * (num_nodes - 1)) / 2
    if num_edges == max_edges:
        return "Malla (Full Mesh)"

    # 2. ¿Es un Árbol o Lineal? (Sin ciclos y conectado)
    if nx.is_tree(G):
        # Si el grado máximo (conexiones por nodo) es <= 2, es Lineal (o Bus simple)
        max_degree = max(dict(G.degree()).values())
        if max_degree <= 2:
            return "Lineal"
        else:
            return "Árbol (Estrella/Jerárquico)"

    # 3. ¿Es un Anillo?
    # Un anillo perfecto tiene todos los nodos con grado 2 exacto y forma un solo ciclo
    degrees = list(dict(G.degree()).values())
    if all(d == 2 for d in degrees) and nx.is_connected(G):
        return "Anillo"

    # 4. Si tiene ciclos pero no es anillo perfecto ni malla completa
    return "Malla Parcial (Topología Mixta)"

def calculate_network_plan(G: nx.Graph, base_vlan_inner: int = 10) -> list:
    """
    Itera por los enlaces del grafo y asigna a cada uno:
    - Una vlan_inner única (10, 20, 30...) para aislamiento de broadcast (R5).
    - Un flag is_remote comparando los worker_id físicos.
    """
    links_configured = []
    current_vlan = base_vlan_inner
    
    for u, v in G.edges():
        # Obtener los atributos 'worker_id' de cada nodo
        worker_u = G.nodes[u].get('worker_id')
        worker_v = G.nodes[v].get('worker_id')
        
        # Si están en distinto hardware físico, necesitan túnel (QinQ en el adaptador)
        is_remote = (worker_u != worker_v)
        
        links_configured.append({
            "source_vm": u,
            "target_vm": v,
            "vlan_inner": current_vlan,
            "is_remote": is_remote
        })
        
        current_vlan += 10 # Saltos de 10 en 10 por limpieza (10, 20, 30...)

    return links_configured
