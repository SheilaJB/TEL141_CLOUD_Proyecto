"""
main.py — Punto de Entrada del API Gateway

Actúa como Proxy Inverso inteligente. Redirige el tráfico de UI/CLI hacia la red interna.
Cumple estrictamente con el Diseño: NO decodifica JWT, NO autoriza roles. Solo enruta y transporta datos.
"""
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from app.config import ROUTE_MAP, MAX_BODY_SIZE
from app.proxy import forward_request, stream_request

app = FastAPI(
    title="API Gateway - Grupo 3", 
    description="Puerta de entrada única del Orquestador Cloud. Enruta tráfico UI/CLI a microservicios."
)

# Configuración de CORS: Obligatorio para evitar que los navegadores bloqueen la comunicación web
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En un entorno real, colocar la IP del dominio web Frontend
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health_check():
    """Endpoint básico para monitoreo. Confirma que el API Gateway está en línea."""
    return {"status": "ok", "service": "API Gateway"}

@app.api_route("/api/v1/{service}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def catch_all_gateway(service: str, path: str, request: Request):
    """
    Ruta Comodín (Catch-all). Todo el tráfico entra por aquí.
    
    Ejemplo de flujo:
      - Cliente envía POST a http://gateway:8080/api/v1/auth/login
      - Esta función detecta que service="auth" y path="login"
      - Reenvía el tráfico con httpx hacia http://auth:8000/auth/login
    """
    # 1. Validar si el prefijo solicitado existe en nuestra configuración
    if service not in ROUTE_MAP:
        raise HTTPException(
            status_code=404, 
            detail=f"Servicio destino '{service}' no existe en el mapa de ruteo del Gateway."
        )

    # Construir la URL final dentro de la red interna de Docker
    # IMPORTANTE: Si la URL base ya tiene path (ej http://auth:8000/auth) asegúrate de ajustarlo.
    # Con el diseño propuesto: http://auth:8000/auth/{path}
    target_base_url = ROUTE_MAP[service].rstrip("/")
    target_url = f"{target_base_url}/{path.lstrip('/')}"
    
    # Conservar los query params (ej. ?limit=10)
    if request.url.query:
        target_url += f"?{request.url.query}"

    # 2. Evaluar tipo de carga (Streaming vs Memoria)
    content_type = request.headers.get("content-type", "")
    is_streamed_upload = content_type.startswith("multipart/form-data")

    # 3. Cargar cuerpo de la petición si es un JSON normal
    body = None
    if not is_streamed_upload:
        body = await request.body()
        if len(body) > MAX_BODY_SIZE:
            raise HTTPException(
                status_code=413,
                detail="Payload demasiado grande (excede 2MB). Use carga en streaming (multipart) para archivos."
            )

    # 4. Extraer cabeceras (pasamos la cabecera Authorization tal cual, sin leerla nosotros)
    headers = dict(request.headers)

    # 5. Ejecutar reenvío
    try:
        if is_streamed_upload:
            return await stream_request(request.method, target_url, headers, request)
        else:
            return await forward_request(request.method, target_url, headers, body)
    except Exception as e:
        # Atrapar si el microservicio destino está caído (Connection Refused)
        raise HTTPException(status_code=502, detail=f"Bad Gateway: Error al contactar servicio {service}. Detalles: {str(e)}")
