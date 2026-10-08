# Image Manager y VM Manager

##  Rol de cada módulo en la arquitectura

Ambos módulos están en la Capa 3: Servicios Internos y son *ejecutores*: NO DECIDEN NADA CARAMBAS >:V, reciben las órdenes ya resueltas.

| Módulo | Qué hace 
|---|---
| **Image Manager** | Mantiene el catálogo de imágenes (qcow2) en el server 4 y las sirve a los workers. |
| **VM Manager** | Crea y borra VMs en un worker usando QEMU/KVM + Open vSwitch.

**De dónde viene cada dato:**
- **RAM, vCPUs, disco, imagen:** los define el usuario al crear el slice y llegan hasta VM Manager vía Slice Manager.
- **Worker donde se despliega:** lo decide VM Placement (en la fase 1, round robin: VM1,VM4 → W1; VM2,VM5 → W2; VM3,VM6 → W3) y llega como `worker_ip`.


**Flujo de creación de una VM:**
1. Slice Manager llama a `POST /vms/create` con todo resuelto.
2. VM Manager pide los metadatos de la imagen a Image Manager.
3. El worker descarga la imagen base desde Image Manager (si no la tiene).
4. VM Manager crea un disco overlay y lanza QEMU en el worker.
5. Responde con `process_id` (PID de QEMU), `vnc_port` y el worker.

##  Archivos

```
image_manager/
└── vm_image.py            # Image Manager (FastAPI)
vm_manager/app/
├── main.py                # VM Manager (FastAPI): endpoints, validaciones, reintentos
└── qemu_driver.py         # Driver QEMU/KVM + OVS remoto (por SSH)
```
---

##  `vm_image.py` – Image Manager

### Qué hace
API en FastAPI que administra el catálogo de imágenes de disco guardadas en la carpeta `Storage/` del server 4 (puerto 8001). Los workers 1, 2 y 3 descargan de aquí la imagen base, y VM Manager consulta sus metadatos para validar recursos.

###  Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Confirma que el servicio está en línea. |
| GET | `/images` | Lista el catálogo e indica si el archivo físico existe (`available`). |
| GET | `/images/{image_id}` | Metadatos: formato, `min_ram_mb`, `min_disk_gb`, `sha256`. |
| GET | `/images/{image_id}/download` | Descarga la imagen; incluye el header `X-Checksum-SHA256`. |
| POST | `/images/upload` | Sube una imagen nueva (multipart). Parámetros en query: `image_id`, `img_format`, `min_ram_mb`, `min_disk_gb`. |
| DELETE | `/images/{image_id}` | Quita la imagen del catálogo y borra el archivo si ninguna otra entrada lo usa. |





###  Ejecución GAAA
```bash
pip install fastapi uvicorn python-multipart
uvicorn vm_image:app --host 0.0.0.0 --port 8001
```

---

##  `qemu_driver.py` – Driver QEMU/KVM + Open vSwitch

### Qué hace
Clase `QEMURemoteDriver`: ejecuta comandos por SSH en un worker para preparar discos, lanzar y destruir VMs. (Reemplaza a libvirt que era mejor pipipip).

### Cómo funciona

| Tarea | Implementación |
|---|---|
| **Ejecutar en el worker** | `ssh` con lista de argumentos (sin `shell=True`), `BatchMode`, timeout. Los nombres se validan con regex para evitar inyección. |
| **Descargar imagen base** | `wget` a un `.tmp` + `mv` atómico, protegido con `flock`. Así VM1 y VM4 (mismo worker) no se pisan. |
| **Verificar integridad** | Compara el `sha256sum` de la copia en el worker con el del catálogo; si no coincide, borra la copia. |
| **Disco de la VM** | Overlay: `qemu-img create -f qcow2 -b <base> -F qcow2 <overlay> [tamaño]`. Si existe uno de un intento anterior, lo recrea. |
| **Red (L2)** | Por cada interfaz: crea un `tap`, lo sube y hace `ovs-vsctl --may-exist add-port <bridge> <tap> [tag=<vlan>]`. Los nombres de tap y MAC son determinísticos. |
| **Arranque** | `qemu-system-x86_64 -name <vm> -enable-kvm -cpu host -m <MB> -smp <n> -drive ... -netdev tap ... -vnc 0.0.0.0:0,to=99 -daemonize -pidfile /run/vms/<vm>.pid`. |
| **PID** | Se lee del pidfile (PID real de QEMU) y se confirma con `kill -0`. |
| **Puerto VNC** | `to=99` toma el primer display libre; el puerto real se obtiene con `ss -ltnp` filtrando por el PID. |
| **Estado por VM** | `/run/vms/<vm>.pid` y `/run/vms/<vm>.taps`  |

### Métodos principales

| Método | Función |
|---|---|
| `prepare_qcow2_overlay(...)` | Descarga la base (si falta), verifica sha256 y crea el overlay. Acepta `disk_gb`. |
| `define_and_start_vm(...)` | Crea taps/OVS, lanza QEMU y devuelve `{vm_id, worker, pid, vnc_port}`. Acepta `interfaces` y `seed_iso`. |
| `cleanup_partial(...)` | Limpia restos de un intento fallido (proceso, taps, overlay) antes de reintentar. |
| `destroy_and_smart_clean(...)` | Apaga la VM, quita taps, borra el overlay y, si nadie más usa la base, la borra también. |



---

##  `main.py` – VM Manager

### Qué hace
API en FastAPI que expone la creación y el borrado de VMs, valida los datos y usa `QEMURemoteDriver` para ejecutarlos en el worker.

### Configuración

| Variable | Valor por defecto | Uso |
|---|---|---|
| `IMAGE_SERVICE_URL` | `http://10.0.10.4:8001` | Dirección del Image Manager. |
| `ALLOWED_WORKERS` | `10.0.10.1,10.0.10.2,10.0.10.3` | Workers permitidos. |
| `REMOTE_BASE_DIR` | `/var/lib/vms/base` | Imágenes base dentro del worker. |
| `REMOTE_DISKS_DIR` | `/var/lib/vms/overlays` | Overlays dentro del worker. |

### 5.3 Endpoints

**`POST /vms/create`**

```json
{
  "vm_id": "VM1",
  "image_id": "cirros",
  "worker_ip": "10.0.10.1",
  "vcpus": 1,
  "memory_ram_mb": 512,
  "disk_gb": 5,
  "interfaces": [{"bridge": "br-int", "vlan": 100}],
  "seed_iso": null
}
```

Respuesta exitosa:

```json
{
  "status": "SUCCESS",
  "vm_id": "VM1",
  "worker": "10.0.10.1",
  "process_id": 4321,
  "vnc_port": 5901,
  "attempts": 1
}
```

**`DELETE /vms/{worker_ip}/{vm_id}`** – borra la VM y aplica el borrado:

```json
{
  "status": "DELETED",
  "vm_id": "VM1",
  "worker": "10.0.10.1",
  "cleaned_disk": "/var/lib/vms/overlays/VM1.qcow2",
  "base_image": "/var/lib/vms/base/cirros-0.6.2-x86_64-disk.img",
  "base_removed": true
}
```

**`GET /health`** – estado del servicio.

### Validaciones
- `vm_id` solo con letras, números, `.`, `_` o `-` (máx. 48 caracteres).
- `worker_ip` debe estar en `ALLOWED_WORKERS`.
- La RAM pedida no puede ser menor al `min_ram_mb` de la imagen (HTTP 400).
- Si se indica `disk_gb`, no puede ser menor al `min_disk_gb` de la imagen (HTTP 400).
- Si el Image Manager no responde: HTTP 502. Si la imagen no existe: HTTP 400.

### Reintentos 
Hasta **3 intentos**, con espera. Entre intentos se llama a `cleanup_partial`, que deja el worker limpio (proceso, taps y overlay). Si los 3 fallan, responde HTTP 500 con el último error. La respuesta exitosa informa cuántos intentos se usaron (`attempts`).

---




