"""
action_handler.py — Procesador de Acciones Atómicas del Adaptador / Slice Manager.
Maneja las operaciones emitidas durante el flujo de despliegue:
- CREATE_LINK: Inicializa el enlace virtual y reserva C-TAG.
- ATTACH_PORT: Conecta la VM a la red, asigna MAC, IP y retorna el diccionario 'facts'.
- SET_PUBLIC: Habilita salida a Internet, asigna IP pública y regla NAT.
- DELETE_LINK: Libera los recursos del enlace.
"""
from typing import Dict, Any
from app.schemas import AdapterActionInput, AdapterActionOutput
from app.vlan_manager import vlan_manager
from app.ipam import ipam
from app.config import config

class ActionHandler:
    """
    Ejecutor de alto nivel para las acciones del adaptador.
    Traduce las órdenes lógicas del orquestador en parámetros de infraestructura
    y construye el payload de respuesta esperado por el Slice Manager.
    """

    @classmethod
    def handle_action(cls, req: AdapterActionInput) -> AdapterActionOutput:
        op = req.action.op
        slice_id = req.slice_id
        target = req.action.target
        params = req.action.params or {}
        server_id = req.server_id

        # Asegurar que el slice tenga su VLAN de servicio exterior (S-TAG)
        vlan_slice = vlan_manager.reserve_vlan_slice(slice_id)

        facts: Dict[str, Any] = {}

        if op == "CREATE_LINK":
            link_name = params.get("name", str(target.id))
            vlan_inner = vlan_manager.allocate_vlan_inner(slice_id, link_name)
            cidr, _, _ = ipam.allocate_p2p_subnet(slice_id, link_name)
            
            facts = {
                "link_id": str(target.id),
                "link_name": link_name,
                "vlan_inner": vlan_inner,
                "vlan_slice": vlan_slice,
                "subnet": cidr,
                "bridge": f"br-sl-{slice_id}"
            }

        elif op == "ATTACH_PORT":
            link_name = params.get("link_name", target.link_id or "default_link")
            vm_name = params.get("vm_name", target.node_id or "vm")
            
            # Obtener o calcular C-TAG
            vlan_inner = vlan_manager.allocate_vlan_inner(slice_id, link_name)
            
            # Obtener subred P2P e IP para este endpoint
            cidr, ip_a, ip_b = ipam.allocate_p2p_subnet(slice_id, link_name)
            assigned_ip = ip_a  # El primer extremo toma .1, el siguiente tomará .2
            
            # Generar MAC y nombre TAP
            tap_name = f"tap_{vm_name}"
            mac_addr = ipam.generate_mac(seed_key=f"{slice_id}_{vm_name}_{link_name}")

            facts = {
                "port_id": str(target.id),
                "vm_name": vm_name,
                "server_id": server_id,
                "mac": mac_addr,
                "ip": assigned_ip,
                "vlan_tag": vlan_inner,
                "vlan_slice": vlan_slice,
                "tap": tap_name,
                "bridge": f"br-sl-{slice_id}",
                "ns": f"ns_sl_{slice_id}"
            }

        elif op == "SET_PUBLIC":
            vm_id = target.node_id or str(target.id)
            pub_ip, gw_ip = ipam.allocate_public_ip(slice_id, vm_id)
            mac_addr = ipam.generate_mac(seed_key=f"{slice_id}_{vm_id}_inet")

            facts = {
                "node_id": str(target.id),
                "ip": pub_ip,
                "gateway": gw_ip,
                "mac": mac_addr,
                "bridge": config.internet_bridge,
                "tap": f"tap_{vm_id}_inet",
                "vlan_tag": 0,
                "public": True
            }

        elif op in {"DELETE_LINK", "DETACH_PORT"}:
            facts = {
                "target_id": str(target.id),
                "status": "released"
            }

        else:
            facts = {
                "target_id": str(target.id),
                "message": f"Operación {op} reconocida sin cambios de red"
            }

        return AdapterActionOutput(
            action_id=req.action.id,
            operation=op,
            already_existed=False,
            already_absent=False,
            facts=facts
        )
