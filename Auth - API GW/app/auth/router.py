from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.auth.dependencies import (
    get_auth_service,
    get_claims,
    get_current_user,
    require_roles,
)
from app.auth.schemas import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.auth.service import AuthService
from app.config import Settings
from app.shared.claims import Claims
from app.shared.schemas import UserInfo

router = APIRouter(prefix="/auth", tags=["auth"])


def _refresh_token(body_token: str | None, cookie_token: str | None) -> str:
    raw_token = body_token or cookie_token
    if not raw_token:
        raise HTTPException(status_code=401, detail="refresh token is required")
    return raw_token


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/auth",
        max_age=settings.refresh_ttl_days * 24 * 60 * 60,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> LoginResponse:
    client_ip = request.client.host if request.client is not None else "unknown"
    issued = await service.login(body, client_ip)
    settings: Settings = request.app.state.settings
    refresh_token = issued.refresh_token if body.client == "cli" else None
    if body.client == "web":
        _set_refresh_cookie(response, issued.refresh_token, settings)
    return LoginResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        refresh_token=refresh_token,
        user=issued.user,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    body: RefreshRequest | None = None,
) -> RefreshResponse:
    body_token = body.refresh_token if body is not None else None
    raw_token = _refresh_token(body_token, request.cookies.get("refresh_token"))
    issued = await service.rotate_refresh(raw_token)
    settings: Settings = request.app.state.settings
    if body_token is None:
        _set_refresh_cookie(response, issued.refresh_token, settings)
        returned_refresh_token = None
    else:
        returned_refresh_token = issued.refresh_token
    return RefreshResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        refresh_token=returned_refresh_token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    body: RefreshRequest | None = None,
) -> Response:
    body_token = body.refresh_token if body is not None else None
    raw_token = _refresh_token(body_token, request.cookies.get("refresh_token"))
    await service.logout(raw_token)
    response.delete_cookie(
        key="refresh_token",
        path="/auth",
        secure=request.app.state.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserInfo)
async def me(
    claims: Annotated[Claims, Depends(get_claims)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserInfo:
    return await service.get_user(int(claims.sub))


@router.post(
    "/usuarios",
    response_model=UserInfo,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    body: UserCreateRequest,
    actor: Annotated[UserInfo, Depends(require_roles({"admin"}))],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserInfo:
    return await service.create_user(actor, body)


@router.patch("/usuarios/{user_id}", response_model=UserInfo)
async def update_user(
    user_id: int,
    body: UserUpdateRequest,
    actor: Annotated[UserInfo, Depends(require_roles({"admin"}))],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserInfo:
    return await service.update_user(actor, user_id, body)
