from typing import Literal

from pydantic import BaseModel, ConfigDict

Role = Literal["consumidor", "operador", "admin"]
ServiceLevel = Literal["basico", "avanzado"]


class Claims(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sub: str
    cod: str
    rol: Role
    nivel: ServiceLevel | None
    iat: int
    exp: int
    jti: str
