import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.schemas import LoginRequest, UserCreateRequest, UserUpdateRequest
from app.auth.security import TokenIssuer
from app.config import Settings
from app.shared.denylist import Denylist
from app.shared.schemas import UserInfo


class IssuedTokens:
    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        user: UserInfo,
        expires_in: int,
    ) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.user = user
        self.expires_in = expires_in


class AuthService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        private_key: str,
        settings: Settings,
        denylist: Denylist,
    ) -> None:
        self._sessions = session_factory
        self._issuer = TokenIssuer(private_key, settings)
        self._settings = settings
        self._denylist = denylist
        self._password_hasher = PasswordHasher()
        self._dummy_hash = self._password_hasher.hash(secrets.token_urlsafe(32))
        self._failed_logins: dict[str, deque[float]] = defaultdict(deque)

    async def login(self, body: LoginRequest, client_ip: str) -> IssuedTokens:
        key = f"{client_ip}:{body.codigo}"
        self._check_rate_limit(key)

        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT u.id, u.codigo, u.hash_password, u.estado,
                               r.nombre AS rol, n.nombre AS nivel
                        FROM auth.usuario AS u
                        JOIN auth.rol AS r ON r.id = u.rol_id
                        LEFT JOIN auth.nivel AS n ON n.id = u.nivel_id
                        WHERE u.codigo = :codigo
                        """
                    ),
                    {"codigo": body.codigo},
                )
            ).mappings().first()

        hash_to_check = row["hash_password"] if row is not None else self._dummy_hash
        password_valid = self._verify_password(body.password, hash_to_check)
        if row is None or row["estado"] != "ACTIVE" or not password_valid:
            self._record_failure(key)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
            )

        self._failed_logins.pop(key, None)
        user = self._to_user(row)
        return await self._new_session(user)

    async def rotate_refresh(self, raw_token: str) -> IssuedTokens:
        token_hash = self._hash_refresh_token(raw_token)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        revoked_at = now
        result: IssuedTokens | None = None
        failure_status: int | None = None
        user_id: int | None = None

        async with self._sessions() as session:
            async with session.begin():
                token_row = (
                    await session.execute(
                        text(
                            """
                            SELECT id, usuario_id, familia_id, expira_en, revocado_en
                            FROM auth.refresh_token
                            WHERE token_hash = :token_hash
                            FOR UPDATE
                            """
                        ),
                        {"token_hash": token_hash},
                    )
                ).mappings().first()
                if token_row is None:
                    failure_status = status.HTTP_401_UNAUTHORIZED
                elif token_row["revocado_en"] is not None:
                    await session.execute(
                        text(
                            """
                            UPDATE auth.refresh_token
                            SET revocado_en = COALESCE(revocado_en, :revoked_at)
                            WHERE familia_id = :family_id
                            """
                        ),
                        {
                            "revoked_at": revoked_at,
                            "family_id": token_row["familia_id"],
                        },
                    )
                    failure_status = status.HTTP_401_UNAUTHORIZED
                elif token_row["expira_en"] <= now:
                    failure_status = status.HTTP_401_UNAUTHORIZED
                else:
                    user_row = (
                        await session.execute(
                            text(
                                """
                                SELECT u.id, u.codigo, u.hash_password, u.estado,
                                       r.nombre AS rol, n.nombre AS nivel
                                FROM auth.usuario AS u
                                JOIN auth.rol AS r ON r.id = u.rol_id
                                LEFT JOIN auth.nivel AS n ON n.id = u.nivel_id
                                WHERE u.id = :user_id
                                FOR UPDATE OF u
                                """
                            ),
                            {"user_id": token_row["usuario_id"]},
                        )
                    ).mappings().first()
                    if user_row is None or user_row["estado"] != "ACTIVE":
                        await session.execute(
                            text(
                                """
                                UPDATE auth.refresh_token
                                SET revocado_en = COALESCE(revocado_en, :revoked_at)
                                WHERE familia_id = :family_id
                                """
                            ),
                            {
                                "revoked_at": revoked_at,
                                "family_id": token_row["familia_id"],
                            },
                        )
                        user_id = token_row["usuario_id"]
                        failure_status = status.HTTP_401_UNAUTHORIZED
                    else:
                        user = self._to_user(user_row)
                        result = self._prepare_tokens(user)
                        refresh_id = uuid4()
                        refresh_expires = now + timedelta(
                            days=self._settings.refresh_ttl_days
                        )
                        await session.execute(
                            text(
                                """
                                UPDATE auth.refresh_token
                                SET revocado_en = :revoked_at,
                                    reemplazado_por = :replacement_id
                                WHERE id = :token_id
                                """
                            ),
                            {
                                "revoked_at": revoked_at,
                                "replacement_id": refresh_id,
                                "token_id": token_row["id"],
                            },
                        )
                        await session.execute(
                            text(
                                """
                                INSERT INTO auth.refresh_token
                                    (id, usuario_id, familia_id, token_hash, expira_en)
                                VALUES
                                    (:id, :user_id, :family_id, :token_hash, :expires_at)
                                """
                            ),
                            {
                                "id": refresh_id,
                                "user_id": user.id,
                                "family_id": token_row["familia_id"],
                                "token_hash": self._hash_refresh_token(
                                    result.refresh_token
                                ),
                                "expires_at": refresh_expires,
                            },
                        )

        if user_id is not None:
            self._denylist.revoke_user(user_id)
        if failure_status is not None or result is None:
            raise HTTPException(
                status_code=failure_status or status.HTTP_401_UNAUTHORIZED,
                detail="invalid refresh token",
            )
        return result

    async def logout(self, raw_token: str) -> None:
        token_hash = self._hash_refresh_token(raw_token)
        async with self._sessions() as session:
            result = await session.execute(
                text(
                    """
                    UPDATE auth.refresh_token
                    SET revocado_en = COALESCE(revocado_en, :revoked_at)
                    WHERE token_hash = :token_hash
                    """
                ),
                {
                    "revoked_at": datetime.now(timezone.utc).replace(tzinfo=None),
                    "token_hash": token_hash,
                },
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=401, detail="invalid refresh token")
            await session.commit()

    async def get_user(self, user_id: int) -> UserInfo:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT u.id, u.codigo, u.estado,
                               r.nombre AS rol, n.nombre AS nivel
                        FROM auth.usuario AS u
                        JOIN auth.rol AS r ON r.id = u.rol_id
                        LEFT JOIN auth.nivel AS n ON n.id = u.nivel_id
                        WHERE u.id = :user_id
                        """
                    ),
                    {"user_id": user_id},
                )
            ).mappings().first()
        if row is None or row["estado"] != "ACTIVE":
            raise HTTPException(status_code=401, detail="user is not active")
        return self._to_user(row)

    async def create_user(
        self,
        actor: UserInfo,
        body: UserCreateRequest,
    ) -> UserInfo:
        self._check_role_assignment(actor.rol, body.rol)
        try:
            async with self._sessions() as session:
                async with session.begin():
                    role_id, level_id = await self._role_and_level_ids(
                        session, body.rol, body.nivel
                    )
                    result = await session.execute(
                        text(
                            """
                            INSERT INTO auth.usuario
                                (codigo, hash_password, rol_id, nivel_id, creado_por)
                            VALUES
                                (:codigo, :password_hash, :role_id, :level_id, :created_by)
                            RETURNING id, codigo
                            """
                        ),
                        {
                            "codigo": body.codigo,
                            "password_hash": self._password_hasher.hash(body.password),
                            "role_id": role_id,
                            "level_id": level_id,
                            "created_by": actor.id,
                        },
                    )
                    created = result.mappings().one()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail="user could not be created because a database constraint failed",
            ) from exc
        return UserInfo(
            id=created["id"],
            codigo=created["codigo"],
            rol=body.rol,
            nivel=body.nivel,
        )

    async def update_user(
        self,
        actor: UserInfo,
        user_id: int,
        body: UserUpdateRequest,
    ) -> UserInfo:
        password_hash = (
            self._password_hasher.hash(body.password)
            if "password" in body.model_fields_set and body.password is not None
            else None
        )
        should_revoke = False
        updated: UserInfo | None = None
        async with self._sessions() as session:
            async with session.begin():
                user_row = (
                    await session.execute(
                        text(
                            """
                            SELECT u.id, u.codigo, u.estado, r.nombre AS rol,
                                   n.nombre AS nivel
                            FROM auth.usuario AS u
                            JOIN auth.rol AS r ON r.id = u.rol_id
                            LEFT JOIN auth.nivel AS n ON n.id = u.nivel_id
                            WHERE u.id = :user_id
                            FOR UPDATE OF u
                            """
                        ),
                        {"user_id": user_id},
                    )
                ).mappings().first()
                if user_row is None:
                    raise HTTPException(status_code=404, detail="user not found")
                current = self._to_user(user_row)
                if actor.rol == "operador" and current.rol == "admin":
                    raise HTTPException(
                        status_code=403,
                        detail="operators cannot modify administrators",
                    )

                desired_role = (
                    body.rol
                    if "rol" in body.model_fields_set and body.rol is not None
                    else current.rol
                )
                desired_level = (
                    body.nivel
                    if "nivel" in body.model_fields_set
                    else current.nivel
                )
                desired_state = (
                    body.estado
                    if "estado" in body.model_fields_set and body.estado is not None
                    else user_row["estado"]
                )
                if (desired_role == "consumidor") != (desired_level is not None):
                    raise HTTPException(
                        status_code=422,
                        detail="consumidor requires a nivel; other roles must not have one",
                    )
                self._check_role_assignment(actor.rol, desired_role)

                role_id, level_id = await self._role_and_level_ids(
                    session, desired_role, desired_level
                )
                await session.execute(
                    text(
                        """
                        UPDATE auth.usuario
                        SET rol_id = :role_id,
                            nivel_id = :level_id,
                            estado = :state,
                            hash_password = COALESCE(:password_hash, hash_password)
                        WHERE id = :user_id
                        """
                    ),
                    {
                        "role_id": role_id,
                        "level_id": level_id,
                        "state": desired_state,
                        "password_hash": password_hash,
                        "user_id": user_id,
                    },
                )
                should_revoke = (
                    desired_role != current.rol
                    or desired_level != current.nivel
                    or desired_state in {"INACTIVE", "BLOCKED"}
                    or password_hash is not None
                )
                if should_revoke:
                    await session.execute(
                        text(
                            """
                            UPDATE auth.refresh_token
                            SET revocado_en = COALESCE(revocado_en, :revoked_at)
                            WHERE usuario_id = :user_id
                            """
                        ),
                        {
                            "revoked_at": datetime.now(timezone.utc).replace(
                                tzinfo=None
                            ),
                            "user_id": user_id,
                        },
                    )
                updated = UserInfo(
                    id=user_id,
                    codigo=current.codigo,
                    rol=desired_role,
                    nivel=desired_level,
                )
        if should_revoke:
            self._denylist.revoke_user(user_id)
        if updated is None:
            raise RuntimeError("user update transaction did not produce a result")
        return updated

    async def _new_session(self, user: UserInfo) -> IssuedTokens:
        issued = self._prepare_tokens(user)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        refresh_id = uuid4()
        family_id = uuid4()
        async with self._sessions() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO auth.refresh_token
                        (id, usuario_id, familia_id, token_hash, expira_en)
                    VALUES
                        (:id, :user_id, :family_id, :token_hash, :expires_at)
                    """
                ),
                {
                    "id": refresh_id,
                    "user_id": user.id,
                    "family_id": family_id,
                    "token_hash": self._hash_refresh_token(issued.refresh_token),
                    "expires_at": now + timedelta(days=self._settings.refresh_ttl_days),
                },
            )
            await session.commit()
        return issued

    def _prepare_tokens(self, user: UserInfo) -> IssuedTokens:
        access_token, _ = self._issuer.issue(user)
        return IssuedTokens(
            access_token=access_token,
            refresh_token=secrets.token_urlsafe(48),
            user=user,
            expires_in=self._settings.access_ttl_min * 60,
        )

    async def _role_and_level_ids(
        self,
        session: AsyncSession,
        role: str,
        level: str | None,
    ) -> tuple[int, int | None]:
        role_id = (
            await session.execute(
                text("SELECT id FROM auth.rol WHERE nombre = :name"),
                {"name": role},
            )
        ).scalar_one_or_none()
        if role_id is None:
            raise HTTPException(status_code=422, detail="role is not configured")
        if level is None:
            return role_id, None
        level_id = (
            await session.execute(
                text("SELECT id FROM auth.nivel WHERE nombre = :name"),
                {"name": level},
            )
        ).scalar_one_or_none()
        if level_id is None:
            raise HTTPException(status_code=422, detail="service level is not configured")
        return role_id, level_id

    def _check_rate_limit(self, key: str) -> None:
        now = time.monotonic()
        attempts = self._failed_logins[key]
        while attempts and attempts[0] <= now - 60:
            attempts.popleft()
        if len(attempts) >= self._settings.login_attempts_per_minute:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many failed login attempts",
                headers={"Retry-After": "60"},
            )

    def _record_failure(self, key: str) -> None:
        self._failed_logins[key].append(time.monotonic())

    def _verify_password(self, password: str, password_hash: str) -> bool:
        try:
            return self._password_hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    @staticmethod
    def _hash_refresh_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_user(row: Any) -> UserInfo:
        return UserInfo(
            id=row["id"],
            codigo=row["codigo"],
            rol=row["rol"],
            nivel=row["nivel"],
        )

    @staticmethod
    def _check_role_assignment(actor_role: str, target_role: str) -> None:
        if actor_role == "operador" and target_role == "admin":
            raise HTTPException(
                status_code=403,
                detail="operators cannot assign the administrator role",
            )
