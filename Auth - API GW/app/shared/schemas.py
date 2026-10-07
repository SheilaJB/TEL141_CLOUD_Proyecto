from pydantic import BaseModel, ConfigDict

from app.shared.claims import Role, ServiceLevel


class UserInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    rol: Role
    nivel: ServiceLevel | None
