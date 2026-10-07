from typing import Any

import jwt
from pydantic import ValidationError

from app.shared.claims import Claims

ISSUER = "tel141-auth"
AUDIENCE = "tel141-edge"


def decode_access_token(token: str, public_key: str) -> Claims:
    payload: dict[str, Any] = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=ISSUER,
        audience=AUDIENCE,
        options={
            "require": ["exp", "iat", "sub", "jti", "cod", "rol"],
        },
    )
    try:
        return Claims.model_validate(payload)
    except ValidationError as exc:
        raise jwt.InvalidTokenError("access token claims are invalid") from exc
