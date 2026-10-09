# Compute Manager (Image Manager + VM Manager)

Un solo servicio

```
Compute Manager/
├── app/
│   ├── main.py              # arranque
│   ├── config.py            # variables de entorno
│   ├── db.py                # pool PostgreSQL
│   ├── images/              # catálogo y descarga de imágenes   (/image/...)
│   │   ├── router.py
│   │   └── store.py         # archivos en Storage/, sha256, validación qcow2
│   ├── vms/                 # ciclo de vida de VMs               (/vms/...)
│   │   ├── router.py
│   │   ├── service.py       # máquina de estados + reintentos
│   │   └── repository.py    # SQL sobre slice_nodo 
│   └── adapters/
│       └── qemu_driver.py   # Adaptador Linux: QEMU/KVM + OVS por SSH
├── Storage/                 # imágenes qcow2 

```

## Estado de las VMs (slices.slice_nodo.estado_nodo)

| Acción | Desde | Hacia |
|---|---|---|
| `create` | `PENDING` (= Creating), `FAILED` | `RUNNING`; si se agotan los reintentos, `FAILED` |
| `stop` | `RUNNING` | `STOPPED` (conserva overlay y taps) |
| `start` | `STOPPED` | `RUNNING` (si falla, queda `STOPPED`) |
| `delete` | `RUNNING`, `STOPPED`, `FAILED` | `DELETED` (borrado inteligente) |



## De dónde sale cada dato CARAMBASSSS

| Dato | Fuente |
|---|---|
| vCPU, RAM, disco | `flavor` (`slice_nodo.flavor_id`: `vcpu`, `vram_mb`, `vdisk_mb`) |
| Imagen | `image` (`slice_nodo.imagen_id`; `ruta_referencia` = nombre del archivo en `Storage/`) |
| Interfaces, VLAN, MAC | `slice_enlace_puerto` (filas del nodo; `vlan_tag`, `mac`) |
| Worker | `reserva_nodo` (reserva `RESERVED`; si no hay, la más reciente) → `servidor.ip_serv` |
| Nombre de la VM en QEMU | UUID del nodo |
| Resultado | se escribe en `slice_nodo`: `estado_nodo`, `pid`, `puerto_vnc` |

## Endpoints

**VMs** 

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/vms?slice_id=&estado=` | Lista de VMs |
| GET | `/vms/{nodo_id}?verify=true` | Estado; con `verify` consulta al worker una vez y corrige la BD (VM caída → `FAILED`) |
| POST | `/vms/{nodo_id}/create` | Body opcional: `{"worker_ip": "...", "seed_iso": "..."}` |
| POST | `/vms/{nodo_id}/start` | Body opcional: `{"seed_iso": "..."}` |
| POST | `/vms/{nodo_id}/stop` | |
| DELETE | `/vms/{nodo_id}` | |

Respuesta de `create`: `{"status","nodo_id","estado_nodo","worker","pid","puerto_vnc","attempts"}`.

**Imágenes** 

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/image/` | Catálogo (`slices.image`) + `available` |
| GET | `/image/{id}` | Metadatos + `sha256` |
| GET | `/image/{id}/download` | Descarga (workers); header `X-Checksum-SHA256` |
| POST | `/image/upload?nombre=&cluster_compatible=` | Multipart `file`. Solo qcow2 (se valida la cabecera) |
| DELETE | `/image/{id}` | Baja lógica (`DELETED`); 409 si algún nodo la usa |

## Configuración

| Variable | Por defecto |
|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@db:5432/cloud_g3` |
| `STORAGE_DIR` | `<carpeta>/Storage` |
| `COMPUTE_PUBLIC_URL` | `http://10.0.10.4:8001` (URL con la que los workers descargan imágenes) |
| `ALLOWED_WORKERS` | `10.0.10.1,10.0.10.2,10.0.10.3` |
| `WORKER_SSH_USER` | `ubuntu` |
| `OVS_BRIDGE` | `br-int` |
| `REMOTE_BASE_DIR` / `REMOTE_DISKS_DIR` | `/var/lib/vms/base` / `/var/lib/vms/overlays` |
| `MAX_RETRIES` / `RETRY_BACKOFF_S` | `3` / `2` |

## Ejecutar

```bash
pip install -r requirements.txt
DATABASE_URL=postgresql://... uvicorn app.main:app --host 0.0.0.0 --port 8001
```




## Pendientes

- VM Placement debe escribir `reserva_nodo` (si no, pasar `worker_ip` en el body de `create`). Si Placement libera la reserva al hacer `stop`, `start` no sabrá el worker.
- el `estado_nodo` lo escribe solo el Compute Manager.


