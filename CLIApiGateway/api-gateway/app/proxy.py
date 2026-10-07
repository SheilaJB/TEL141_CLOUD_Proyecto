"""
proxy.py — Lógica de Reenvío Asíncrono (Proxy)

Implementa el reenvío de peticiones HTTP hacia la red interna usando httpx.
Contiene soporte nativo para Streaming (vital para subir archivos sin colapsar la RAM).
"""
import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

async def forward_request(method: str, target_url: str, headers: dict, body: bytes = None) -> Response:
    """
    Reenvía una petición estándar (ej. envíos de JSON) hacia un servicio interno.
    """
    # Filtramos la cabecera 'host' porque el cliente HTTP debe definir la suya hacia la IP interna
    filtered_headers = {k: v for k, v in headers.items() if k.lower() not in ["host", "content-length"]}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method=method,
            url=target_url,
            headers=filtered_headers,
            content=body
        )

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )

async def stream_request(method: str, target_url: str, headers: dict, request: Request) -> StreamingResponse:
    """
    Reenvía la petición como un "tubo" de Streaming directo.
    Se usa para subidas pesadas (multipart/form-data). Evita guardar archivos de GBs en la RAM del Gateway.
    """
    filtered_headers = {k: v for k, v in headers.items() if k.lower() not in ["host"]}
    
    # Timeout muy generoso (1 hora) por si suben una imagen ISO/QCOW2 grande
    client = httpx.AsyncClient(timeout=httpx.Timeout(3600.0))
    
    req = client.build_request(
        method=method,
        url=target_url,
        headers=filtered_headers,
        content=request.stream()
    )
    
    response = await client.send(req, stream=True)

    async def close_client():
        await response.aclose()
        await client.aclose()

    return StreamingResponse(
        response.aiter_raw(),
        status_code=response.status_code,
        headers=dict(response.headers),
        background=BackgroundTask(close_client)
    )
