from fastapi.testclient import TestClient

from app.auth.security import TokenIssuer
from app.config import Settings
from app.gateway.proxy import _forward_headers
from app.gateway.routes import policy_for, upstream_for
from app.main import create_app
from app.shared.denylist import Denylist
from app.shared.schemas import UserInfo


def test_route_policies_cover_expected_roles() -> None:
    assert policy_for("/auth/login", "POST").public
    assert policy_for("/auth/me", "GET").roles == {
        "consumidor",
        "operador",
        "admin",
    }
    assert policy_for("/auth/usuarios", "POST").roles == {"operador", "admin"}
    assert policy_for("/slices/aprobaciones/1", "POST").roles == {"operador"}
    assert policy_for("/cruds/items", "DELETE").roles == {"admin"}


def test_slice_routes_map_to_slice_manager_api() -> None:
    assert upstream_for("/slices/42/versions") == (
        "slice_manager",
        "/api/v1/slices/42/versions",
    )
    assert upstream_for("/slices/deployments/9/approval") == (
        "slice_manager",
        "/api/v1/deployments/9/approval",
    )


def test_proxy_drops_client_supplied_identity_and_internal_headers() -> None:
    headers = _forward_headers(
        [
            ("x-user-id", "attacker"),
            ("X-User-Role", "admin"),
            ("X-Internal-Token", "forged"),
            ("X-Request-Id", "forged-id"),
            ("Authorization", "Bearer access-token"),
            ("Content-Type", "application/json"),
        ]
    )

    assert "x-user-id" not in {name.lower() for name in headers}
    assert "x-user-role" not in {name.lower() for name in headers}
    assert "x-internal-token" not in {name.lower() for name in headers}
    assert "x-request-id" not in {name.lower() for name in headers}
    assert headers["Authorization"] == "Bearer access-token"


def test_gateway_requires_auth_and_enforces_role(tmp_path) -> None:
    from datetime import datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

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
    private_path = tmp_path / "jwt-private.pem"
    public_path = tmp_path / "jwt-public.pem"
    ca_path = tmp_path / "ca.crt"
    private_path.write_bytes(private_bytes)
    public_path.write_bytes(public_bytes)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    ca_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    settings = Settings(
        database_url="postgresql+psycopg://user:pass@localhost/db",
        jwt_private_key_path=private_path,
        jwt_public_key_path=public_path,
        tls_cert_path=tmp_path / "edge.crt",
        tls_key_path=tmp_path / "edge.key",
        ca_cert_path=ca_path,
        internal_service_token="test-internal-token",
        upstream_cruds_url="http://localhost:8001",
        upstream_slice_manager_url="http://localhost:8000",
        cors_origins=["https://client.example"],
    )
    app = create_app(settings)
    issuer = TokenIssuer(private_bytes.decode("utf-8"), settings)
    consumer_token, _ = issuer.issue(
        UserInfo(id=7, codigo="20260001", rol="consumidor", nivel="basico")
    )

    with TestClient(app) as client:
        unauthenticated = client.get("/auth/me")
        forbidden = client.delete(
            "/cruds/items",
            headers={"Authorization": f"Bearer {consumer_token}"},
        )
        preflight = client.options(
            "/auth/login",
            headers={
                "Origin": "https://client.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert unauthenticated.status_code == 401
    assert unauthenticated.headers["x-request-id"]
    assert forbidden.status_code == 403
    assert preflight.status_code == 204
    assert (
        preflight.headers["access-control-allow-origin"] == "https://client.example"
    )


def test_denylist_revokes_only_preceding_tokens_and_prunes() -> None:
    denylist = Denylist(token_ttl_seconds=900)
    denylist.revoke_user(42, revoked_at=1000.5)

    assert denylist.is_revoked("42", issued_at=1000, now=1001)
    assert not denylist.is_revoked("42", issued_at=1001, now=1001)
    assert not denylist.is_revoked("42", issued_at=900, now=1901)
