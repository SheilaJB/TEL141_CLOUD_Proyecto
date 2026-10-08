from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

RoleType = Literal["consumidor", "operador", "admin"]
LevelType = Literal["basico", "avanzado"]
StateType = Literal["ACTIVE", "INACTIVE", "BLOCKED"]


class LoginRequest(BaseModel):
    username: Optional[str] = Field(None, description="Código de usuario (compatibilidad frontend)")
    codigo: Optional[str] = Field(None, description="Código de usuario")
    password: str = Field(..., description="Contraseña en texto plano")
    client: Literal["web", "cli"] = Field("cli", description="Tipo de cliente (web usa cookie, cli usa body)")

    @model_validator(mode="after")
    def check_identifier(self):
        ident = self.codigo or self.username
        if not ident:
            raise ValueError("Debe ingresar un código o nombre de usuario.")
        self.codigo = ident
        self.username = ident
        return self


class TokenResponse(BaseModel):
    access_token: str = Field(..., description="Token JWT RS256 de acceso rápido (15 min)")
    token_type: str = Field("bearer", description="Tipo de token (Bearer)")
    expires_in: int = Field(900, description="Tiempo de expiración en segundos")
    refresh_token: Optional[str] = Field(None, description="Token de refresco persistente en BD")
    rol: str = Field(..., description="Rol del usuario")
    nivel: Optional[str] = Field(None, description="Nivel de servicio del consumidor")


class RefreshTokenRequest(BaseModel):
    refresh_token: Optional[str] = Field(None, description="Token de refresco")


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900
    refresh_token: Optional[str] = None


class UserVerifyResponse(BaseModel):
    id: int
    codigo: str
    rol: str
    nivel: Optional[str] = None
    exp: int


class UserInfo(BaseModel):
    id: int
    codigo: str
    rol: str
    nivel: Optional[str] = None
    estado: Optional[str] = "ACTIVE"
    fecha_creacion: Optional[datetime] = None


class UserCreate(BaseModel):
    codigo: str = Field(..., description="Código único de usuario")
    password: str = Field(..., min_length=6, description="Contraseña inicial")
    rol: Optional[str] = Field(None, description="Rol ('consumidor', 'operador', 'admin')")
    rol_nombre: Optional[str] = Field(None, description="Alias de rol")
    nivel: Optional[str] = Field(None, description="Nivel ('basico', 'avanzado')")
    nivel_nombre: Optional[str] = Field(None, description="Alias de nivel")

    @model_validator(mode="after")
    def unify_names(self):
        r = self.rol or self.rol_nombre
        if not r:
            raise ValueError("El campo rol es obligatorio.")
        self.rol = r.lower()
        self.rol_nombre = self.rol

        n = self.nivel or self.nivel_nombre
        if n:
            self.nivel = n.lower()
            self.nivel_nombre = self.nivel
        return self


class UserUpdateRequest(BaseModel):
    password: Optional[str] = Field(None, min_length=6)
    rol: Optional[str] = None
    nivel: Optional[str] = None
    estado: Optional[str] = Field(None, description="'ACTIVE', 'INACTIVE', 'BLOCKED'")
