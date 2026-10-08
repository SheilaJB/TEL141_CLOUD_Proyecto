"""
security.py — Utilidades de Seguridad, Criptografía RS256, Refresh Tokens y Rate Limiting
"""
import os
import secrets
import hashlib
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List
from uuid import uuid4

import jwt
from fastapi import Header, HTTPException, status, Depends
from passlib.context import CryptContext

from app.schemas import UserVerifyResponse

ISSUER = "tel141-auth"
AUDIENCE = "tel141-edge"
ALGORITHM = "RS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TTL_MIN", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TTL_DAYS", "7"))

# Contexto de contraseñas con Argon2id
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
# Hash señuelo para evitar fugas de información por tiempo de respuesta (timing attacks)
DUMMY_HASH = pwd_context.hash("dummy_protection_password_secret_2026")

# Rate limiting de login en memoria: { "ip:codigo": deque([timestamp, ...]) }
_failed_logins: dict[str, deque[float]] = defaultdict(deque)
MAX_LOGIN_ATTEMPTS = int(os.getenv("LOGIN_ATTEMPTS_PER_MINUTE", "5"))


def _load_key(path_env: str, fallback_relative: str) -> str:
    path_str = os.getenv(path_env)
    search_paths = []
    if path_str:
        search_paths.append(Path(path_str))
    search_paths.append(Path(fallback_relative))
    search_paths.append(Path("/run/edge-keys") / Path(fallback_relative).name)
    search_paths.append(Path("keys") / Path(fallback_relative).name)
    search_paths.append(Path("../keys") / Path(fallback_relative).name)
    search_paths.append(Path("../Auth - API GW/keys") / Path(fallback_relative).name)
    search_paths.append(Path("C:/Users/Luis/Documents/Proy CLOUD Auth - API GW/keys") / Path(fallback_relative).name)

    for p in search_paths:
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8")
    
    # Si no se encuentra archivo en disco, advertir o retornar vacío
    return ""


_private_key_cache: Optional[str] = None
_public_key_cache: Optional[str] = None


def get_private_key() -> str:
    global _private_key_cache
    if not _private_key_cache:
        _private_key_cache = _load_key("JWT_PRIVATE_KEY_PATH", "keys/jwt-private.pem")
    return _private_key_cache


def get_public_key() -> str:
    global _public_key_cache
    if not _public_key_cache:
        _public_key_cache = _load_key("JWT_PUBLIC_KEY_PATH", "keys/jwt-public.pem")
    return _public_key_cache


# -------------------------------------------------------------
# Rate Limiting
# -------------------------------------------------------------
def check_rate_limit(key: str) -> None:
    now = time.time()
    queue = _failed_logins[key]
    while queue and queue[0] <= now - 60.0:
        queue.popleft()
    if len(queue) >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Intente de nuevo en un minuto."
        )


def record_failure(key: str) -> None:
    _failed_logins[key].append(time.time())


def clear_failures(key: str) -> None:
    _failed_logins.pop(key, None)


# -------------------------------------------------------------
# Passwords
# -------------------------------------------------------------
def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


# -------------------------------------------------------------
# Refresh Tokens
# -------------------------------------------------------------
def generate_opaque_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# -------------------------------------------------------------
# Access Tokens (RS256)
# -------------------------------------------------------------
def create_access_token(user_id: int, codigo: str, rol: str, nivel: Optional[str] = None) -> str:
    private_key = get_private_key()
    if not private_key:
        raise RuntimeError("No se encontró la clave privada RS256 para firmar tokens.")

    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(user_id),
        "cod": codigo,
        "rol": rol,
        "nivel": nivel,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": str(uuid4())
    }
    return jwt.encode(payload, private_key, algorithm=ALGORITHM, headers={"typ": "JWT"})


def decode_jwt_token(token: str) -> dict:
    key = get_public_key() or get_private_key()
    if not key:
        raise RuntimeError("No se encontró clave criptográfica para validar tokens.")
    
    try:
        return jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={"require": ["exp", "iat", "sub", "cod", "rol"]}
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="El token ha expirado.")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Token inválido: {str(e)}")


async def get_current_user(authorization: Optional[str] = Header(None)) -> UserVerifyResponse:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Falta encabezado Authorization.")
    
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Formato Bearer inválido.")
    
    payload = decode_jwt_token(parts[1])
    return UserVerifyResponse(
        id=int(payload["sub"]),
        codigo=payload["cod"],
        rol=payload["rol"],
        nivel=payload.get("nivel"),
        exp=payload["exp"]
    )


def require_roles(allowed_roles: List[str]):
    async def role_checker(current_user: UserVerifyResponse = Depends(get_current_user)):
        if current_user.rol not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado. Requiere uno de los roles: {allowed_roles}"
            )
        return current_user
    return role_checker
