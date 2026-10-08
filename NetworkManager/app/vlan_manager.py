"""
vlan_manager.py — Gestor Thread-Safe para la asignación y liberación de VLANs.
Maneja:
1. vlan_slice: S-TAG (etiqueta externa de servicio para QinQ, una por slice).
2. vlan_inner: C-TAG (etiqueta interna por enlace dentro de un slice).
"""
import threading
from typing import Dict, Optional, Set
from app.config import config

class VlanManager:
    """
    Gestor atómico en memoria para el pool de VLANs.
    Garantiza reservas concurrentes seguras sin colisiones de S-TAG o C-TAG.
    """

    def __init__(self):
        self._lock = threading.Lock()
        
        # Pool de S-TAG (vlan_slice): e.g. 100 a 1000
        self._pool_start = config.vlan_pool_start
        self._pool_end = config.vlan_pool_end
        
        # Estado de VLANs externas
        self._available_vlans: Set[int] = set(range(self._pool_start, self._pool_end + 1))
        self._slice_to_vlan: Dict[int, int] = {}
        self._vlan_to_slice: Dict[int, int] = {}

        # Estado de VLANs internas por slice: {slice_id: {link_name: vlan_inner}}
        self._inner_allocations: Dict[int, Dict[str, int]] = {}
        self._next_inner_counter: Dict[int, int] = {}

    def reserve_vlan_slice(self, slice_id: int) -> int:
        """
        Reserva atómicamente una VLAN de servicio externa (S-TAG) para un slice.
        Si el slice ya tiene una asignada, la reutiliza (idempotencia).
        """
        with self._lock:
            if slice_id in self._slice_to_vlan:
                return self._slice_to_vlan[slice_id]

            if not self._available_vlans:
                raise ValueError("Pool de VLANs agotado: no hay vlan_slice disponibles en el rango configurado.")

            # Seleccionar la menor VLAN disponible para orden determinista
            chosen_vlan = min(self._available_vlans)
            self._available_vlans.remove(chosen_vlan)
            self._slice_to_vlan[slice_id] = chosen_vlan
            self._vlan_to_slice[chosen_vlan] = slice_id
            
            return chosen_vlan

    def get_vlan_slice(self, slice_id: int) -> Optional[int]:
        """Obtiene la VLAN de servicio de un slice si ya está reservada."""
        with self._lock:
            return self._slice_to_vlan.get(slice_id)

    def allocate_vlan_inner(self, slice_id: int, link_name: str) -> int:
        """
        Asigna una VLAN interna única (C-TAG) para un enlace dentro del slice.
        Idempotente: si el enlace ya tiene VLAN, retorna la existente.
        """
        with self._lock:
            slice_inners = self._inner_allocations.setdefault(slice_id, {})
            if link_name in slice_inners:
                return slice_inners[link_name]

            current = self._next_inner_counter.get(slice_id, config.vlan_inner_base)
            slice_inners[link_name] = current
            self._next_inner_counter[slice_id] = current + config.vlan_inner_step
            return current

    def get_vlan_inner(self, slice_id: int, link_name: str) -> Optional[int]:
        """Consulta la VLAN interna asignada a un enlace."""
        with self._lock:
            return self._inner_allocations.get(slice_id, {}).get(link_name)

    def release_slice(self, slice_id: int) -> Optional[int]:
        """
        Libera todos los recursos de VLAN de un slice (S-TAG y C-TAGs).
        Retorna la vlan_slice liberada o None si no existía.
        """
        with self._lock:
            vlan = self._slice_to_vlan.pop(slice_id, None)
            if vlan is not None:
                self._vlan_to_slice.pop(vlan, None)
                self._available_vlans.add(vlan)

            self._inner_allocations.pop(slice_id, None)
            self._next_inner_counter.pop(slice_id, None)
            
            return vlan

    def get_pool_status(self) -> dict:
        """Retorna estadísticas de uso del pool de VLANs."""
        with self._lock:
            total = (self._pool_end - self._pool_start + 1)
            available = len(self._available_vlans)
            used = total - available
            return {
                "total": total,
                "available": available,
                "used": used,
                "active_slices": len(self._slice_to_vlan)
            }

# Instancia singleton global
vlan_manager = VlanManager()
