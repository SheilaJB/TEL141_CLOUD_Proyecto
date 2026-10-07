# TEL141 Auth + API Gateway

Servicio FastAPI `edge` que ejecuta auth y gateway en un solo proceso y un solo
worker. Sigue el alcance descrito en `auth - api gw.md`; no agrega servicios
upstream al Compose.

## Inicio

1. Copia `.env.example` a `.env` y ajusta credenciales de DB, URLs upstream y
   orígenes CORS.
2. Genera por fuera de este proyecto los archivos indicados por
   `JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH`, `TLS_CERT_PATH`,
   `TLS_KEY_PATH` y `CA_CERT_PATH`. El certificado del edge debe incluir los
   SAN que usarán los clientes. No se genera ni se almacena material de claves
   en el repositorio.
3. Pon esos archivos en un directorio local ignorado por Git, por ejemplo
   `keys/`, y configura `EDGE_KEYS_DIR` con su ruta.
4. Levanta el proceso con `docker compose up --build`.

El Compose publica únicamente el puerto TLS del edge (443 por defecto), monta
las claves/certificados como solo lectura y no inicia Postgres, `cruds` ni
`slice-manager`. `DATABASE_URL`, `UPSTREAM_CRUDS_URL` y
`UPSTREAM_SLICE_MANAGER_URL` deben apuntar a recursos accesibles desde la red
Docker. En Windows con Docker Desktop, `host.docker.internal` sirve para
conectarse a servicios publicados en el host.

## Base de datos

La aplicación consume `auth.usuario`, `auth.rol`, `auth.nivel` y
`auth.refresh_token` existentes. No ejecuta migraciones ni modifica seeds. El
URL debe usar SQLAlchemy async con psycopg (esquema
`postgresql+psycopg://usuario:password@host:5432/cloud_g3`). Las cuentas deben
tener hashes Argon2 válidos en `hash_password`; los valores placeholder actuales
no permiten iniciar sesión.

## Rutas principales

- `POST /auth/login`: `client=web` establece cookie HttpOnly segura; `client=cli`
  devuelve el refresh token en JSON.
- `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`.
- `POST /auth/usuarios` y `PATCH /auth/usuarios/{user_id}` para operador/admin.
- `/cruds/**` pasa al upstream conservando el path.
- `/slices/**` pasa a la API del slice manager bajo `/api/v1`; las rutas
  `/slices/deployments/**` y `/slices/aprobaciones/**` se mapean directamente
  bajo ese prefijo.
- `GET /healthz` es público.

Login, refresh y logout son rutas públicas del middleware de acceso; refresh y
logout siguen exigiendo un refresh token válido. El rate limit es por IP y
código de usuario, en memoria, con el límite `LOGIN_ATTEMPTS_PER_MINUTE`.

El gateway envía `X-Internal-Token`, `X-User-*` y `X-Request-Id` al upstream.
Los middleware internos de `cruds` y `slice-manager` quedan fuera de este
proyecto; los servicios deben incorporarlos antes de confiar en esas cabeceras.

## Pruebas locales

```powershell
py -m pip install -e ".[test]"
py -m pytest
```

Los tests de gateway no necesitan conectarse a la base de datos.
