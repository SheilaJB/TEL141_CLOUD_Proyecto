import logging
from collections.abc import Iterable

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.config import Settings
from app.gateway.routes import upstream_for
from app.shared.claims import Claims

logger = logging.getLogger(__name__)
router = APIRouter()
PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def _forward_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    forwarded: dict[str, str] = {}
    for name, value in headers:
        normalized = name.lower()
        if (
            normalized in HOP_BY_HOP_HEADERS
            or normalized in {"host", "content-length", "x-internal-token", "x-request-id"}
            or normalized.startswith("x-user-")
        ):
            continue
        forwarded[name] = value
    return forwarded


async def proxy_request(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    upstream = upstream_for(request.url.path)
    if upstream is None:
        raise HTTPException(status_code=404, detail="gateway route not found")
    upstream_name, upstream_path = upstream
    base_url = (
        settings.upstream_cruds_url
        if upstream_name == "cruds"
        else settings.upstream_slice_manager_url
    ).rstrip("/")
    target_url = f"{base_url}{upstream_path}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    headers = _forward_headers(request.headers.items())
    claims: Claims | None = getattr(request.state, "claims", None)
    if claims is not None:
        headers.update(
            {
                "X-User-Id": claims.sub,
                "X-User-Role": claims.rol,
                "X-User-Nivel": claims.nivel or "",
            }
        )
    headers["X-Internal-Token"] = settings.internal_service_token.get_secret_value()
    headers["X-Request-Id"] = request.state.request_id

    try:
        upstream_response = await request.app.state.http_client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=await request.body(),
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Upstream request failed",
            extra={"request_id": request.state.request_id, "upstream": upstream_name},
            exc_info=exc,
        )
        return Response(
            content='{"detail":"upstream service unavailable"}',
            status_code=502,
            media_type="application/json",
        )

    response_headers = {
        name: value
        for name, value in upstream_response.headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
        and name.lower() not in {"content-encoding", "content-length"}
    }
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
    )


@router.api_route("/cruds", methods=PROXY_METHODS)
@router.api_route("/cruds/{path:path}", methods=PROXY_METHODS)
@router.api_route("/slices", methods=PROXY_METHODS)
@router.api_route("/slices/{path:path}", methods=PROXY_METHODS)
async def forward_to_upstream(request: Request) -> Response:
    return await proxy_request(request)
