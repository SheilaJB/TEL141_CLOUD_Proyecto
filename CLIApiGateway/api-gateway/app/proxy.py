"""
proxy.py — Lógica de Reenvío Asíncrono, Sanitización e Inyección de Identidad
Cumple con AUTHENTICATION.rst: Inyecta X-Internal-Token y X-User-*
"""
import logging
import httpx
from typing import Optional, Dict
from fastapi import Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.config import INTERNAL_SERVICE_TOKEN

logger = logging.getLogger(__name__)

HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade"
}


def sanitize_and_prepare_headers(
    request: Request,
    claims: Optional[dict] = None,
    request_id: Optional[str] = None
) -> dict:
    """
    Elimina cabeceras cliente inseguras (antisuplantación) e inyecta
    las cabeceras confiables de identidad y el token de servicio interno.
    """
    forwarded = {}
    for name, value in request.headers.items():
        norm = name.lower()
        # Filtro de hop-by-hop, host y cualquier cabecera X-User-* o X-Internal-Token inyectada por el cliente
        if (
            norm in HOP_BY_HOP_HEADERS
            or norm in {"host", "content-length", "x-internal-token", "x-request-id"}
            or norm.startswith("x-user-")
        ):
            continue
        forwarded[name] = value

    # Inyección de cabeceras seguras para el backend interno
    if request_id:
        forwarded["X-Request-Id"] = request_id
    if INTERNAL_SERVICE_TOKEN:
        forwarded["X-Internal-Token"] = INTERNAL_SERVICE_TOKEN

    if claims:
        forwarded["X-User-Id"] = str(claims.get("sub", ""))
        forwarded["X-User-Role"] = str(claims.get("rol", ""))
        forwarded["X-User-Nivel"] = str(claims.get("nivel") or "")

    return forwarded


async def forward_request(
    client: httpx.AsyncClient,
    method: str,
    target_url: str,
    headers: dict,
    body: Optional[bytes] = None
) -> Response:
    """
    Reenvía una petición estándar en memoria (JSON/REST).
    """
    try:
        upstream_resp = await client.request(
            method=method,
            url=target_url,
            headers=headers,
            content=body
        )
    except httpx.RequestError as exc:
        logger.warning(f"Error conectando a upstream {target_url}: {str(exc)}")
        return Response(
            content='{"detail":"Servicio interno no disponible (Bad Gateway)"}',
            status_code=502,
            media_type="application/json"
        )

    # Filtrar cabeceras de respuesta hop-by-hop
    resp_headers = {
        k: v for k, v in upstream_resp.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() not in {"content-encoding", "content-length"}
    }

    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=resp_headers
    )


async def stream_request(
    method: str,
    target_url: str,
    headers: dict,
    request: Request
) -> StreamingResponse:
    """
    Reenvía la petición como flujo de streaming directo.
    Esencial para subir imágenes pesadas (ISOs/QCOW2) sin colapsar la RAM del Gateway.
    """
    # Timeout extendido (1 hora) para transferencias de archivos masivos
    client = httpx.AsyncClient(timeout=httpx.Timeout(3600.0))
    
    req = client.build_request(
        method=method,
        url=target_url,
        headers=headers,
        content=request.stream()
    )

    try:
        response = await client.send(req, stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        logger.warning(f"Error streaming a upstream {target_url}: {str(exc)}")
        return Response(
            content='{"detail":"Servicio interno no disponible para streaming"}',
            status_code=502,
            media_type="application/json"
        )

    async def close_client():
        await response.aclose()
        await client.aclose()

    resp_headers = {
        k: v for k, v in response.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }

    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers=resp_headers,
        background=BackgroundTask(close_client)
    )
