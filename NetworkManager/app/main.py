"""
main.py — Punto de Entrada del Network Manager (Módulo 8)

Procesa las solicitudes JSON que envía el Slice Manager,
usa el motor NetworkX para clasificar la topología y asignar VLANS internas.
"""
from fastapi import FastAPI, HTTPException
from app.schemas import NetworkDraftRequest, NetworkPlanResponse, LinkResponse
from app.network_logic import build_graph, classify_topology, calculate_network_plan
import networkx as nx

app = FastAPI(
    title="Network Manager - Grupo 3",
    description="Calcula topologías con NetworkX (R1B) y prepara aislamiento QinQ (R5)."
)

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "Network Manager"}

@app.post("/network/calculate", response_model=NetworkPlanResponse, tags=["Networking"])
async def calculate_plan(draft: NetworkDraftRequest):
    """
    Recibe el borrador (Draft) de máquinas y cables, y devuelve el plan matemático.
    """
    if not draft.nodes:
        raise HTTPException(status_code=400, detail="El draft no contiene nodos (VMs).")
    
    # 1. Construir el Grafo Matemático
    try:
        G = build_graph(draft.nodes, draft.links)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error al construir el grafo: {str(e)}")

    # 2. Requisito R1B: Dictaminar automáticamente la topología
    topology_type = classify_topology(G)

    # 3. Calcular la estrategia de aislamiento (vlan_inner y túneles)
    links_calc = calculate_network_plan(G, base_vlan_inner=10)

    # 4. Formatear la respuesta
    configured_links = [
        LinkResponse(
            source_vm=lc["source_vm"],
            target_vm=lc["target_vm"],
            vlan_inner=lc["vlan_inner"],
            is_remote=lc["is_remote"]
        )
        for lc in links_calc
    ]

    return NetworkPlanResponse(
        slice_id=draft.slice_id,
        topology_type=topology_type,
        links_configured=configured_links
    )
