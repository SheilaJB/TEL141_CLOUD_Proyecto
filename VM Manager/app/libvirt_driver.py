import libvirt
import json
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET


_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

class KVMRemoteDriver:
    def __init__(self, worker_ip: str, user: str = "ubuntu"):
        self.worker_ip = worker_ip
        self.user = user
        self.uri = f"qemu+ssh://{user}@{worker_ip}/system?no_verify=1"

    def _exec_remote_ssh(self, command: str, timeout: int = 600) -> str:
        # Ejecuta sudo en el worker
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
        # Escapa y ejecuta un script en shell
        return f"sh -c {shlex.quote(script)}"


    def prepare_qcow2_overlay(
        self,
        image_download_url: str,
        base_path: str,
        overlay_path: str,
        expected_sha256: str = None,
    ):
        base_q, overlay_q = shlex.quote(base_path), shlex.quote(overlay_path)
        url_q = shlex.quote(image_download_url)
 
        self._exec_remote_ssh(
            f"mkdir -p {shlex.quote(base_path.rsplit('/', 1)[0])} "
            f"{shlex.quote(overlay_path.rsplit('/', 1)[0])}"
        )
 
        # Descarga atómica por flock (si VMx y VMx llegan a la vez al mismo worker, la segunda espera y reutiliza la imagen)
        download_script = (
            f"test -s {base_q} || "
            f"(wget -qO {base_q}.tmp {url_q} && mv {base_q}.tmp {base_q})"
        )
        self._exec_remote_ssh(
            f"flock {shlex.quote(base_path + '.lock')} {self._sh(download_script)}",
            timeout=3600,
        )
 
        # Verificar integridad
        if expected_sha256:
            got = self._exec_remote_ssh(f"sha256sum {base_q} | cut -d' ' -f1")
            if got != expected_sha256:
                self._exec_remote_ssh(f"rm -f {base_q}")
                raise Exception("sha256 de la imagen base no coincide; se eliminó la copia corrupta")
 
        # Overlay limpio 
        self._exec_remote_ssh(f"rm -f {overlay_q}")
        self._exec_remote_ssh(
            f"qemu-img create -f qcow2 -b {base_q} -F qcow2 {overlay_q}"
        )
 
    @staticmethod
    def _build_xml(vm_name, memory_mb, vcpus, disk_path, interfaces, seed_iso) -> str:
        nics = ""
        for nic in interfaces or []:
            bridge = nic["bridge"]
            if not _SAFE_NAME.match(bridge):
                raise ValueError(f"Nombre de bridge inválido: {bridge}")
            vlan = f"<vlan><tag id='{int(nic['vlan'])}'/></vlan>" if nic.get("vlan") else ""
            nics += f"""
    <interface type='bridge'>
      <source bridge='{bridge}'/>
      <virtualport type='openvswitch'/>
      {vlan}
      <model type='virtio'/>
    </interface>"""
 
        seed = ""
        if seed_iso:
            seed = f"""
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{seed_iso}'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>"""
 
        return f"""
<domain type='kvm'>
  <name>{vm_name}</name>
  <memory unit='KiB'>{memory_mb * 1024}</memory>
  <vcpu>{vcpus}</vcpu>
  <os>
    <type arch='x86_64'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features><acpi/><apic/></features>
  <cpu mode='host-model'/>
  <clock offset='utc'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='{disk_path}'/>
      <target dev='vda' bus='virtio'/>
    </disk>{seed}{nics}
    <video><model type='vga'/></video>
    <graphics type='vnc' port='-1' autoport='yes' listen='0.0.0.0'/>
  </devices>
</domain>"""


    def define_and_start_vm(
        self,
        vm_name: str,
        memory_mb: int,
        vcpus: int,
        disk_path: str,
        interfaces: list = None,
        seed_iso: str = None,
    ) -> dict:
        if not _SAFE_NAME.match(vm_name):
            raise ValueError(f"Nombre de VM inválido: {vm_name}")
 
        conn = libvirt.open(self.uri)
        if not conn:
            raise Exception(f"No se pudo conectar a KVM en {self.worker_ip}")
 
        try:
            xml_config = self._build_xml(vm_name, memory_mb, vcpus, disk_path, interfaces, seed_iso)
            dom = conn.createXML(xml_config, 0)
            time.sleep(1)
 
            # PID real del proceso QEMU
            pid_str = self._exec_remote_ssh(f"cat /run/libvirt/qemu/{vm_name}.pid")
            pid = int(pid_str)
 
            # Puerto VNC asignado por libvirt
            vnc_port = None
            root = ET.fromstring(dom.XMLDesc(0))
            gfx = root.find("./devices/graphics[@type='vnc']")
            if gfx is not None and gfx.get("port") not in (None, "-1"):
                vnc_port = int(gfx.get("port"))
 
            return {"vm_id": vm_name, "worker": self.worker_ip, "pid": pid, "vnc_port": vnc_port}
        finally:
            conn.close()


    def destroy_and_smart_clean(self, vm_name: str, overlay_path: str):
        try:
            conn = libvirt.open(self.uri)
            if conn:
                try:
                    dom = conn.lookupByName(vm_name)
                    dom.destroy()
                except libvirt.libvirtError:
                    pass
                conn.close()
        except Exception:
            pass

        rm_cmd = f"rm -f {overlay_path}"
        self._exec_remote_ssh(rm_cmd)


    def _destroy_domain(self, vm_name: str):
        try:
            conn = libvirt.open(self.uri)
            if conn:
                try:
                    dom = conn.lookupByName(vm_name)
                    dom.destroy()
                except libvirt.libvirtError:
                    pass
                finally:
                    conn.close()
        except Exception:
            pass
 
    def cleanup_partial(self, vm_name: str, overlay_path: str):
        # Se llama entre reintentos: deja el worker sin restos de la VM fallida
        self._destroy_domain(vm_name)
        try:
            self._exec_remote_ssh(f"rm -f {shlex.quote(overlay_path)}")
        except Exception:
            pass
 
    def _get_backing(self, overlay_path: str):
        try:
            out = self._exec_remote_ssh(
                f"qemu-img info --output=json {shlex.quote(overlay_path)}"
            )
            return json.loads(out).get("backing-filename")
        except Exception:
            return None
 
    def destroy_and_smart_clean(self, vm_name: str, overlay_path: str) -> dict:
        # Apaga la VM, borra su overlay 
        base_path = self._get_backing(overlay_path)
 
        self._destroy_domain(vm_name)
        self._exec_remote_ssh(f"rm -f {shlex.quote(overlay_path)}")
 
        removed_base = False
        if base_path:
            overlay_dir = overlay_path.rsplit("/", 1)[0]
            # El backing file queda escrito en texto plano en la cabecera del qcow2,
            users = self._exec_remote_ssh(
                self._sh(
                    f"grep -rl -a -F {shlex.quote(base_path)} {shlex.quote(overlay_dir)} || true"
                )
            )
            if not users.strip():
                self._exec_remote_ssh(f"rm -f {shlex.quote(base_path)} {shlex.quote(base_path + '.lock')}")
                removed_base = True
 
        return {"cleaned_disk": overlay_path, "base_image": base_path, "base_removed": removed_base}
