from pydantic import BaseModel
import os

class NetworkConfig(BaseModel):
    service_name: str = "NetworkManager"
    host: str = os.getenv("NETWORK_MANAGER_HOST", "0.0.0.0")
    port: int = int(os.getenv("NETWORK_MANAGER_PORT", "8000"))
    
    # Pools de VLAN
    vlan_pool_start: int = int(os.getenv("VLAN_POOL_START", "100"))
    vlan_pool_end: int = int(os.getenv("VLAN_POOL_END", "999"))
    vlan_inner_base: int = int(os.getenv("VLAN_INNER_BASE", "2"))
    vlan_inner_step: int = int(os.getenv("VLAN_INNER_STEP", "1"))
    public_vid: int = int(os.getenv("PUBLIC_VID", "4000"))
    
    # Infraestructura Física & Puentes
    management_iface: str = os.getenv("MANAGEMENT_IFACE", "ens3")
    data_iface: str = os.getenv("DATA_IFACE", "ens4")
    provider_bridge: str = os.getenv("PROVIDER_BRIDGE", "br-provider")
    internet_bridge: str = os.getenv("INTERNET_BRIDGE", "br-provider")
    
    # Redes y Enrutamiento (Guía Maestra & Examen 1)
    gateway_ip: str = os.getenv("GATEWAY_IP", "10.60.5.1")
    internet_subnet: str = os.getenv("INTERNET_SUBNET", "10.60.5.0/24")
    internet_gw_ip: str = os.getenv("INTERNET_GW_IP", "10.60.5.1")
    private_p2p_base: str = os.getenv("PRIVATE_P2P_BASE", "192.168.1.0/24")
    mgmt_network_restricted: str = os.getenv("MGMT_NETWORK_RESTRICTED", "10.0.10.0/24")

config = NetworkConfig()
