from contracts.base import ContractModel


class ServerHost(ContractModel):
    server_id: int
    management_ip: str
