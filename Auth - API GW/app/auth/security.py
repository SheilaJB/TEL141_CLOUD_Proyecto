from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt

from app.config import Settings
from app.shared.claims import Claims
from app.shared.schemas import UserInfo
from app.shared.jwt_tokens import AUDIENCE, ISSUER


class TokenIssuer:
    def __init__(self, private_key: str, settings: Settings) -> None:
        self._private_key = private_key
        self._settings = settings

    def issue(self, user: UserInfo) -> tuple[str, Claims]:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=self._settings.access_ttl_min)
        claims = Claims(
            sub=str(user.id),
            cod=user.codigo,
            rol=user.rol,
            nivel=user.nivel,
            iat=int(now.timestamp()),
            exp=int(expires_at.timestamp()),
            jti=str(uuid4()),
        )
        payload = claims.model_dump()
        payload["iss"] = ISSUER
        payload["aud"] = AUDIENCE
        token = jwt.encode(
            payload,
            self._private_key,
            algorithm="RS256",
            headers={"typ": "JWT"},
        )
        return token, claims
