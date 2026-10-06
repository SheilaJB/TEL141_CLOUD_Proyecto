"""
security.py — Utilidades de Seguridad, JWT y Autorización por Roles

Este módulo centraliza la generación y validación de tokens JWT,
así como la verificación de permisos por rol (RBAC - Role-Based Access Control)
"""

import os
import jwt
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import Header, HTTPException, status, Depends
from app.schemas import UserVerifyResponse

# Configuración del secreto JWT y algoritmo
SECRET_KEY = os.getenv("JWT_SECRET", "super_secreto_pucp_2026")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 12


def create_jwt_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Genera un Token JWT firmado con la información del usuario (claims).
    Incluye id, código, rol, nivel y fecha de expiración.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_jwt_token(token: str) -> dict:
    """
    Decodifica y valida la firma y expiración de un Token JWT.
    Lanza una excepción HTTP 401 si el token no es válido o expiró.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El token JWT ha expirado. Por favor, inicie sesión nuevamente."
        )
    except (jwt.InvalidTokenError, Exception):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token JWT inválido o corrupto."
        )


async def get_current_user(authorization: Optional[str] = Header(None)) -> UserVerifyResponse:
    """
    Inyector de dependencia para FastAPI.
    Extrae el token del encabezado 'Authorization: Bearer <token>',
    lo valida y retorna el objeto con la identidad del usuario.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere el encabezado Authorization con un token Bearer."
        )
    
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Formato de autorización inválido. Debe ser: 'Bearer <token>'"
        )
    
    token = parts[1]
    payload = decode_jwt_token(token)
    
    return UserVerifyResponse(
        id=payload["id"],
        codigo=payload["codigo"],
        rol=payload["rol"],
        nivel=payload.get("nivel"),
        exp=payload["exp"]
    )


def require_roles(allowed_roles: List[str]):
    """
    Generador de dependencias para restringir endpoints según el Rol del usuario.
    Ejemplo de uso: Depends(require_roles(["Administrador"]))
    """
    async def role_checker(current_user: UserVerifyResponse = Depends(get_current_user)):
        if current_user.rol not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado. El rol '{current_user.rol}' no tiene permisos para esta acción. Requiere: {allowed_roles}"
            )
        return current_user
    
    return role_checker
