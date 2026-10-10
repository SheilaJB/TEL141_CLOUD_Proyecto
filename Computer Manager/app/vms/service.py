import os
import time
from typing import Optional

from fastapi import HTTPException

from app import config, db
from app.adapters.qemu_driver import NonRetryableError, QEMURemoteDriver
from app.images import store
from app.vms import repository as repo

VALID_STATES = {"PENDING", "RUNNING", "STOPPED", "FAILED", "DELETED"}


ALLOWED_FROM = {
    "create": {"PENDING", "FAILED"},
    "start": {"STOPPED"},
    "stop": {"RUNNING"},
    "delete": {"RUNNING", "STOPPED", "FAILED"},
}


def driver_factory(worker_ip: str):
    
    return QEMURemoteDriver(worker_ip=worker_ip, user=config.SSH_USER)



def _load_node(conn, nodo_id: str) -> dict:
    node = repo.get_node(conn, nodo_id)
    if not node:
        raise HTTPException(404, "Nodo (VM) no encontrado")
    return node


def _require_state(node: dict, action: str):
    if node["estado_nodo"] not in ALLOWED_FROM[action]:
        raise HTTPException(
            409,
            f"No se puede ejecutar '{action}' con la VM en estado {node['estado_nodo']} "
            f"(requiere: {', '.join(sorted(ALLOWED_FROM[action]))})",
        )


def _check_worker(ip: Optional[str]):
    if not ip:
        raise HTTPException(409, "El nodo no tiene worker asignado (falta la reserva de VM Placement)")
    if ip not in config.ALLOWED_WORKERS:
        raise HTTPException(409, f"El worker {ip} no está gestionado por el adaptador Linux")


def _overlay_path(nodo_id: str) -> str:
    return f"{config.REMOTE_DISKS_DIR}/{nodo_id}.qcow2"


def _interfaces(ports: list) -> list:
    return [
        {"bridge": config.OVS_BRIDGE, "vlan": p["vlan_tag"], "mac": p["mac"]} for p in ports
    ]


def _set_state(nodo_id: str, estado: str, pid: Optional[int] = None, vnc: Optional[int] = None):
    with db.tx() as conn:
        repo.set_state(conn, nodo_id, estado, pid, vnc)


def create_vm(nodo_id: str, worker_ip: Optional[str] = None, seed_iso: Optional[str] = None) -> dict:
    # PENDING/FAILED -> RUNNING (con reintentos)
    with db.node_lock(nodo_id):
        with db.tx() as conn:
            node = _load_node(conn, nodo_id)
            _require_state(node, "create")
            image = repo.get_image_for_create(conn, node["imagen_id"])
            ports = repo.get_ports(conn, nodo_id)
            worker = worker_ip or repo.find_worker_ip(conn, nodo_id)

        _check_worker(worker)
        if not image or image["estado"] != "ACTIVE":
            raise HTTPException(409, "La imagen del nodo no está disponible (estado distinto de ACTIVE)")
        if image["cluster"] not in (None, "linux"):
            raise HTTPException(409, f"La imagen es del cluster '{image['cluster']}', no del cluster Linux")
        path = store.file_path(image)
        if not os.path.exists(path):
            raise HTTPException(409, f"El archivo de la imagen ({os.path.basename(path)}) no existe en Storage")

        sha = store.sha256_cached(path)
        filename = os.path.basename(path)
        base_path = f"{config.REMOTE_BASE_DIR}/{filename}"
        overlay = _overlay_path(nodo_id)
        url = f"{config.PUBLIC_URL}/image/{image['id']}/download"
        driver = driver_factory(worker)

        last_error = None
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                print(f"Intento {attempt}/{config.MAX_RETRIES}: creando {nodo_id} en {worker}")
                driver.prepare_qcow2_overlay(url, base_path, overlay, sha, disk_mb=node["vdisk_mb"])
                vm = driver.define_and_start_vm(
                    nodo_id, node["vram_mb"], node["vcpu"], overlay, _interfaces(ports), seed_iso
                )
                _set_state(nodo_id, "RUNNING", vm["pid"], vm["vnc_port"])
                return {
                    "status": "SUCCESS",
                    "nodo_id": nodo_id,
                    "estado_nodo": "RUNNING",
                    "worker": worker,
                    "pid": vm["pid"],
                    "puerto_vnc": vm["vnc_port"],
                    "attempts": attempt,
                }
            except Exception as e:
                last_error = str(e)
                print(f"[!] Fallo en intento {attempt}: {last_error}")
                driver.cleanup_partial(nodo_id, overlay, base_path)
                if isinstance(e, NonRetryableError):
                    break
                if attempt < config.MAX_RETRIES:
                    time.sleep(config.RETRY_BACKOFF_S * attempt)

        _set_state(nodo_id, "FAILED")
        raise HTTPException(500, f"Falló la creación de {nodo_id} en {worker}: {last_error}")


def start_vm(nodo_id: str, seed_iso: Optional[str] = None, worker_ip: Optional[str] = None) -> dict:
    # STOPPED -> RUNNING
    with db.node_lock(nodo_id):
        with db.tx() as conn:
            node = _load_node(conn, nodo_id)
            _require_state(node, "start")
            ports = repo.get_ports(conn, nodo_id)
            worker = worker_ip or repo.find_worker_ip(conn, nodo_id)
        _check_worker(worker)

        driver = driver_factory(worker)
        try:
            vm = driver.start_vm(
                nodo_id, node["vram_mb"], node["vcpu"], _overlay_path(nodo_id), _interfaces(ports), seed_iso
            )
        except Exception as e:
            if not isinstance(e, NonRetryableError):
                try:
                    driver.stop_vm(nodo_id)        # no dejar un QEMU a medias
                except Exception:
                    pass
            raise HTTPException(500, f"No se pudo iniciar la VM {nodo_id}: {e}")

        _set_state(nodo_id, "RUNNING", vm["pid"], vm["vnc_port"])
        return {
            "status": "SUCCESS", "nodo_id": nodo_id, "estado_nodo": "RUNNING",
            "worker": worker, "pid": vm["pid"], "puerto_vnc": vm["vnc_port"],
        }


def stop_vm(nodo_id: str, worker_ip: Optional[str] = None) -> dict:
    # RUNNING -> STOPPED
    with db.node_lock(nodo_id):
        with db.tx() as conn:
            node = _load_node(conn, nodo_id)
            _require_state(node, "stop")
            worker = worker_ip or repo.find_worker_ip(conn, nodo_id)
        _check_worker(worker)

        try:
            driver_factory(worker).stop_vm(nodo_id)
        except Exception as e:
            raise HTTPException(502, f"No se pudo detener la VM {nodo_id} en {worker}: {e}")

        _set_state(nodo_id, "STOPPED")
        return {"status": "STOPPED", "nodo_id": nodo_id, "estado_nodo": "STOPPED", "worker": worker}


def delete_vm(nodo_id: str, worker_ip: Optional[str] = None) -> dict:
    # RUNNING/STOPPED/FAILED -> DELETED
    with db.node_lock(nodo_id):
        with db.tx() as conn:
            node = _load_node(conn, nodo_id)
            _require_state(node, "delete")
            worker = worker_ip or repo.find_worker_ip(conn, nodo_id)

        if not worker:
            if node["estado_nodo"] == "FAILED":
                _set_state(nodo_id, "DELETED")
                return {"status": "DELETED", "nodo_id": nodo_id, "estado_nodo": "DELETED", "cleaned": False}
            raise HTTPException(409, "El nodo no tiene worker asignado")
        _check_worker(worker)

        try:
            result = driver_factory(worker).destroy_and_smart_clean(nodo_id, _overlay_path(nodo_id))
        except Exception as e:
            raise HTTPException(500, f"Error al limpiar la VM {nodo_id} en {worker}: {e}")

        _set_state(nodo_id, "DELETED")
        return {"status": "DELETED", "nodo_id": nodo_id, "estado_nodo": "DELETED", "worker": worker, **result}



def get_vm(nodo_id: str, verify: bool = False) -> dict:
    #Estado de la VM
    with db.tx() as conn:
        node = _load_node(conn, nodo_id)
        worker = repo.find_worker_ip(conn, nodo_id)

    result = {
        "nodo_id": node["id"], "slice_id": node["slice_id"], "name": node["name"],
        "estado_nodo": node["estado_nodo"], "pid": node["pid"], "puerto_vnc": node["puerto_vnc"],
        "worker": worker,
        "flavor": {"vcpu": node["vcpu"], "vram_mb": node["vram_mb"], "vdisk_mb": node["vdisk_mb"]},
    }

    if verify and node["estado_nodo"] == "RUNNING" and worker in config.ALLOWED_WORKERS:
        try:
            st = driver_factory(worker).status(nodo_id)
        except Exception as e:
            result["verificacion"] = {"ok": False, "error": str(e)}
        else:
            if st["running"]:
                result["verificacion"] = {"ok": True, "running": True, "pid": st["pid"]}
                if st["pid"] != node["pid"]:
                    _set_state(nodo_id, "RUNNING", st["pid"], node["puerto_vnc"])
                    result["pid"] = st["pid"]
            else:
                _set_state(nodo_id, "FAILED")
                result.update(estado_nodo="FAILED", pid=None, puerto_vnc=None)
                result["verificacion"] = {"ok": True, "running": False, "accion": "marcada FAILED"}
    return result


def list_vms(slice_id: Optional[int] = None, estado: Optional[str] = None) -> list:
    if estado is not None and estado not in VALID_STATES:
        raise HTTPException(400, f"estado inválido. Use uno de: {', '.join(sorted(VALID_STATES))}")
    with db.tx() as conn:
        return repo.list_nodes(conn, slice_id, estado)