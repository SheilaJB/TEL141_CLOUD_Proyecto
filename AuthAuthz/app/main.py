"""
main.py — Módulo AuthAuthz 
"""
from fastapi import FastAPI, HTTPException, Depends, status
from typing import List
import asyncpg
from passlib.context import CryptContext

from app.schemas import (
    LoginRequest, TokenResponse, UserResponse, UserCreate,
    UserStatusUpdate, UserLevelUpdate, UserVerifyResponse, RefreshTokenRequest
)
from app.database import get_db
from app.security import create_access_token, create_refresh_token, get_current_user, require_roles

app = FastAPI(
    title="AuthAuthz Microservice - Grupo 3",
    description="Microservicio de autenticación compatible con la DB Unificada (Luis)"
)

# Contexto de encriptación para soportar Argon2id 
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# 1. FLUJO DE AUTENTICACIÓN
@app.post("/auth/login", response_model=TokenResponse, tags=["Autenticación"])
@app.post("/login", response_model=TokenResponse, tags=["Autenticación"], include_in_schema=False)
async def login(req: LoginRequest, conn: asyncpg.Connection = Depends(get_db)):
    query = """
        SELECT u.id, u.codigo, u.hash_password, r.nombre as rol, n.nombre as nivel, u.estado 
        FROM auth.usuario u
        JOIN auth.rol r ON u.rol_id = r.id
        LEFT JOIN auth.nivel n ON u.nivel_id = n.id
        WHERE u.codigo = $1
    """
    user = await conn.fetchrow(query, req.username)
    
    if not user or not pwd_context.verify(req.password, user["hash_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Código de usuario o contraseña incorrectos."
        )
    
    if user["estado"] != 'ACTIVE':
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo o bloqueado.")

    # Generar ambos Tokens
    access_token = create_access_token({
        "id": user["id"],
        "codigo": user["codigo"],
        "rol": user["rol"],
        "nivel": user["nivel"]
    })
    refresh_token = create_refresh_token(user["id"])

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        rol=user["rol"],
        nivel=user["nivel"],
    )

@app.post("/auth/refresh", response_model=TokenResponse, tags=["Autenticación"])
@app.post("/refresh", response_model=TokenResponse, tags=["Autenticación"], include_in_schema=False)
async def refresh_access_token(req: RefreshTokenRequest, conn: asyncpg.Connection = Depends(get_db)):
    from app.security import decode_jwt_token
    payload = decode_jwt_token(req.refresh_token)
    
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Token provisto no es un Refresh Token.")
    
    user_id = int(payload.get("sub"))
    
    # Validar que el usuario sigue existiendo y está activo
    query = """
        SELECT u.id, u.codigo, r.nombre as rol, n.nombre as nivel, u.estado 
        FROM auth.usuario u
        JOIN auth.rol r ON u.rol_id = r.id
        LEFT JOIN auth.nivel n ON u.nivel_id = n.id
        WHERE u.id = $1
    """
    user = await conn.fetchrow(query, user_id)
    if not user or user["estado"] != 'ACTIVE':
        raise HTTPException(status_code=403, detail="Usuario inactivo o eliminado.")

    # Emitir nuevos tokens
    new_access = create_access_token({
        "id": user["id"],
        "codigo": user["codigo"],
        "rol": user["rol"],
        "nivel": user["nivel"]
    })
    new_refresh = create_refresh_token(user["id"])

    return TokenResponse(
        access_token=new_access,
        refresh_token=new_refresh,
        token_type="bearer",
        rol=user["rol"],
        nivel=user["nivel"],
    )

@app.get("/auth/verify", response_model=UserVerifyResponse, tags=["Autenticación"])
@app.get("/verify", response_model=UserVerifyResponse, tags=["Autenticación"], include_in_schema=False)
async def verify_token(current_user: UserVerifyResponse = Depends(get_current_user)):
    return current_user

# 2. GESTIÓN DE USUARIOS
@app.post("/auth/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED, tags=["Administración de Usuarios"])
async def register_user(
    user_data: UserCreate,
    conn: asyncpg.Connection = Depends(get_db),
    admin: UserVerifyResponse = Depends(require_roles(["admin"])),
):
    # Validar rol ('consumidor', 'operador', 'admin')
    rol_row = await conn.fetchrow("SELECT id FROM auth.rol WHERE nombre = $1", user_data.rol_nombre.lower())
    if not rol_row:
        raise HTTPException(status_code=400, detail=f"El rol '{user_data.rol_nombre}' no existe.")

    # Validar nivel ('basico', 'avanzado') si es consumidor
    nivel_id = None
    if user_data.rol_nombre.lower() == "consumidor":
        if not user_data.nivel_nombre:
            raise HTTPException(status_code=400, detail="El nivel es obligatorio para un 'consumidor'.")
        
        nivel_row = await conn.fetchrow("SELECT id FROM auth.nivel WHERE nombre = $1", user_data.nivel_nombre.lower())
        if not nivel_row:
            raise HTTPException(status_code=400, detail=f"El nivel '{user_data.nivel_nombre}' no existe.")
        nivel_id = nivel_row["id"]

    existing = await conn.fetchrow("SELECT id FROM auth.usuario WHERE codigo = $1", user_data.codigo)
    if existing:
        raise HTTPException(status_code=409, detail=f"El usuario '{user_data.codigo}' ya existe.")

    # Hashear contraseña con Argon2
    hashed_password = pwd_context.hash(user_data.password)

    insert_query = """
        INSERT INTO auth.usuario (codigo, hash_password, rol_id, nivel_id, estado, creado_por)
        VALUES ($1, $2, $3, $4, 'ACTIVE', $5)
        RETURNING id, codigo, estado, fecha_creacion;
    """
    new_user = await conn.fetchrow(insert_query, user_data.codigo, hashed_password, rol_row["id"], nivel_id, admin.id)

    return UserResponse(
        id=new_user["id"],
        codigo=new_user["codigo"],
        rol=user_data.rol_nombre,
        nivel=user_data.nivel_nombre if user_data.rol_nombre.lower() == "consumidor" else None,
        estado=new_user["estado"],
        fecha_creacion=new_user["fecha_creacion"],
    )

@app.get("/auth/users", response_model=List[UserResponse], tags=["Administración de Usuarios"])
async def list_users(
    conn: asyncpg.Connection = Depends(get_db),
    current_user: UserVerifyResponse = Depends(require_roles(["admin", "operador"])),
):
    query = """
        SELECT u.id, u.codigo, r.nombre as rol, n.nombre as nivel, u.estado, u.fecha_creacion
        FROM auth.usuario u
        JOIN auth.rol r ON u.rol_id = r.id
        LEFT JOIN auth.nivel n ON u.nivel_id = n.id
        ORDER BY u.id ASC;
    """
    rows = await conn.fetch(query)

    return [
        UserResponse(
            id=r["id"],
            codigo=r["codigo"],
            rol=r["rol"],
            nivel=r["nivel"],
            estado=r["estado"],
            fecha_creacion=r["fecha_creacion"],
        )
        for r in rows
    ]
