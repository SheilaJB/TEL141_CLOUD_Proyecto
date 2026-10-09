"""
qemu_driver.py — Adaptador Linux: QEMU/KVM + Open vSwitch en los workers, por SSH.

Es la ÚNICA pieza que toca la infraestructura. No guarda estado: ejecuta y devuelve.

  - Cada VM = un proceso `qemu-system-x86_64 -daemonize -pidfile /run/vms/<vm>.pid`.
  - PID: se lee del pidfile (es el PID real de QEMU, el que pide la demo).
  - Red: un tap por interfaz, conectado a un bridge OVS (con tag de VLAN opcional).
  - VNC: `-vnc 0.0.0.0:0,to=99` toma el primer display libre; el puerto real se lee con `ss`.
  - Estado por VM en /run/vms/<vm>.pid y /run/vms/<vm>.taps (para limpiar sin adivinar).
  - Descarga de imagen base atómica (.tmp + mv) y serializada con flock.
  - Borrado inteligente: overlay + imagen base si ninguna otra VM la usa.

Operaciones: prepare_qcow2_overlay, define_and_start_vm, stop_vm, start_vm, status,
             cleanup_partial, destroy_and_smart_clean.

Requisitos en cada worker: qemu-system-x86, qemu-utils, openvswitch-switch, wget, iproute2,
bridge OVS ya creado, /dev/kvm, usuario con sudo sin password y SSH por llave desde el orquestador.
"""
import hashlib
import json
import re
import shlex
import subprocess
import time

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_MAC = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")

# Mata el proceso del pidfile $P (SIGTERM, espera hasta 5 s, luego SIGKILL)
_KILL_SNIPPET = """
if [ -f "$P" ]; then
  PID=$(cat "$P")
  kill "$PID" 2>/dev/null
  for i in 1 2 3 4 5; do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
fi
"""


class NonRetryableError(Exception):
    """Error que no se arregla reintentando (p. ej. flavor con disco menor que la imagen)."""


class QEMURemoteDriver:
    def __init__(self, worker_ip: str, user: str = "ubuntu", run_dir: str = "/run/vms"):
        self.worker_ip = worker_ip
        self.user = user
        self.run_dir = run_dir

    # ------------------------------------------------------------------ SSH
    def _exec_remote_ssh(self, command: str, timeout: int = 600) -> str:
        """Ejecuta `sudo <command>` en el worker. El comando lo interpreta el shell REMOTO."""
        res = subprocess.run(
            [
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10",
                f"{self.user}@{self.worker_ip}",
                f"sudo {command}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if res.returncode != 0:
            raise Exception(f"SSH Error en {self.worker_ip}: {res.stderr.strip()}")
        return res.stdout.strip()

    @staticmethod
    def _sh(script: str) -> str:
        """Envuelve un script para que pipes, ||, ; se ejecuten en el worker."""
        return f"sh -c {shlex.quote(script)}"

    @staticmethod
    def _check_name(value, what: str = "nombre"):
        if not _SAFE_NAME.match(str(value)):
            raise ValueError(f"{what} inválido: {value}")

    @staticmethod
    def _tap_name(vm_name: str, index: int) -> str:
        # Linux limita los nombres de interfaz a 15 caracteres
        return f"tap{hashlib.sha1(vm_name.encode()).hexdigest()[:6]}{index}"

    @staticmethod
    def _mac(vm_name: str, index: int) -> str:
        h = hashlib.sha1(f"{vm_name}-{index}".encode()).hexdigest()
        return f"52:54:00:{h[0:2]}:{h[2:4]}:{h[4:6]}"

    # ------------------------------------------------------ Imagen / overlay
    def prepare_qcow2_overlay(
        self,
        image_download_url: str,
        base_path: str,
        overlay_path: str,
        expected_sha256: str = None,
        disk_mb: int = None,
    ):
        base_q, overlay_q = shlex.quote(base_path), shlex.quote(overlay_path)
        url_q = shlex.quote(image_download_url)

        self._exec_remote_ssh(
            f"mkdir -p {shlex.quote(base_path.rsplit('/', 1)[0])} "
            f"{shlex.quote(overlay_path.rsplit('/', 1)[0])} {shlex.quote(self.run_dir)}"
        )

        # Descarga atómica y serializada: si dos VMs llegan a la vez al mismo worker,
        # la segunda espera y reutiliza la imagen ya descargada.
        download_script = (
            f"test -s {base_q} || "
            f"(wget -qO {base_q}.tmp {url_q} && mv {base_q}.tmp {base_q})"
        )
        self._exec_remote_ssh(
            f"flock {shlex.quote(base_path + '.lock')} {self._sh(download_script)}",
            timeout=3600,
        )

        if expected_sha256:
            got = self._exec_remote_ssh(self._sh(f"sha256sum {base_q} | cut -d' ' -f1"))
            if got != expected_sha256:
                self._exec_remote_ssh(f"rm -f {base_q}")
                raise Exception("sha256 de la imagen base no coincide; se eliminó la copia corrupta")

        # Formato y tamaño virtual de la imagen base (paso 10 de la demo: qemu-img info)
        info = json.loads(self._exec_remote_ssh(f"qemu-img info -U --output=json {base_q}"))
        if info.get("format") != "qcow2":
            raise NonRetryableError(f"La imagen base no es qcow2 (es {info.get('format')})")
        if disk_mb and int(disk_mb) * 1024 * 1024 < int(info["virtual-size"]):
            raise NonRetryableError(
                f"El disco del flavor ({disk_mb} MB) es menor que el tamaño virtual de la imagen "
                f"({int(info['virtual-size']) // (1024 * 1024)} MB)"
            )

        # Overlay limpio (si quedó uno de un intento anterior, se recrea)
        size = f" {int(disk_mb)}M" if disk_mb else ""
        self._exec_remote_ssh(f"rm -f {overlay_q}")
        self._exec_remote_ssh(
            f"qemu-img create -f qcow2 -b {base_q} -F qcow2 {overlay_q}{size}"
        )

    # ----------------------------------------------------------- Arranque VM
    def define_and_start_vm(
        self,
        vm_name: str,
        memory_mb: int,
        vcpus: int,
        disk_path: str,
        interfaces: list = None,
        seed_iso: str = None,
    ) -> dict:
        """
        interfaces: [{"bridge": "br-int", "vlan": 100 | None, "mac": "52:54:00:.." | None}, ...]
        Devuelve {"vm_id", "worker", "pid", "vnc_port"}.
        """
        self._check_name(vm_name, "vm_id")
        if self.status(vm_name)["running"]:
            raise NonRetryableError(f"La VM {vm_name} ya está en ejecución en {self.worker_ip}")

        pidfile = f"{self.run_dir}/{vm_name}.pid"
        tapsfile = f"{self.run_dir}/{vm_name}.taps"
        self._exec_remote_ssh(f"mkdir -p {shlex.quote(self.run_dir)}")

        # 1) Registrar los taps ANTES de crearlos, para poder limpiarlos aunque falle algo después
        nics = interfaces or []
        taps = [self._tap_name(vm_name, i) for i in range(len(nics))]
        if taps:
            self._exec_remote_ssh(self._sh(f"echo {' '.join(taps)} > {shlex.quote(tapsfile)}"))

        # 2) Crear cada tap y conectarlo al bridge OVS (con VLAN si corresponde)
        net_args = []
        for i, nic in enumerate(nics):
            bridge = nic["bridge"]
            self._check_name(bridge, "bridge")
            mac = nic.get("mac") or self._mac(vm_name, i)
            if not _MAC.match(mac):
                raise ValueError(f"MAC inválida: {mac}")
            tap = taps[i]
            tag = f" tag={int(nic['vlan'])}" if nic.get("vlan") else ""
            self._exec_remote_ssh(
                self._sh(
                    f"ip link show {tap} >/dev/null 2>&1 || ip tuntap add dev {tap} mode tap; "
                    f"ip link set {tap} up; "
                    f"ovs-vsctl --may-exist add-port {bridge} {tap}{tag}"
                )
            )
            net_args += [
                "-netdev", f"tap,id=n{i},ifname={tap},script=no,downscript=no",
                "-device", f"virtio-net-pci,netdev=n{i},mac={mac}",
            ]

        # 3) Lanzar QEMU en segundo plano
        args = [
            "qemu-system-x86_64",
            "-name", vm_name,
            "-enable-kvm", "-cpu", "host",
            "-m", str(int(memory_mb)),
            "-smp", str(int(vcpus)),
            "-drive", f"file={disk_path},format=qcow2,if=virtio",
        ]
        if seed_iso:
            args += ["-drive", f"file={seed_iso},format=raw,media=cdrom,readonly=on"]
        args += net_args
        args += [
            "-vga", "std",
            "-vnc", "0.0.0.0:0,to=99",
            "-daemonize",
            "-pidfile", pidfile,
        ]
        self._exec_remote_ssh(" ".join(shlex.quote(a) for a in args))

        # 4) PID real de QEMU + comprobación de que sigue vivo
        pid = int(self._exec_remote_ssh(f"cat {shlex.quote(pidfile)}"))
        self._exec_remote_ssh(f"kill -0 {pid}")

        # 5) Puerto VNC que tomó este proceso
        vnc_port = None
        for _ in range(6):
            out = self._exec_remote_ssh(self._sh(f"ss -ltnp | grep 'pid={pid},' || true"))
            m = re.search(r":(59\d{2})\b", out)
            if m:
                vnc_port = int(m.group(1))
                break
            time.sleep(0.5)

        return {"vm_id": vm_name, "worker": self.worker_ip, "pid": pid, "vnc_port": vnc_port}

    def start_vm(
        self,
        vm_name: str,
        memory_mb: int,
        vcpus: int,
        disk_path: str,
        interfaces: list = None,
        seed_iso: str = None,
    ) -> dict:
        """Reanuda una VM detenida: relanza QEMU con el overlay existente (sin descargar nada)."""
        try:
            self._exec_remote_ssh(f"test -s {shlex.quote(disk_path)}")
        except Exception:
            raise NonRetryableError(f"El disco {disk_path} no existe en {self.worker_ip}")
        return self.define_and_start_vm(vm_name, memory_mb, vcpus, disk_path, interfaces, seed_iso)

    # ----------------------------------------------------------- Estado / stop
    def status(self, vm_name: str) -> dict:
        """Una sola consulta: ¿hay un proceso QEMU vivo para esta VM? -> {"running", "pid"}."""
        self._check_name(vm_name, "vm_id")
        p = shlex.quote(f"{self.run_dir}/{vm_name}.pid")
        out = self._exec_remote_ssh(
            self._sh(
                f'P={p}; if [ -f "$P" ] && kill -0 "$(cat "$P")" 2>/dev/null; '
                f'then echo "running $(cat "$P")"; else echo stopped; fi'
            )
        )
        parts = out.split()
        running = bool(parts) and parts[0] == "running"
        return {"running": running, "pid": int(parts[1]) if running and len(parts) > 1 else None}

    def stop_vm(self, vm_name: str) -> dict:
        """Detiene el proceso QEMU. CONSERVA el overlay y los taps para poder hacer start."""
        self._check_name(vm_name, "vm_id")
        p = shlex.quote(f"{self.run_dir}/{vm_name}.pid")
        script = f"P={p}\n{_KILL_SNIPPET}\nrm -f \"$P\"\ntrue\n"
        self._exec_remote_ssh(self._sh(script), timeout=60)
        return {"vm_id": vm_name, "worker": self.worker_ip, "stopped": True}

    # --------------------------------------------------------------- Limpieza
    def _teardown_runtime(self, vm_name: str):
        """Mata el proceso QEMU (si existe) y quita sus taps del OVS. Idempotente."""
        self._check_name(vm_name, "vm_id")
        p = shlex.quote(f"{self.run_dir}/{vm_name}.pid")
        t = shlex.quote(f"{self.run_dir}/{vm_name}.taps")
        script = f"""
P={p}; T={t}
{_KILL_SNIPPET}
if [ -f "$T" ]; then
  for t in $(cat "$T"); do
    ovs-vsctl --if-exists del-port "$t"
    ip link del "$t" 2>/dev/null
  done
fi
rm -f "$P" "$T"
true
"""
        self._exec_remote_ssh(self._sh(script), timeout=60)

    def _remove_base_if_unused(self, base_path: str, overlay_dir: str) -> bool:
        """Borra la imagen base del worker si ningún overlay la usa. Devuelve True si la borró."""
        # El backing file queda en texto plano en la cabecera del qcow2: grep detecta
        # si algún otro overlay sigue apuntando a esta base.
        users = self._exec_remote_ssh(
            self._sh(f"grep -rl -a -F {shlex.quote(base_path)} {shlex.quote(overlay_dir)} || true")
        )
        if users.strip():
            return False
        self._exec_remote_ssh(f"rm -f {shlex.quote(base_path)} {shlex.quote(base_path + '.lock')}")
        return True

    def cleanup_partial(self, vm_name: str, overlay_path: str, base_path: str = None):
        """Entre reintentos / tras un fallo: deja el worker sin restos de la VM (y sin base huérfana)."""
        try:
            self._teardown_runtime(vm_name)
        except Exception:
            pass
        try:
            self._exec_remote_ssh(f"rm -f {shlex.quote(overlay_path)}")
            if base_path:
                self._remove_base_if_unused(base_path, overlay_path.rsplit("/", 1)[0])
        except Exception:
            pass

    def _get_backing(self, overlay_path: str):
        try:
            # -U (--force-share): permite leer la imagen aunque la VM siga corriendo
            out = self._exec_remote_ssh(
                f"qemu-img info -U --output=json {shlex.quote(overlay_path)}"
            )
            return json.loads(out).get("backing-filename")
        except Exception:
            return None

    def destroy_and_smart_clean(self, vm_name: str, overlay_path: str) -> dict:
        """Apaga la VM, borra su overlay y, si ninguna otra VM usa la imagen base, la borra también."""
        base_path = self._get_backing(overlay_path)

        self._teardown_runtime(vm_name)
        self._exec_remote_ssh(f"rm -f {shlex.quote(overlay_path)}")

        removed_base = False
        if base_path:
            removed_base = self._remove_base_if_unused(base_path, overlay_path.rsplit("/", 1)[0])

        return {"cleaned_disk": overlay_path, "base_image": base_path, "base_removed": removed_base}
