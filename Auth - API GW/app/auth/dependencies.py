from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.service import AuthService
from app.shared.claims import Claims
from app.shared.schemas import UserInfo


def get_auth_service(request: Request) -> AuthService:
    service: AuthService | None = getattr(request.app.state, "auth_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="authentication service is unavailable",
        )
    return service


def get_claims(request: Request) -> Claims:
    claims: Claims | None = getattr(request.state, "claims", None)
    if claims is None:
        raise HTTPException(status_code=401, detail="authentication is required")
    return claims


def get_current_user(claims: Annotated[Claims, Depends(get_claims)]) -> UserInfo:
    return UserInfo(
        id=int(claims.sub),
        codigo=claims.cod,
        rol=claims.rol,
        nivel=claims.nivel,
    )
