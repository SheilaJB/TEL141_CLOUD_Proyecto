"""
main.py — Microservicio AuthAuthz (Stateful con asyncpg y RS256)
Orquestador Cloud TEL141 - Grupo 3
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import uuid4

import asyncpg
from fastapi import FastAPI, HTTPException, Depends, status, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.database import get_db, init_db_pool, close_db_pool
from app.schemas import (
    LoginRequest, TokenResponse, RefreshTokenRequest, RefreshResponse,
    UserInfo, UserCreate, UserUpdateRequest, UserVerifyResponse
)
from app.security import (
    create_access_token, generate_opaque_refresh_token, hash_refresh_token,
    verify_password, hash_password, DUMMY_HASH, check_rate_limit, record_failure,
    clear_failures, get_current_user, require_roles, REFRESH_TOKEN_EXPIRE_DAYS,
    ACCESS_TOKEN_EXPIRE_MINUTES
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Inicialización del pool de conexiones PostgreSQL
    await init_db_pool()
    yield
    # Cierre ordenado de conexiones
    await close_db_pool()


app = FastAPI(
    title="AuthAuthz Microservice - Grupo 3",
    description="Microservicio de Autenticación y Autorización (RS256, Rotación de Tokens en BD, Argon2id)",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=False,  # En entorno interno o dev HTTP/HTTPS
        samesite="lax",
        path="/auth",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    )


# -----------------------------------------------------------------------------
# 1. AUTENTICACIÓN Y SESIÓN
# -----------------------------------------------------------------------------

@app.post("/auth/login", response_model=TokenResponse, tags=["Autenticación"])
@app.post("/login", response_model=TokenResponse, tags=["Autenticación"], include_in_schema=False)
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    conn: asyncpg.Connection = Depends(get_db)
):
    client_ip = request.client.host if request.client else "unknown"
    rate_limit_key = f"{client_ip}:{req.codigo}"
    check_rate_limit(rate_limit_key)

    query = """
        SELECT u.id, u.codigo, u.hash_password, r.nombre as rol, n.nombre as nivel, u.estado 
        FROM auth.usuario u
        JOIN auth.rol r ON u.rol_id = r.id
        LEFT JOIN auth.nivel n ON u.nivel_id = n.id
        WHERE u.codigo = $1
    """
    user = await conn.fetchrow(query, req.codigo)

    # Protección contra ataques de timing: si el usuario no existe, se verifica dummy hash
    hash_to_verify = user["hash_password"] if user else DUMMY_HASH
    is_valid = verify_password(req.password, hash_to_verify)

    if not user or not is_valid:
        record_failure(rate_limit_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Código de usuario o contraseña incorrectos."
        )

    if user["estado"] != "ACTIVE":
        record_failure(rate_limit_key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo o bloqueado."
        )

    clear_failures(rate_limit_key)

    # 1. Generar Access Token RS256
    access_token = create_access_token(
        user_id=user["id"],
        codigo=user["codigo"],
        rol=user["rol"],
        nivel=user["nivel"]
    )

    # 2. Generar Refresh Token Opaco y almacenar su hash SHA-256 en BD
    raw_refresh = generate_opaque_refresh_token()
    token_hash = hash_refresh_token(raw_refresh)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    token_id = uuid4()
    family_id = token_id  # Inicia una nueva cadena/familia de rotación

    insert_token_sql = """
        INSERT INTO auth.refresh_token (id, usuario_id, familia_id, token_hash, creado_en, expira_en)
        VALUES ($1, $2, $3, $4, $5, $6)
    """
    await conn.execute(insert_token_sql, token_id, user["id"], family_id, token_hash, now, expires_at)

    if req.client == "web":
        _set_refresh_cookie(response, raw_refresh)
        return_refresh = None
    else:
        return_refresh = raw_refresh

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_token=return_refresh,
        rol=user["rol"],
        nivel=user["nivel"]
    )


@app.post("/auth/refresh", response_model=RefreshResponse, tags=["Autenticación"])
@app.post("/refresh", response_model=RefreshResponse, tags=["Autenticación"], include_in_schema=False)
async def refresh_token(
    request: Request,
    response: Response,
    body: Optional[RefreshTokenRequest] = None,
    conn: asyncpg.Connection = Depends(get_db)
):
    body_token = body.refresh_token if body else None
    raw_token = body_token or request.cookies.get("refresh_token")

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere un token de refresco (en body o cookie)."
        )

    t_hash = hash_refresh_token(raw_token)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async with conn.transaction():
        # Bloqueo de fila para evitar condiciones de carrera en rotaciones simultáneas
        select_token_sql = """
            SELECT id, usuario_id, familia_id, expira_en, revocado_en
            FROM auth.refresh_token
            WHERE token_hash = $1
            FOR UPDATE
        """
        token_row = await conn.fetchrow(select_token_sql, t_hash)

        if not token_row:
            raise HTTPException(status_code=401, detail="Token de refresco inválido.")

        # Detección de reuso malicioso: si el token ya fue revocado, se anula toda la familia
        if token_row["revocado_en"] is not None:
            await conn.execute(
                """
                UPDATE auth.refresh_token
                SET revocado_en = COALESCE(revocado_en, $1)
                WHERE familia_id = $2
                """,
                now, token_row["familia_id"]
            )
            raise HTTPException(
                status_code=401,
                detail="Intento de reuso de token revocado detectado. Sesión cerrada por seguridad."
            )

        if token_row["expira_en"] <= now:
            raise HTTPException(status_code=401, detail="El token de refresco ha expirado.")

        # Verificar que el usuario continúe activo
        user_sql = """
            SELECT u.id, u.codigo, u.estado, r.nombre as rol, n.nombre as nivel
            FROM auth.usuario u
            JOIN auth.rol r ON u.rol_id = r.id
            LEFT JOIN auth.nivel n ON u.nivel_id = n.id
            WHERE u.id = $1
            FOR UPDATE
        """
        user = await conn.fetchrow(user_sql, token_row["usuario_id"])
        if not user or user["estado"] != "ACTIVE":
            await conn.execute(
                "UPDATE auth.refresh_token SET revocado_en = COALESCE(revocado_en, $1) WHERE familia_id = $2",
                now, token_row["familia_id"]
            )
            raise HTTPException(status_code=403, detail="Usuario inactivo o suspendido.")

        # Emitir nuevo refresh token y rotar
        new_raw_refresh = generate_opaque_refresh_token()
        new_token_hash = hash_refresh_token(new_raw_refresh)
        new_token_id = uuid4()
        new_expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

        await conn.execute(
            """
            INSERT INTO auth.refresh_token (id, usuario_id, familia_id, token_hash, creado_en, expira_en)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            new_token_id, user["id"], token_row["familia_id"], new_token_hash, now, new_expires_at
        )

        await conn.execute(
            """
            UPDATE auth.refresh_token
            SET revocado_en = $1, reemplazado_por = $2
            WHERE id = $3
            """,
            now, new_token_id, token_row["id"]
        )

        new_access = create_access_token(
            user_id=user["id"],
            codigo=user["codigo"],
            rol=user["rol"],
            nivel=user["nivel"]
        )

    if body_token is None:
        _set_refresh_cookie(response, new_raw_refresh)
        ret_refresh = None
    else:
        ret_refresh = new_raw_refresh

    return RefreshResponse(
        access_token=new_access,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_token=ret_refresh
    )


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["Autenticación"])
@app.post("/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["Autenticación"], include_in_schema=False)
async def logout(
    request: Request,
    response: Response,
    body: Optional[RefreshTokenRequest] = None,
    conn: asyncpg.Connection = Depends(get_db)
):
    body_token = body.refresh_token if body else None
    raw_token = body_token or request.cookies.get("refresh_token")

    if raw_token:
        t_hash = hash_refresh_token(raw_token)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        await conn.execute(
            "UPDATE auth.refresh_token SET revocado_en = COALESCE(revocado_en, $1) WHERE token_hash = $2",
            now, t_hash
        )

    response.delete_cookie(key="refresh_token", path="/auth")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@app.get("/auth/me", response_model=UserInfo, tags=["Autenticación"])
async def get_me(
    current_user: UserVerifyResponse = Depends(get_current_user),
    conn: asyncpg.Connection = Depends(get_db)
):
    query = """
        SELECT u.id, u.codigo, r.nombre as rol, n.nombre as nivel, u.estado, u.fecha_creacion
        FROM auth.usuario u
        JOIN auth.rol r ON u.rol_id = r.id
        LEFT JOIN auth.nivel n ON u.nivel_id = n.id
        WHERE u.id = $1
    """
    user = await conn.fetchrow(query, current_user.id)
    if not user or user["estado"] != "ACTIVE":
        raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo.")

    return UserInfo(
        id=user["id"],
        codigo=user["codigo"],
        rol=user["rol"],
        nivel=user["nivel"],
        estado=user["estado"],
        fecha_creacion=user["fecha_creacion"]
    )


@app.get("/auth/verify", response_model=UserVerifyResponse, tags=["Autenticación"])
@app.get("/verify", response_model=UserVerifyResponse, tags=["Autenticación"], include_in_schema=False)
async def verify_token(current_user: UserVerifyResponse = Depends(get_current_user)):
    return current_user


# -----------------------------------------------------------------------------
# 2. ADMINISTRACIÓN DE USUARIOS
# -----------------------------------------------------------------------------

@app.post("/auth/usuarios", response_model=UserInfo, status_code=status.HTTP_201_CREATED, tags=["Gestión de Usuarios"])
@app.post("/auth/register", response_model=UserInfo, status_code=status.HTTP_201_CREATED, tags=["Gestión de Usuarios"], include_in_schema=False)
async def create_user(
    user_data: UserCreate,
    admin: UserVerifyResponse = Depends(require_roles(["admin"])),
    conn: asyncpg.Connection = Depends(get_db)
):
    rol_row = await conn.fetchrow("SELECT id FROM auth.rol WHERE nombre = $1", user_data.rol.lower())
    if not rol_row:
        raise HTTPException(status_code=400, detail=f"El rol '{user_data.rol}' no existe.")

    nivel_id = None
    if user_data.rol.lower() == "consumidor":
        if not user_data.nivel:
            raise HTTPException(status_code=400, detail="El nivel es obligatorio para un 'consumidor'.")
        nivel_row = await conn.fetchrow("SELECT id FROM auth.nivel WHERE nombre = $1", user_data.nivel.lower())
        if not nivel_row:
            raise HTTPException(status_code=400, detail=f"El nivel '{user_data.nivel}' no existe.")
        nivel_id = nivel_row["id"]
    else:
        if user_data.nivel:
            raise HTTPException(status_code=422, detail="Roles 'operador' y 'admin' no deben tener nivel de servicio.")

    existing = await conn.fetchrow("SELECT id FROM auth.usuario WHERE codigo = $1", user_data.codigo)
    if existing:
        raise HTTPException(status_code=409, detail=f"El usuario '{user_data.codigo}' ya existe.")

    hashed_pw = hash_password(user_data.password)
    insert_sql = """
        INSERT INTO auth.usuario (codigo, hash_password, rol_id, nivel_id, estado, creado_por)
        VALUES ($1, $2, $3, $4, 'ACTIVE', $5)
        RETURNING id, codigo, estado, fecha_creacion;
    """
    new_user = await conn.fetchrow(insert_sql, user_data.codigo, hashed_pw, rol_row["id"], nivel_id, admin.id)

    return UserInfo(
        id=new_user["id"],
        codigo=new_user["codigo"],
        rol=user_data.rol,
        nivel=user_data.nivel if user_data.rol == "consumidor" else None,
        estado=new_user["estado"],
        fecha_creacion=new_user["fecha_creacion"]
    )


@app.patch("/auth/usuarios/{user_id}", response_model=UserInfo, tags=["Gestión de Usuarios"])
async def update_user(
    user_id: int,
    body: UserUpdateRequest,
    admin: UserVerifyResponse = Depends(require_roles(["admin"])),
    conn: asyncpg.Connection = Depends(get_db)
):
    target = await conn.fetchrow(
        """
        SELECT u.id, u.codigo, u.estado, r.nombre as rol, n.nombre as nivel, u.rol_id, u.nivel_id
        FROM auth.usuario u
        JOIN auth.rol r ON u.rol_id = r.id
        LEFT JOIN auth.nivel n ON u.nivel_id = n.id
        WHERE u.id = $1
        """,
        user_id
    )
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    new_rol = body.rol.lower() if body.rol else target["rol"]
    new_nivel = body.nivel.lower() if body.nivel else target["nivel"]
    new_estado = body.estado if body.estado else target["estado"]

    if (new_rol == "consumidor") != (new_nivel is not None):
        raise HTTPException(status_code=422, detail="Consumidor requiere nivel; otros roles no deben tenerlo.")

    rol_row = await conn.fetchrow("SELECT id FROM auth.rol WHERE nombre = $1", new_rol)
    if not rol_row:
        raise HTTPException(status_code=400, detail=f"Rol '{new_rol}' no existe.")

    nivel_id = None
    if new_rol == "consumidor":
        nivel_row = await conn.fetchrow("SELECT id FROM auth.nivel WHERE nombre = $1", new_nivel)
        if not nivel_row:
            raise HTTPException(status_code=400, detail=f"Nivel '{new_nivel}' no existe.")
        nivel_id = nivel_row["id"]

    new_password_hash = hash_password(body.password) if body.password else None

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE auth.usuario
            SET rol_id = $1,
                nivel_id = $2,
                estado = $3,
                hash_password = COALESCE($4, hash_password)
            WHERE id = $5
            """,
            rol_row["id"], nivel_id, new_estado, new_password_hash, user_id
        )

        # Si cambian credenciales, rol, nivel o se bloquea/desactiva: revocar todos los refresh tokens
        should_revoke = (
            new_rol != target["rol"]
            or new_nivel != target["nivel"]
            or new_estado in {"INACTIVE", "BLOCKED"}
            or new_password_hash is not None
        )
        if should_revoke:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            await conn.execute(
                "UPDATE auth.refresh_token SET revocado_en = COALESCE(revocado_en, $1) WHERE usuario_id = $2",
                now, user_id
            )

    updated = await conn.fetchrow(
        """
        SELECT u.id, u.codigo, r.nombre as rol, n.nombre as nivel, u.estado, u.fecha_creacion
        FROM auth.usuario u
        JOIN auth.rol r ON u.rol_id = r.id
        LEFT JOIN auth.nivel n ON u.nivel_id = n.id
        WHERE u.id = $1
        """,
        user_id
    )

    return UserInfo(
        id=updated["id"],
        codigo=updated["codigo"],
        rol=updated["rol"],
        nivel=updated["nivel"],
        estado=updated["estado"],
        fecha_creacion=updated["fecha_creacion"]
    )


@app.get("/auth/users", response_model=List[UserInfo], tags=["Gestión de Usuarios"])
async def list_users(
    conn: asyncpg.Connection = Depends(get_db),
    actor: UserVerifyResponse = Depends(require_roles(["admin", "operador"]))
):
    rows = await conn.fetch(
        """
        SELECT u.id, u.codigo, r.nombre as rol, n.nombre as nivel, u.estado, u.fecha_creacion
        FROM auth.usuario u
        JOIN auth.rol r ON u.rol_id = r.id
        LEFT JOIN auth.nivel n ON u.nivel_id = n.id
        ORDER BY u.id ASC
        """
    )
    return [
        UserInfo(
            id=r["id"],
            codigo=r["codigo"],
            rol=r["rol"],
            nivel=r["nivel"],
            estado=r["estado"],
            fecha_creacion=r["fecha_creacion"]
        )
        for r in rows
    ]


@app.get("/health", tags=["Monitoreo"])
@app.get("/healthz", tags=["Monitoreo"], include_in_schema=False)
async def health():
    return {"status": "ok", "service": "auth"}
