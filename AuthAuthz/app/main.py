"""
main.py — Microservicio Auth & AuthZ 

Este servicio gestiona la autenticación de usuarios, la emisión de Tokens JWT
y el control de acceso basado en roles (RBAC).

Roles Soportados:
  - Usuario (Nivel Básico / Avanzado)
  - Operador de Plataforma
  - Administrador de Infraestructura
"""

from fastapi import FastAPI, Depends, HTTPException, status
from typing import List
import asyncpg

from app.database import get_db
from app.schemas import (
    LoginRequest,
    TokenResponse,
    UserVerifyResponse,
    UserCreate,
    UserResponse,
    UserStatusUpdate,
    UserLevelUpdate,
)
from app.security import (
    create_jwt_token,
    get_current_user,
    require_roles,
)

app = FastAPI(
    title="Servicio de Autenticación y Autorización (Auth/AuthZ)",
    description="Microservicio para gestión de identidades, tokens JWT y roles de usuario.",
    version="1.0.0",
)


# 1. AUTENTICACIÓN (LOGIN & VERIFICACIÓN)

@app.post("/auth/login", response_model=TokenResponse, tags=["Autenticación"])
@app.post("/login", response_model=TokenResponse, tags=["Autenticación"], include_in_schema=False)
async def login(req: LoginRequest, conn: asyncpg.Connection = Depends(get_db)):
    """
    Verifica credenciales en PostgreSQL utilizando la extensión 'pgcrypto'.
    
    Proceso:
      1. Busca al usuario por su código y verifica el hash Bcrypt nativamente en la BD.
      2. Valida que la cuenta esté en estado 'Activo'.
      3. Genera y firma un Token JWT que incluye id, código, rol y nivel.
    """
    query = """
        SELECT u.id, u.codigo, r.nombre as rol, n.nombre as nivel 
        FROM auth.usuario u
        JOIN auth.rol r ON u.rol_id = r.id
        LEFT JOIN auth.nivel n ON u.nivel_id = n.id
        WHERE u.codigo = $1 
          AND u.hash_password = crypt($2, u.hash_password)
          AND u.estado = 'Activo';
    """
    user = await conn.fetchrow(query, req.username, req.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas o usuario inactivo.",
        )

    # Payload para el JWT
    payload = {
        "id": user["id"],
        "codigo": user["codigo"],
        "rol": user["rol"],
        "nivel": user["nivel"],
    }

    # Generación de token JWT
    access_token = create_jwt_token(payload)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        rol=user["rol"],
        nivel=user["nivel"],
    )


@app.get("/auth/verify", response_model=UserVerifyResponse, tags=["Autenticación"])
@app.get("/verify", response_model=UserVerifyResponse, tags=["Autenticación"], include_in_schema=False)
async def verify_token(current_user: UserVerifyResponse = Depends(get_current_user)):
    """
    Valida la validez de un token JWT recibido en el encabezado Authorization.
    Devuelve los datos de identidad y permisos del usuario.
    """
    return current_user


# 2. GESTIÓN DE USUARIOS (SOLO ADMINISTRADOR)

@app.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Administración de Usuarios"],
)
async def register_user(
    user_data: UserCreate,
    conn: asyncpg.Connection = Depends(get_db),
    admin: UserVerifyResponse = Depends(require_roles(["Administrador"])),
):
    """
    Registra un nuevo usuario en el sistema.
    
    Restricción: Solo puede ser ejecutado por un 'Administrador'.
    Seguridad: Hashea la contraseña con pgcrypto gen_salt('bf') directo en la BD.
    """
    # 1. Validar que el rol exista
    rol_row = await conn.fetchrow("SELECT id FROM auth.rol WHERE nombre = $1", user_data.rol_nombre)
    if not rol_row:
        raise HTTPException(status_code=400, detail=f"El rol '{user_data.rol_nombre}' no existe.")

    rol_id = rol_row["id"]

    # 2. Validar nivel si el rol es 'Usuario'
    nivel_id = None
    if user_data.rol_nombre == "Usuario":
        if not user_data.nivel_nombre:
            raise HTTPException(status_code=400, detail="El campo 'nivel_nombre' es obligatorio para el rol 'Usuario'.")
        
        nivel_row = await conn.fetchrow("SELECT id FROM auth.nivel WHERE nombre = $1", user_data.nivel_nombre)
        if not nivel_row:
            raise HTTPException(status_code=400, detail=f"El nivel '{user_data.nivel_nombre}' no existe.")
        nivel_id = nivel_row["id"]

    # 3. Comprobar que el usuario no exista
    existing = await conn.fetchrow("SELECT id FROM auth.usuario WHERE codigo = $1", user_data.codigo)
    if existing:
        raise HTTPException(status_code=409, detail=f"El usuario con código '{user_data.codigo}' ya existe.")

    # 4. Insertar nuevo usuario hasheando la clave con Bcrypt nativo (pgcrypto)
    insert_query = """
        INSERT INTO auth.usuario (codigo, hash_password, rol_id, nivel_id, estado)
        VALUES ($1, crypt($2, gen_salt('bf')), $3, $4, 'Activo')
        RETURNING id, codigo, estado, fecha_creacion;
    """
    new_user = await conn.fetchrow(insert_query, user_data.codigo, user_data.password, rol_id, nivel_id)

    return UserResponse(
        id=new_user["id"],
        codigo=new_user["codigo"],
        rol=user_data.rol_nombre,
        nivel=user_data.nivel_nombre if user_data.rol_nombre == "Usuario" else None,
        estado=new_user["estado"],
        fecha_creacion=new_user["fecha_creacion"],
    )


@app.get("/auth/users", response_model=List[UserResponse], tags=["Administración de Usuarios"])
async def list_users(
    conn: asyncpg.Connection = Depends(get_db),
    current_user: UserVerifyResponse = Depends(require_roles(["Administrador", "Operador"])),
):
    """
    Obtiene la lista completa de usuarios registrados.
    
    Restricción: Accesible por 'Administrador' y 'Operador'.
    """
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


@app.put("/auth/users/{user_id}/status", response_model=UserResponse, tags=["Administración de Usuarios"])
async def update_user_status(
    user_id: int,
    status_data: UserStatusUpdate,
    conn: asyncpg.Connection = Depends(get_db),
    admin: UserVerifyResponse = Depends(require_roles(["Administrador"])),
):
    """
    Cambia el estado de un usuario (Activo / Inactivo).
    
    Restricción: Solo ejecutable por un 'Administrador'.
    """
    if status_data.estado not in ["Activo", "Inactivo"]:
        raise HTTPException(status_code=400, detail="El estado debe ser 'Activo' o 'Inactivo'.")

    query = """
        UPDATE auth.usuario
        SET estado = $1
        WHERE id = $2
        RETURNING id, codigo, rol_id, nivel_id, estado, fecha_creacion;
    """
    updated = await conn.fetchrow(query, status_data.estado, user_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    # Consultar nombres de rol y nivel
    rol_name = await conn.fetchval("SELECT nombre FROM auth.rol WHERE id = $1", updated["rol_id"])
    nivel_name = await conn.fetchval("SELECT nombre FROM auth.nivel WHERE id = $1", updated["nivel_id"]) if updated["nivel_id"] else None

    return UserResponse(
        id=updated["id"],
        codigo=updated["codigo"],
        rol=rol_name,
        nivel=nivel_name,
        estado=updated["estado"],
        fecha_creacion=updated["fecha_creacion"],
    )


@app.put("/auth/users/{user_id}/level", response_model=UserResponse, tags=["Administración de Usuarios"])
async def update_user_level(
    user_id: int,
    level_data: UserLevelUpdate,
    conn: asyncpg.Connection = Depends(get_db),
    admin: UserVerifyResponse = Depends(require_roles(["Administrador"])),
):
    """
    Actualiza el nivel de cuota de un usuario ('Básico' o 'Avanzado').
    
    Restricción: Solo ejecutable por un 'Administrador'.
    """
    nivel_row = await conn.fetchrow("SELECT id FROM auth.nivel WHERE nombre = $1", level_data.nivel_nombre)
    if not nivel_row:
        raise HTTPException(status_code=400, detail=f"El nivel '{level_data.nivel_nombre}' no existe.")

    query = """
        UPDATE auth.usuario
        SET nivel_id = $1
        WHERE id = $2 AND rol_id = (SELECT id FROM auth.rol WHERE nombre = 'Usuario')
        RETURNING id, codigo, rol_id, nivel_id, estado, fecha_creacion;
    """
    updated = await conn.fetchrow(query, nivel_row["id"], user_id)
    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado o no posee el rol 'Usuario' para cambiar de nivel."
        )

    rol_name = await conn.fetchval("SELECT nombre FROM auth.rol WHERE id = $1", updated["rol_id"])

    return UserResponse(
        id=updated["id"],
        codigo=updated["codigo"],
        rol=rol_name,
        nivel=level_data.nivel_nombre,
        estado=updated["estado"],
        fecha_creacion=updated["fecha_creacion"],
    )
