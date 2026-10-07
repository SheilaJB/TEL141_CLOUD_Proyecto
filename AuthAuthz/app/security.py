"""
security.py — Utilidades de Seguridad, JWT y Autorización por Roles
Implementa lógica de Access Tokens (Cortos) y Refresh Tokens (Largos).
"""

import os
import jwt
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import Header, HTTPException, status, Depends
from app.schemas import UserVerifyResponse

SECRET_KEY = os.getenv("JWT_SECRET", "super_secreto_pucp_2026")
ALGORITHM = "HS256"

# Nuevos tiempos de vida 
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


def create_access_token(data: dict) -> str:
    """Genera Token de Acceso (30 min)"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    """Genera Token de Refresco (7 días) - Solo almacena el ID del usuario"""
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {"sub": str(user_id), "exp": expire, "type": "refresh"}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_jwt_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="El token ha expirado.")
    except (jwt.InvalidTokenError, Exception):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o corrupto.")


async def get_current_user(authorization: Optional[str] = Header(None)) -> UserVerifyResponse:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Falta encabezado Authorization.")
    
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Formato Bearer inválido.")
    
    payload = decode_jwt_token(parts[1])
    
    # Prevenir que usen un Refresh Token para entrar a rutas protegidas
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No se puede usar un Refresh Token para esta ruta.")
    
    return UserVerifyResponse(
        id=payload["id"],
        codigo=payload["codigo"],
        rol=payload["rol"],
        nivel=payload.get("nivel"),
        exp=payload["exp"]
    )


def require_roles(allowed_roles: List[str]):
    async def role_checker(current_user: UserVerifyResponse = Depends(get_current_user)):
        if current_user.rol not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado. Requiere: {allowed_roles}"
            )
        return current_user
    return role_checker
