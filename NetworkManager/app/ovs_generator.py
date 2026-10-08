"""
ovs_generator.py — Generador idempotente de comandos Open vSwitch y Linux.
Implementa:
1. Creación de puentes Br-Slice (distribución local) y Br-Provider (físico ens4).
2. Configuración QinQ dot1q-tunnel con S-TAG (vlan_slice) y C-TAG (vlan_inner).
3. Conexión de puertos TAP de VMs.
4. Reglas de IPtables (MASQUERADE en ens3, bloqueo de red de gestión, DNAT para SSH).
"""
from typing import List, Dict, Any
from app.config import config

class OvsCommandGenerator:
    """
    Genera listas ordenadas e idempotentes de comandos de consola para desplegar
    la infraestructura de red en los servidores físicos (Workers y Gateway Master).
    """

    @staticmethod
    def generate_worker_setup(
        slice_id: int,
        vlan_slice: int,
        worker_id: Any,
        taps_with_vlan: List[Dict[str, Any]],
        has_remote_links: bool = True
    ) -> List[str]:
        """
        Genera la secuencia de comandos OVS para un worker específico.
        taps_with_vlan: [{'tap_name': 'tap-vm1-eth0', 'vlan_inner': 100}]
        """
        br_slice = f"br-sl-{slice_id}"
        br_provider = config.provider_bridge  # br-provider o br-wk
        ens4 = config.data_iface
        
        cmds = []
        # 1. Asegurar la existencia de los puentes
        cmds.append(f"sudo ovs-vsctl --may-exist add-br {br_slice}")
        cmds.append(f"sudo ovs-vsctl --may-exist add-br {br_provider}")
        cmds.append(f"sudo ovs-vsctl --may-exist add-port {br_provider} {ens4}")
        cmds.append(f"sudo ovs-vsctl set port {ens4} vlan_mode=trunk")

        # 2. Agregar interfaces TAP de las VMs locales al puente del slice con su C-TAG
        for item in taps_with_vlan:
            tap = item["tap_name"]
            vlan_inner = item.get("vlan_inner", 0)
            cmds.append(f"sudo ip tuntap add {tap} mode tap 2>/dev/null || true")
            cmds.append(f"sudo ip link set {tap} up")
            
            if vlan_inner > 0:
                # Puerto de acceso con VLAN interna del enlace
                cmds.append(f"sudo ovs-vsctl --may-exist add-port {br_slice} {tap} tag={vlan_inner}")
            else:
                # Sin etiqueta interna (acceso directo o br-inet)
                cmds.append(f"sudo ovs-vsctl --may-exist add-port {br_slice} {tap}")

        # 3. Si hay enlaces que cruzan hacia otros workers, configurar el túnel QinQ
        if has_remote_links and vlan_slice > 0:
            veth_sl = f"veth-sl-{slice_id}"
            veth_wk = f"veth-wk-{slice_id}"
            
            cmds.append(f"sudo ip link add {veth_sl} type veth peer name {veth_wk} 2>/dev/null || true")
            cmds.append(f"sudo ip link set {veth_sl} up")
            cmds.append(f"sudo ip link set {veth_wk} up")
            
            # En br_slice: troncal que transporta todos los C-TAGs internos
            cmds.append(f"sudo ovs-vsctl --may-exist add-port {br_slice} {veth_sl} -- set port {veth_sl} vlan_mode=trunk")
            
            # En br_provider: dot1q-tunnel que agrega el S-TAG del slice (QinQ)
            cmds.append(
                f"sudo ovs-vsctl --may-exist add-port {br_provider} {veth_wk} "
                f"-- set port {veth_wk} vlan_mode=dot1q-tunnel tag={vlan_slice} other_config:qinq-ethtype=802.1q"
            )

        return cmds

    @staticmethod
    def generate_gateway_setup(
        vm_public_ips: List[str] = None,
        ssh_forward_ports: Dict[int, str] = None
    ) -> List[str]:
        """
        Genera la configuración de Capa 3 e IPtables en el Gateway (Server 4 / Master).
        - MASQUERADE de salida por ens3.
        - Bloqueo de tráfico hacia la red de gestión.
        - Port forwarding DNAT para acceso SSH entrante.
        """
        br_inet = config.internet_bridge
        ens3 = config.management_iface
        subnet = config.internet_subnet
        gw_ip = config.internet_gw_ip
        restricted_mgmt = config.mgmt_network_restricted

        cmds = [
            f"sudo ovs-vsctl --may-exist add-br {br_inet}",
            f"sudo ip addr add {gw_ip}/24 dev {br_inet} 2>/dev/null || true",
            f"sudo ip link set {br_inet} up",
            "sudo sysctl -w net.ipv4.ip_forward=1",
            
            # NAT Egress por ens3
            f"sudo iptables -t nat -C POSTROUTING -s {subnet} -o {ens3} -j MASQUERADE 2>/dev/null || "
            f"sudo iptables -t nat -A POSTROUTING -s {subnet} -o {ens3} -j MASQUERADE",
            
            # Aislamiento: Bloquear acceso desde VMs hacia la red de gestión interna (10.0.10.0/24)
            f"sudo iptables -C FORWARD -s {subnet} -d {restricted_mgmt} -j DROP 2>/dev/null || "
            f"sudo iptables -I FORWARD 1 -s {subnet} -d {restricted_mgmt} -j DROP",
            
            # Permitir reenvío general
            f"sudo iptables -C FORWARD -s {subnet} -j ACCEPT 2>/dev/null || "
            f"sudo iptables -A FORWARD -s {subnet} -j ACCEPT",
            f"sudo iptables -C FORWARD -d {subnet} -j ACCEPT 2>/dev/null || "
            f"sudo iptables -A FORWARD -d {subnet} -j ACCEPT",
        ]

        # Port Forwarding (DNAT) para SSH
        if ssh_forward_ports:
            for external_port, vm_ip in ssh_forward_ports.items():
                cmds.append(
                    f"sudo iptables -t nat -C PREROUTING -i {ens3} -p tcp --dport {external_port} "
                    f"-j DNAT --to-destination {vm_ip}:22 2>/dev/null || "
                    f"sudo iptables -t nat -A PREROUTING -i {ens3} -p tcp --dport {external_port} "
                    f"-j DNAT --to-destination {vm_ip}:22"
                )

        return cmds

    @staticmethod
    def generate_slice_teardown(slice_id: int) -> List[str]:
        """Genera los comandos de limpieza al eliminar un slice."""
        br_slice = f"br-sl-{slice_id}"
        br_provider = config.provider_bridge
        veth_sl = f"veth-sl-{slice_id}"
        veth_wk = f"veth-wk-{slice_id}"

        return [
            f"sudo ovs-vsctl --if-exists del-port {br_provider} {veth_wk} || true",
            f"sudo ovs-vsctl --if-exists del-br {br_slice} || true",
            f"sudo ip link delete {veth_sl} 2>/dev/null || true",
        ]
