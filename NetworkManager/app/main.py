"""
main.py — Punto de Entrada Principal del Servicio NetworkManager .
Orquestador de Capa 2 / Capa 3, motor matemático NetworkX (R1B),
aislamiento QinQ (R5), asignación IPAM y generación de comandos OVS/IPtables.
"""
import logging
from typing import Dict, List, Any
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import config
from app.schemas import (
    NetworkDraftRequest, NetworkPlanResponse,
    AllocateRequest, AllocateResponse, ReleaseRequest,
    VlanAvailableResponse, SliceNetworkResponse, NetworkDetail, InterfaceDetail,
    OvsCommandResponse, OvsWorkerCommand,
    AdapterActionInput, AdapterActionOutput
)
from app.topology_engine import TopologyEngine
from app.vlan_manager import vlan_manager
from app.ipam import ipam
from app.ovs_generator import OvsCommandGenerator
from app.action_handler import ActionHandler

# Configuración de Logging Estructurado
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("NetworkManager")

app = FastAPI(
    title=f"{config.service_name} API",
    version="2.0.0",
    description=(
        "Servicio de Red y Aislamiento para la nube privada PUCP (TEL141 - Grupo 3). "
        "Soporta clasificación topológica (NetworkX), gestión de VLANs (QinQ), IPAM y comandos OVS."
    )
)

# Middleware CORS para el Frontend del orquestador
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. VERIFICACIÓN DE ESTADO (HEALTH CHECK)
@app.get("/health", tags=["Salud del Sistema"])
async def health_check():
    """Retorna el estado de operatividad del NetworkManager y el pool de VLANs."""
    vlan_status = vlan_manager.get_pool_status()
    return {
        "status": "healthy",
        "service": config.service_name,
        "vlan_pool": vlan_status,
        "gateway_ip": config.gateway_ip,
        "internet_subnet": config.internet_subnet
    }

# 2. CÁLCULO MATEMÁTICO DE TOPOLOGÍA (R1B & R5)
@app.post("/network/calculate", response_model=NetworkPlanResponse, tags=["Planificación Topológica"])
async def calculate_topology_plan(draft: NetworkDraftRequest):
    """
    Recibe el borrador con la lista de VMs y enlaces lógicos.
    Usa NetworkX para clasificar la topología (Malla, Anillo, Lineal, Árbol, etc.)
    y proyecta las VLANs internas necesarias para el aislamiento L2.
    """
    if not draft.nodes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El borrador no contiene nodos (VMs)."
        )
    logger.info(f"Procesando cálculo topológico para Slice {draft.slice_id} con {len(draft.nodes)} VMs.")
    try:
        engine = TopologyEngine(nodes=draft.nodes, links=draft.links)
        plan = engine.calculate_plan(slice_id=draft.slice_id)
        return plan
    except Exception as e:
        logger.error(f"Error calculando plan topológico: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en el motor NetworkX: {str(e)}"
        )

# 3. ASIGNACIÓN DE RECURSOS (ALLOCATE)
@app.post("/networking/allocate", response_model=AllocateResponse, tags=["Asignación de Recursos"])
async def allocate_resources(req: AllocateRequest):
    """
    Reserva la VLAN de servicio (S-TAG QinQ) para el Slice, genera C-TAGs para cada enlace,
    crea las direcciones MAC para cada interfaz de VM y devuelve el plan detallado.
    """
    logger.info(f"Reservando recursos de red para Slice {req.slice_id}.")
    
    try:
        vlan_slice = vlan_manager.reserve_vlan_slice(req.slice_id)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(ve))

    bridge_name = f"br-sl-{req.slice_id}"
    networks_response: List[NetworkDetail] = []
    
    # Procesar enlaces con TopologyEngine para clasificar topología
    raw_nodes = [req.links[0].vm_a_id] if req.links else []
    
    for idx, link in enumerate(req.links):
        u_str = str(link.vm_a_id)
        v_str = str(link.vm_b_id)
        
        is_u_inet = TopologyEngine.is_internet_node(u_str)
        is_v_inet = TopologyEngine.is_internet_node(v_str)
        is_internet = is_u_inet or is_v_inet

        if is_internet:
            # Enlace a Internet: Se conecta directamente a br-inet sin QinQ
            target_vm_id = link.vm_a_id if is_v_inet else link.vm_b_id
            target_iface = link.iface_a if is_v_inet else link.iface_b
            worker_id = req.placement_map.get(str(target_vm_id))
            pub_ip, _ = ipam.allocate_public_ip(req.slice_id, str(target_vm_id))
            mac = ipam.generate_mac(seed_key=f"{req.slice_id}_{target_vm_id}_{target_iface}")
            tap = f"tap-vm{target_vm_id}-{target_iface}"
            networks_response.append(
                NetworkDetail(
                    network_id=idx + 1,
                    id=idx + 1,
                    link_name=link.link_name,
                    vlan_inner=0,
                    is_remote=False,
                    subnet_cidr=config.internet_subnet,
                    interfaces=[
                        InterfaceDetail(
                            vm_id=target_vm_id,
                            interface_name=target_iface,
                            tap_name=tap,
                            mac_address=mac,
                            ip_address=pub_ip,
                            bridge_name=config.internet_bridge,
                            worker_id=worker_id
                        )
                    ]
                )
            )
            continue

        # Enlace Privado entre 2 VMs
        worker_a = req.placement_map.get(u_str)
        worker_b = req.placement_map.get(v_str)
        is_remote = (worker_a != worker_b) if (worker_a is not None and worker_b is not None) else False
        vlan_inner = vlan_manager.allocate_vlan_inner(req.slice_id, link.link_name)
        cidr, ip_a, ip_b = ipam.allocate_p2p_subnet(req.slice_id, link.link_name)
        mac_a = ipam.generate_mac(seed_key=f"{req.slice_id}_{u_str}_{link.iface_a}")
        tap_a = f"tap-vm{u_str}-{link.iface_a}"
        mac_b = ipam.generate_mac(seed_key=f"{req.slice_id}_{v_str}_{link.iface_b}")
        tap_b = f"tap-vm{v_str}-{link.iface_b}"
        networks_response.append(
            NetworkDetail(
                network_id=idx + 1,
                id=idx + 1,
                link_name=link.link_name,
                vlan_inner=vlan_inner,
                is_remote=is_remote,
                subnet_cidr=cidr,
                interfaces=[
                    InterfaceDetail(
                        vm_id=link.vm_a_id,
                        interface_name=link.iface_a,
                        tap_name=tap_a,
                        mac_address=mac_a,
                        ip_address=ip_a,
                        bridge_name=bridge_name,
                        worker_id=worker_a
                    ),
                    InterfaceDetail(
                        vm_id=link.vm_b_id,
                        interface_name=link.iface_b,
                        tap_name=tap_b,
                        mac_address=mac_b,
                        ip_address=ip_b,
                        bridge_name=bridge_name,
                        worker_id=worker_b
                    )
                ]
            )
        )

    return AllocateResponse(
        slice_id=req.slice_id,
        vlan_slice=vlan_slice,
        bridge_name=bridge_name,
        networks=networks_response
    )

# 4. LIBERACIÓN DE RECURSOS (RELEASE)
@app.post("/networking/release", tags=["Asignación de Recursos"])
async def release_resources(req: ReleaseRequest):
    """Libera el S-TAG del slice y toda su memoria IPAM."""
    released_vlan = vlan_manager.release_slice(req.slice_id)
    ipam.release_slice(req.slice_id)
    logger.info(f"Recursos liberados para Slice {req.slice_id}. S-TAG liberado: {released_vlan}")
    return {
        "status": "success",
        "slice_id": req.slice_id,
        "released_vlan_slice": released_vlan
    }

# 5. DISPONIBILIDAD DEL POOL DE VLANS
@app.get("/networking/vlans/available", response_model=VlanAvailableResponse, tags=["Pool de VLANs"])
async def get_available_vlans():
    """Consulta la métrica de VLANs disponibles para el Operador / Monitoreo."""
    return vlan_manager.get_pool_status()

# 6. GENERADOR DE COMANDOS OVS POR WORKER
@app.get("/networking/ovs/commands/{slice_id}", response_model=OvsCommandResponse, tags=["Generador OVS"])
async def get_ovs_commands(slice_id: int):
    """
    Genera la lista exacta de comandos Open vSwitch e IPtables requeridos
    para aprovisionar los puentes y túneles QinQ en cada servidor físico.
    """
    vlan_slice = vlan_manager.get_vlan_slice(slice_id)
    if not vlan_slice:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El Slice {slice_id} no tiene una vlan_slice asignada."
        )

    bridge_name = f"br-sl-{slice_id}"
    
    # Generamos los comandos para un clúster estándar de 3 workers
    worker_cmds: List[OvsWorkerCommand] = []
    for w_id in [1, 2, 3]:
        cmds = OvsCommandGenerator.generate_worker_setup(
            slice_id=slice_id,
            vlan_slice=vlan_slice,
            worker_id=w_id,
            taps_with_vlan=[
                {"tap_name": f"tap-vm{w_id}-eth0", "vlan_inner": 100 * w_id}
            ],
            has_remote_links=True
        )
        worker_cmds.append(OvsWorkerCommand(worker_id=w_id, commands=cmds))

    # Comandos para el Gateway Centralizado
    gw_cmds = OvsCommandGenerator.generate_gateway_setup(
        vm_public_ips=[f"10.60.5.{10 + slice_id}"],
        ssh_forward_ports={5000 + slice_id: f"10.60.5.{10 + slice_id}"}
    )

    return OvsCommandResponse(
        slice_id=slice_id,
        vlan_slice=vlan_slice,
        bridge_name=bridge_name,
        workers=worker_cmds,
        gateway_commands=gw_cmds
    )

# 7. CONTRATO ATÓMICO DE ADAPTADOR (TEMPORAL / SLICE MANAGER)
@app.post("/networking/action", response_model=AdapterActionOutput, tags=["Adaptador Temporal"])
async def execute_adapter_action(action_input: AdapterActionInput):
    """
    Endpoint atómico compatible con el Slice Manager y el flujo de Temporal.
    Recibe CREATE_LINK, ATTACH_PORT, SET_PUBLIC y devuelve los 'facts' estructurados
    (MAC, IP, VLAN tags, puentes) para actualizar la base de datos PostgreSQL.
    """
    logger.info(f"Ejecutando acción de red: {action_input.action.op} para Slice {action_input.slice_id}")
    try:
        output = ActionHandler.handle_action(action_input)
        return output
    except Exception as e:
        logger.error(f"Fallo ejecutando acción {action_input.action.op}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en acción de red: {str(e)}"
        )
