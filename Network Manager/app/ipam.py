"""
ipam.py — IP Address Management & Generador de Direcciones MAC.
Maneja:
1. Generación de MACs virtuales con prefijo KVM (52:54:00).
2. Asignación de subredes punto a punto (/30) privadas para enlaces L2 (ej. 192.168.1.0/30).
3. Asignación de IPs para VMs públicas en la red 10.60.5.0/24 (Gateway 10.60.5.1).
"""
import random
import threading
from typing import Dict, Tuple, Optional
from app.config import config

class IPAM:
    """
    Gestor de Direccionamiento IP y MAC para las interfaces virtuales de los Slices.
    """

    def __init__(self):
        self._lock = threading.Lock()
        
        # Subredes /30 privadas para enlaces P2P por slice: {slice_id: {link_name: subnet_base_int}}
        self._p2p_allocations: Dict[int, Dict[str, int]] = {}
        self._p2p_counter: Dict[int, int] = {}
        
        # IPs públicas en 10.60.5.0/24 por slice: {slice_id: {vm_id: ip_str}}
        self._public_ips: Dict[int, Dict[str, str]] = {}
        self._next_public_ip: int = 10  # Comenzar en .10 (e.g. 10.60.5.10)

        # Registro de MACs asignadas para evitar duplicados
        self._mac_registry: Dict[str, str] = {}

    def generate_mac(self, seed_key: Optional[str] = None) -> str:
        """
        Genera una dirección MAC estandarizada para KVM/QEMU:
        Prefijo IEEE: 52:54:00 (QEMU virtual NICs) + 3 bytes aleatorios o basados en seed.
        """
        with self._lock:
            if seed_key and seed_key in self._mac_registry:
                return self._mac_registry[seed_key]

            while True:
                b1 = random.randint(0x00, 0x7f)
                b2 = random.randint(0x00, 0xff)
                b3 = random.randint(0x00, 0xff)
                mac = f"52:54:00:{b1:02x}:{b2:02x}:{b3:02x}"
                if mac not in self._mac_registry.values():
                    if seed_key:
                        self._mac_registry[seed_key] = mac
                    return mac

    def allocate_p2p_subnet(self, slice_id: int, link_name: str) -> Tuple[str, str, str]:
        """
        Asigna una subred /30 para un enlace punto a punto privado entre dos VMs.
        Retorna (CIDR, IP_A, IP_B).
        Ejemplo: ("192.168.1.0/30", "192.168.1.1", "192.168.1.2")
        """
        with self._lock:
            slice_links = self._p2p_allocations.setdefault(slice_id, {})
            if link_name in slice_links:
                base_fourth = slice_links[link_name]
            else:
                idx = self._p2p_counter.get(slice_id, 0)
                base_fourth = idx * 4
                slice_links[link_name] = base_fourth
                self._p2p_counter[slice_id] = idx + 1

            cidr = f"192.168.1.{base_fourth}/30"
            ip_a = f"192.168.1.{base_fourth + 1}"
            ip_b = f"192.168.1.{base_fourth + 2}"
            return (cidr, ip_a, ip_b)

    def allocate_public_ip(self, slice_id: int, vm_id: str) -> Tuple[str, str]:
        """
        Asigna una dirección IP en la subred pública de salida a Internet (10.60.5.0/24).
        Retorna (IP_VM, Gateway_IP).
        """
        with self._lock:
            slice_pub = self._public_ips.setdefault(slice_id, {})
            vm_key = str(vm_id)
            if vm_key in slice_pub:
                return (slice_pub[vm_key], config.internet_gw_ip)

            if self._next_public_ip > 250:
                raise ValueError("Pool de IPs públicas agotado en 10.60.5.0/24")

            allocated_ip = f"10.60.5.{self._next_public_ip}"
            self._next_public_ip += 1
            slice_pub[vm_key] = allocated_ip
            return (allocated_ip, config.internet_gw_ip)

    def get_public_ips_for_slice(self, slice_id: int) -> Dict[str, str]:
        """Retorna el mapa de {vm_id: ip_publica} para un slice."""
        with self._lock:
            return dict(self._public_ips.get(slice_id, {}))

    def release_slice(self, slice_id: int):
        """Libera los recursos de direccionamiento de un slice."""
        with self._lock:
            self._p2p_allocations.pop(slice_id, None)
            self._p2p_counter.pop(slice_id, None)
            self._public_ips.pop(slice_id, None)

# Instancia singleton global
ipam = IPAM()
