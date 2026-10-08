
import hashlib
import json
import re
import shlex
import subprocess
import time

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class QEMURemoteDriver:
    def __init__(self, worker_ip: str, user: str = "ubuntu", run_dir: str = "/run/vms"):
        self.worker_ip = worker_ip
        self.user = user
        self.run_dir = run_dir

    # Para el SSH
    def _exec_remote_ssh(self, command: str, timeout: int = 600) -> str:
        # Ejecuta `sudo en el worker
        res = subprocess.run(
            [
                "ssh",
                "-o", "StrictHostKeyChecking=no",
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
        return f"sh -c {shlex.quote(script)}"

    @staticmethod
    def _check_name(value: str, what: str = "nombre"):
        if not _SAFE_NAME.match(str(value)):
            raise ValueError(f"{what} inválido: {value}")

    @staticmethod
    def _tap_name(vm_name: str, index: int) -> str:
        
        return f"tap{hashlib.sha1(vm_name.encode()).hexdigest()[:6]}{index}"

    @staticmethod
    def _mac(vm_name: str, index: int) -> str:
        h = hashlib.sha1(f"{vm_name}-{index}".encode()).hexdigest()
        return f"52:54:00:{h[0:2]}:{h[2:4]}:{h[4:6]}"

    # Imagen
    def prepare_qcow2_overlay(
        self,
        image_download_url: str,
        base_path: str,
        overlay_path: str,
        expected_sha256: str = None,
        disk_gb: int = None,
    ):
        base_q, overlay_q = shlex.quote(base_path), shlex.quote(overlay_path)
        url_q = shlex.quote(image_download_url)

        self._exec_remote_ssh(
            f"mkdir -p {shlex.quote(base_path.rsplit('/', 1)[0])} "
            f"{shlex.quote(overlay_path.rsplit('/', 1)[0])} {shlex.quote(self.run_dir)}"
        )

        # Descarga serializada: si VMx y VMx llegan a la vez al mismo worker
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

        
        size = f" {int(disk_gb)}G" if disk_gb else ""
        self._exec_remote_ssh(f"rm -f {overlay_q}")
        self._exec_remote_ssh(
            f"qemu-img create -f qcow2 -b {base_q} -F qcow2 {overlay_q}{size}"
        )

    # Arranca en VM
    def define_and_start_vm(
        self,
        vm_name: str,
        memory_mb: int,
        vcpus: int,
        disk_path: str,
        interfaces: list = None,
        seed_iso: str = None,
    ) -> dict:
        self._check_name(vm_name, "vm_id")
        pidfile = f"{self.run_dir}/{vm_name}.pid"
        tapsfile = f"{self.run_dir}/{vm_name}.taps"

        self._exec_remote_ssh(f"mkdir -p {shlex.quote(self.run_dir)}")

        # Registrar los taps ANTES de crearlos
        nics = interfaces or []
        taps = [self._tap_name(vm_name, i) for i in range(len(nics))]
        if taps:
            self._exec_remote_ssh(self._sh(f"echo {' '.join(taps)} > {shlex.quote(tapsfile)}"))

        # Crear cada tap y conectarlo al bridge OVS
        net_args = []
        for i, nic in enumerate(nics):
            bridge = nic["bridge"]
            self._check_name(bridge, "bridge")
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
                "-device", f"virtio-net-pci,netdev=n{i},mac={self._mac(vm_name, i)}",
            ]

        # Lanzar QEMU en segundo plano
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

        # PID real de QEMU 
        pid = int(self._exec_remote_ssh(f"cat {shlex.quote(pidfile)}"))
        self._exec_remote_ssh(f"kill -0 {pid}")

        # Puerto VNC 
        vnc_port = None
        for _ in range(6):
            out = self._exec_remote_ssh(
                self._sh(f"ss -ltnp | grep 'pid={pid},' || true")
            )
            m = re.search(r":(59\d{2})\b", out)
            if m:
                vnc_port = int(m.group(1))
                break
            time.sleep(0.5)

        return {"vm_id": vm_name, "worker": self.worker_ip, "pid": pid, "vnc_port": vnc_port}

    
    def _teardown_runtime(self, vm_name: str):
        # Mata el proceso QEMU y quita sus taps del OVS
        self._check_name(vm_name, "vm_id")
        p = shlex.quote(f"{self.run_dir}/{vm_name}.pid")
        t = shlex.quote(f"{self.run_dir}/{vm_name}.taps")
        script = f"""
P={p}; T={t}
if [ -f "$P" ]; then
  PID=$(cat "$P")
  kill "$PID" 2>/dev/null
  for i in 1 2 3 4 5; do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
fi
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

    def cleanup_partial(self, vm_name: str, overlay_path: str):
        # Se llama entre reintentos
        try:
            self._teardown_runtime(vm_name)
        except Exception:
            pass
        try:
            self._exec_remote_ssh(f"rm -f {shlex.quote(overlay_path)}")
        except Exception:
            pass

    def _get_backing(self, overlay_path: str):
        try:
            # Lee la imagen aunque la VM siga corriendo
            out = self._exec_remote_ssh(
                f"qemu-img info -U --output=json {shlex.quote(overlay_path)}"
            )
            return json.loads(out).get("backing-filename")
        except Exception:
            return None

    def destroy_and_smart_clean(self, vm_name: str, overlay_path: str) -> dict:
        # Apaga la VM
        base_path = self._get_backing(overlay_path)

        self._teardown_runtime(vm_name)
        self._exec_remote_ssh(f"rm -f {shlex.quote(overlay_path)}")

        removed_base = False
        if base_path:
            overlay_dir = overlay_path.rsplit("/", 1)[0]
            
            users = self._exec_remote_ssh(
                self._sh(
                    f"grep -rl -a -F {shlex.quote(base_path)} {shlex.quote(overlay_dir)} || true"
                )
            )
            if not users.strip():
                self._exec_remote_ssh(
                    f"rm -f {shlex.quote(base_path)} {shlex.quote(base_path + '.lock')}"
                )
                removed_base = True

        return {"cleaned_disk": overlay_path, "base_image": base_path, "base_removed": removed_base}