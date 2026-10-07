"""
schemas.py — Modelos de Datos Pydantic para el Módulo Auth/AuthZ
Adaptado para incluir Tokens de Refresco
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

# MODELOS DE AUTENTICACIÓN
class LoginRequest(BaseModel):
    username: str = Field(..., description="Código de usuario")
    password: str = Field(..., description="Contraseña en texto plano")

class TokenResponse(BaseModel):
    """Esquema devuelto tras un inicio de sesión exitoso o Refresco."""
    access_token: str = Field(..., description="Token JWT firmado (Acceso rápido, expira en 30 min)")
    refresh_token: str = Field(..., description="Token JWT de refresco (Larga duración, expira en 7 días)")
    token_type: str = Field("bearer", description="Tipo de token (Bearer)")
    rol: str = Field(..., description="Rol del usuario")
    nivel: Optional[str] = Field(None, description="Nivel del usuario")

class RefreshTokenRequest(BaseModel):
    """Petición para renovar el Access Token silenciosamente."""
    refresh_token: str = Field(..., description="El token de refresco del usuario")

class UserVerifyResponse(BaseModel):
    id: int
    codigo: str
    rol: str
    nivel: Optional[str] = None
    exp: int

# MODELOS DE GESTIÓN DE USUARIOS (ADMIN)
class UserCreate(BaseModel):
    codigo: str = Field(..., description="Código único de usuario/estudiante")
    password: str = Field(..., min_length=6, description="Contraseña inicial")
    rol_nombre: str = Field(..., description="Rol a asignar ('consumidor', 'operador', 'admin')")
    nivel_nombre: Optional[str] = Field(None, description="Nivel ('basico', 'avanzado')")

class UserResponse(BaseModel):
    id: int
    codigo: str
    rol: str
    nivel: Optional[str] = None
    estado: str
    fecha_creacion: datetime

class UserStatusUpdate(BaseModel):
    estado: str = Field(..., description="Estado del usuario ('ACTIVE', 'INACTIVE', 'BLOCKED')")

class UserLevelUpdate(BaseModel):
    nivel_nombre: str = Field(..., description="Nuevo nivel ('basico' o 'avanzado')")
