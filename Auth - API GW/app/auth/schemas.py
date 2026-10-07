from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.shared.claims import Role, ServiceLevel
from app.shared.schemas import UserInfo


class LoginRequest(BaseModel):
    codigo: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)
    client: Literal["web", "cli"] = "web"


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class UserCreateRequest(BaseModel):
    codigo: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=1024)
    rol: Role
    nivel: ServiceLevel | None = None

    @model_validator(mode="after")
    def check_role_level(self) -> "UserCreateRequest":
        if (self.rol == "consumidor") != (self.nivel is not None):
            raise ValueError("consumidor requires a nivel; other roles must not have one")
        return self


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(default=None, min_length=12, max_length=1024)
    rol: Role | None = None
    nivel: ServiceLevel | None = None
    estado: Literal["ACTIVE", "INACTIVE", "BLOCKED"] | None = None

    @model_validator(mode="after")
    def check_not_empty(self) -> "UserUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in ("password", "rol", "estado")
        ):
            raise ValueError("password, rol, and estado cannot be null")
        return self


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_token: str | None = None
    user: UserInfo


class RefreshResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_token: str | None = None
