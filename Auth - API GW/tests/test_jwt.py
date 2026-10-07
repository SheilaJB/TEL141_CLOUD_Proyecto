import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.security import TokenIssuer
from app.config import Settings
from app.shared.jwt_tokens import decode_access_token
from app.shared.schemas import UserInfo


@pytest.fixture
def signing_material(tmp_path):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    settings = Settings(
        database_url="postgresql+psycopg://user:pass@localhost/db",
        jwt_private_key_path=tmp_path / "jwt-private.pem",
        jwt_public_key_path=tmp_path / "jwt-public.pem",
        tls_cert_path=tmp_path / "edge.crt",
        tls_key_path=tmp_path / "edge.key",
        ca_cert_path=tmp_path / "ca.crt",
        internal_service_token="test-internal-token",
        upstream_cruds_url="http://localhost:8001",
        upstream_slice_manager_url="http://localhost:8000",
    )
    return private_bytes.decode("utf-8"), public_bytes.decode("utf-8"), settings


def test_rs256_token_round_trips_with_expected_claims(signing_material) -> None:
    private_key, public_key, settings = signing_material
    issuer = TokenIssuer(private_key, settings)
    token, _ = issuer.issue(
        UserInfo(id=8, codigo="operator", rol="operador", nivel=None)
    )

    claims = decode_access_token(token, public_key)

    assert claims.sub == "8"
    assert claims.cod == "operator"
    assert claims.rol == "operador"
    assert claims.nivel is None


def test_decoder_rejects_non_rs256_algorithm(signing_material) -> None:
    _, public_key, _ = signing_material
    token = jwt.encode(
        {
            "sub": "8",
            "cod": "operator",
            "rol": "operador",
            "nivel": None,
            "iat": 2_000_000_000,
            "exp": 2_000_000_060,
            "jti": "test",
            "iss": "tel141-auth",
            "aud": "tel141-edge",
        },
        "not-a-rsa-key",
        algorithm="HS256",
    )

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token, public_key)
