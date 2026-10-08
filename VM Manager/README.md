# VM Manager: estado actual del módulo

## 1) Alcance y archivos analizados
Este documento describe únicamente lo implementado en:

- `VM Manager/app/main.py`
- `VM Manager/app/libvirt_driver.py`
- `VM Manager/app/qemu_driver.py`

No se describe comportamiento no visible en esos archivos.

---

## 2) Responsabilidad de cada archivo

| Archivo | Responsabilidad principal |
|---|---|
| `main.py` | API FastAPI del módulo VM Manager. Valida entrada, consulta Image Manager, orquesta creación/eliminación de VM y maneja reintentos/errores HTTP. |
| `libvirt_driver.py` | Driver remoto basado en **libvirt** + SSH. Prepara imagen base/overlay qcow2, define VM por XML y la levanta en KVM. También limpia recursos de VM/overlay/base. |
| `qemu_driver.py` | Driver remoto basado en **QEMU directo por CLI** + SSH (sin libvirt). Crea TAP/OVS, ejecuta `qemu-system-x86_64` en background y limpia runtime manualmente. |

Estado de integración actual: `main.py` importa y usa `KVMRemoteDriver` (libvirt). `QEMURemoteDriver` está disponible pero no está conectado a endpoints en este archivo.

---

## 3) Flujo de interacción (estado actual)

### 3.1 Flujo de creación de VM (`POST /vms/create`)
1. **FastAPI (`main.py`)** recibe `CreateVMRequest`.
2. Valida `vm_id` (regex) y `worker_ip` (lista permitida).
3. Consulta **Image Manager** vía HTTP `GET {IMAGE_SERVICE_URL}/images/{image_id}`.
4. Si existe imagen, arma:
   - `base_image_path` (directorio base en worker),
   - `overlay_path` (disco VM qcow2),
   - `download_url` (endpoint de descarga de imagen).
5. Instancia `KVMRemoteDriver(worker_ip=...)`.
6. Reintenta hasta `MAX_RETRIES`:
   - `prepare_qcow2_overlay(...)`:
     - SSH + `wget` + `flock` para descargar base (si falta),
     - verifica SHA256 (si viene),
     - crea overlay con `qemu-img`.
   - `define_and_start_vm(...)`:
     - libvirt abre `qemu+ssh://.../system?no_verify=1`,
     - genera XML de dominio (CPU/RAM/disco/redes/VNC),
     - crea VM y recupera PID y puerto VNC.
7. Devuelve JSON de éxito con `vm_id`, `worker`, `process_id`, `vnc_port`, `attempts`.
8. Si falla un intento: `cleanup_partial(...)` + backoff (`sleep(2*attempt)`).
9. Si fallan todos: HTTP 500 con detalle del último error.

### 3.2 Flujo de eliminación (`DELETE /vms/{worker_ip}/{vm_id}`)
1. FastAPI valida `vm_id` y `worker_ip`.
2. Construye `overlay_path`.
3. Llama `destroy_and_smart_clean(vm_name, overlay_path)` en `KVMRemoteDriver`.
4. Driver:
   - apaga dominio (si existe),
   - borra overlay,
   - detecta backing file (base) y lo elimina solo si no detecta otros overlays usándolo,
   - retorna detalle de limpieza.
5. API responde `{"status":"DELETED", ...}` o HTTP 500 si ocurre excepción.

### 3.3 Capas técnicas involucradas
- **FastAPI**: API REST, validaciones, códigos HTTP.
- **Image Manager**: catálogo de imágenes (`/images/{id}`) y descarga (`/images/{id}/download`).
- **SSH**: canal de ejecución remota con `sudo` en workers.
- **libvirt/QEMU**:
  - Flujo activo en API: libvirt (`createXML`) sobre KVM.
  - Flujo alterno implementado: QEMU CLI directo (`qemu-system-x86_64`).
- **Almacenamiento**: base image + overlay qcow2.
- **Redes**:
  - libvirt: interfaces tipo `bridge` + `openvswitch` en XML.
  - QEMU directo: TAP + `ovs-vsctl add-port` + `virtio-net`.

---

## 4) Endpoints, datos de entrada y respuestas

### 4.1 `GET /health`
- **Entrada**: sin body.
- **Salida 200**:

```json
{"status":"ok","service":"VM Manager"}
```

### 4.2 `POST /vms/create`
- **Body (`CreateVMRequest`)**:

```json
{
  "vm_id": "vm-demo-01",
  "image_id": "ubuntu-22.04",
  "worker_ip": "10.0.10.1",
  "vcpus": 2,
  "memory_ram_mb": 2048,
  "interfaces": [
    {"bridge": "br-int", "vlan": 100},
    {"bridge": "br-mgmt"}
  ],
  "seed_iso": "/var/lib/libvirt/images/seed/vm-demo-01.iso"
}
```

- **Validaciones principales**:
  - `vm_id`: `^[A-Za-z0-9._-]{1,48}$`
  - `worker_ip`: debe estar en `ALLOWED_WORKERS`
  - `memory_ram_mb >= min_ram_mb` reportado por Image Manager

- **Respuesta éxito 200**:

```json
{
  "status": "SUCCESS",
  "vm_id": "vm-demo-01",
  "worker": "10.0.10.1",
  "process_id": 12345,
  "vnc_port": 5903,
  "attempts": 1
}
```

- **Errores típicos**:
  - 400: `vm_id` inválido / `worker_ip` no permitido / imagen no existe / RAM insuficiente.
  - 502: fallo de conexión hacia Image Manager.
  - 500: agotó reintentos al desplegar.

### 4.3 `DELETE /vms/{worker_ip}/{vm_id}`
- **Parámetros**: `worker_ip`, `vm_id`.
- **Respuesta éxito 200** (ejemplo):

```json
{
  "status": "DELETED",
  "vm_id": "vm-demo-01",
  "worker": "10.0.10.1",
  "cleaned_disk": "/var/lib/libvirt/images/overlays/vm-demo-01.qcow2",
  "base_image": "/var/lib/libvirt/images/base/ubuntu-22.04.qcow2",
  "base_removed": false
}
```

- **Error**:
  - 500: error al limpiar VM en worker.

---

## 5) Clases y métodos principales

### 5.1 `KVMRemoteDriver` (`libvirt_driver.py`)
- `__init__(worker_ip, user="ubuntu")`: define URI libvirt remota.
- `_exec_remote_ssh(command, timeout=600)`: ejecuta comandos por SSH con `sudo`.
- `prepare_qcow2_overlay(...)`: descarga imagen base (con lock), valida hash, crea overlay.
- `_build_xml(...)`: arma XML de dominio KVM/libvirt (disco, NIC, VNC, opcional ISO).
- `define_and_start_vm(...)`: crea VM vía libvirt, obtiene PID real y puerto VNC.
- `cleanup_partial(vm_name, overlay_path)`: limpieza entre reintentos.
- `destroy_and_smart_clean(vm_name, overlay_path)`: apaga VM, borra overlay y opcionalmente base.

### 5.2 `QEMURemoteDriver` (`qemu_driver.py`)
- `__init__(worker_ip, user="ubuntu", run_dir="/run/vms")`.
- `_exec_remote_ssh(...)`, `_sh(...)`, `_check_name(...)`.
- `_tap_name(...)`, `_mac(...)`: nombres TAP y MAC determinísticos.
- `prepare_qcow2_overlay(...)`: similar a libvirt, con opción `disk_gb`.
- `define_and_start_vm(...)`: crea TAP/OVS y lanza `qemu-system-x86_64 -daemonize`.
- `_teardown_runtime(...)`: mata QEMU y limpia TAP/OVS.
- `cleanup_partial(...)` y `destroy_and_smart_clean(...)`: limpieza runtime + discos.

---

## 6) Reintentos y reporte de errores

En `main.py`, creación de VM usa:
- `MAX_RETRIES = 3`.
- Backoff lineal simple: espera `2, 4` segundos entre intentos.
- En cada fallo:
  - guarda `last_error`,
  - ejecuta `cleanup_partial(...)`,
  - continúa si quedan intentos.
- Si no recupera: HTTP 500 con detalle `"Último error: ..."` del intento final.

Errores internos de drivers suelen propagarse como `Exception` con mensajes de SSH/libvirt/QEMU.

---

## 7) Diferencias: flujo libvirt vs flujo QEMU directo

| Tema | libvirt (`KVMRemoteDriver`) | QEMU directo (`QEMURemoteDriver`) |
|---|---|---|
| Integración en API actual | **Sí** (`main.py`) | **No** (clase disponible, no usada en endpoints) |
| Arranque VM | `conn.createXML(xml, 0)` | `qemu-system-x86_64 ... -daemonize` |
| Definición de hardware | XML libvirt | Argumentos CLI QEMU |
| Networking | `<interface type='bridge'>` + OVS virtualport | TAP manual + `ovs-vsctl add-port` |
| Estado runtime | gestionado por libvirt | archivos en `/run/vms` (`.pid`, `.taps`) |
| Lectura VNC | parseo XML (`graphics@port`) | sondeo de `ss -ltnp` por PID |

Ejemplo de XML generado (resumen):

```xml
<domain type='kvm'>
  <name>vm-demo-01</name>
  <memory unit='KiB'>2097152</memory>
  <vcpu>2</vcpu>
  <devices>
    <disk type='file' device='disk'>...</disk>
    <graphics type='vnc' port='-1' autoport='yes' listen='0.0.0.0'/>
  </devices>
</domain>
```

---

## 8) Hardcodeos y configuración actual

### 8.1 Valores fijos en código
- `REMOTE_BASE_DIR = "/var/lib/libvirt/images/base"`
- `REMOTE_DISKS_DIR = "/var/lib/libvirt/images/overlays"`
- `MAX_RETRIES = 3`
- Usuario SSH por defecto en drivers: `"ubuntu"`
- SSH options fijas: `StrictHostKeyChecking=no`, `BatchMode=yes`, `ConnectTimeout=10`
- `KVMRemoteDriver.uri`: `qemu+ssh://{user}@{worker_ip}/system?no_verify=1`
- QEMU directo:
  - binario `qemu-system-x86_64`
  - VNC `-vnc 0.0.0.0:0,to=99`
  - `run_dir="/run/vms"` (default)

### 8.2 Variables de entorno
- `IMAGE_SERVICE_URL` (default `http://10.0.10.4:8001`)
- `ALLOWED_WORKERS` (default `"10.0.10.1,10.0.10.2,10.0.10.3"`)

### 8.3 Parámetros recibidos por API
- `vm_id`, `image_id`, `worker_ip`, `vcpus`, `memory_ram_mb`, `interfaces[]`, `seed_iso`.

---

## 9) Riesgos e inconsistencias observables (sin cambiar código)

1. **Driver QEMU no integrado en API**: existe duplicidad funcional (libvirt y QEMU directo), pero endpoints usan solo libvirt.
2. **Mensajería de error potencialmente sensible**: errores SSH/infraestructura pueden exponerse en respuestas 500.
3. **`interfaces` con lista mutable por defecto** en modelo (`[]`), patrón propenso a efectos colaterales en Python.
4. **`StrictHostKeyChecking=no` y `no_verify=1`** reducen garantías de verificación del host remoto.
5. **Variable importada no usada en `libvirt_driver.py`** (`subprocess`), señal menor de higiene.
6. **`destroy_and_smart_clean` está definido dos veces en `libvirt_driver.py`**: la segunda definición sobrescribe la primera.
7. **Detección de “usuarios de base image” vía `grep` en overlays** puede ser frágil frente a formatos/cambios de metadatos qcow2.
8. **`worker_ip` permitido por lista estática** (env/default), no por descubrimiento dinámico.

---

## 10) Resumen corto del estado actual
- El camino productivo actual de la API es **FastAPI + Image Manager + KVMRemoteDriver (libvirt)**.
- Existe implementación alternativa **QEMURemoteDriver** lista para operación directa por SSH, pero no conectada desde `main.py`.
- El módulo ya contempla validación básica, retries, limpieza parcial y limpieza “smart” de base image, con varios valores y supuestos actualmente hardcodeados.
