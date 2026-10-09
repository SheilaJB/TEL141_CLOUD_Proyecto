from typing import Optional
from fastapi import Header, HTTPException, status
from app.config import settings
from app.schemas import UserIdentity


async def get_user_identity(
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(None, alias="X-User-Role"),
    x_user_nivel: Optional[str] = Header(None, alias="X-User-Nivel"),
    x_internal_token: Optional[str] = Header(None, alias="X-Internal-Token"),
) -> UserIdentity:
    if settings.internal_service_token and x_internal_token != settings.internal_service_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Peticion no autorizada: token interno invalido o ausente.",
        )

    if not x_user_id or not x_user_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identidad de usuario ausente en las cabeceras.",
        )

    try:
        user_id = int(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Id invalido.",
        )

    return UserIdentity(
        user_id=user_id,
        role=x_user_role.strip().lower(),
        service_level=x_user_nivel.strip().lower() if x_user_nivel else None,
    )


def require_roles(*allowed_roles: str):
    async def role_checker(identity: UserIdentity) -> UserIdentity:
        if identity.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado para el rol '{identity.role}'.",
            )
        return identity

    return role_checker
