"""
schemas.py — Modelos de Datos Pydantic para el Módulo Auth/AuthZ

Este archivo define las estructuras de entrada y salida para la API REST.
Garantiza la validación estricta de tipos de datos en las peticiones.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# MODELOS DE AUTENTICACIÓN

class LoginRequest(BaseModel):
    """Esquema para la petición de inicio de sesión."""
    username: str = Field(..., description="Código de usuario (ej. 'alumno_basico' o 'admin_pucp')", example="alumno_basico")
    password: str = Field(..., description="Contraseña en texto plano", example="pucp2026")


class TokenResponse(BaseModel):
    """Esquema devuelto tras un inicio de sesión exitoso."""
    access_token: str = Field(..., description="Token JWT firmado")
    token_type: str = Field("bearer", description="Tipo de token (Bearer)")
    rol: str = Field(..., description="Rol del usuario: 'Usuario', 'Operador', 'Administrador'")
    nivel: Optional[str] = Field(None, description="Nivel del usuario: 'Básico', 'Avanzado' (solo para rol Usuario)")


class UserVerifyResponse(BaseModel):
    """Esquema de respuesta al verificar la validez de un Token JWT."""
    id: int
    codigo: str
    rol: str
    nivel: Optional[str] = None
    exp: int


# MODELOS DE GESTIÓN DE USUARIOS (ADMIN)

class UserCreate(BaseModel):
    """Esquema para que el Administrador registre nuevos usuarios."""
    codigo: str = Field(..., description="Código único de usuario/estudiante", example="20201234")
    password: str = Field(..., min_length=6, description="Contraseña inicial", example="pucp2026")
    rol_nombre: str = Field(..., description="Rol a asignar: 'Usuario', 'Operador', 'Administrador'", example="Usuario")
    nivel_nombre: Optional[str] = Field(None, description="Nivel a asignar: 'Básico', 'Avanzado' (obligatorio si rol es 'Usuario')", example="Básico")


class UserResponse(BaseModel):
    """Esquema para mostrar la información pública de un usuario."""
    id: int
    codigo: str
    rol: str
    nivel: Optional[str] = None
    estado: str
    fecha_creacion: datetime


class UserStatusUpdate(BaseModel):
    """Esquema para cambiar el estado de un usuario (Activo / Inactivo)."""
    estado: str = Field(..., description="Estado del usuario ('Activo' o 'Inactivo')", example="Inactivo")


class UserLevelUpdate(BaseModel):
    """Esquema para actualizar el nivel/cuota de un usuario (Básico / Avanzado)."""
    nivel_nombre: str = Field(..., description="Nuevo nivel ('Básico' o 'Avanzado')", example="Avanzado")
