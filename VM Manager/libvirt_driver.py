import libvirt
import subprocess
import time


class KVMRemoteDriver:
    def __init__(self, worker_ip: str, user: str = "ubuntu"):
        self.worker_ip = worker_ip
        self.user = user
        self.uri = f"qemu+ssh://{user}@{worker_ip}/system"

    def _exec_remote_ssh(self, command: str) -> str:
        # Ejecuta un comando bash remoto por SSH en el Worker, con sudo   
        ssh_cmd = (
            f"ssh -o StrictHostKeyChecking=no {self.user}@{self.worker_ip} "
            f"'sudo {command}'"
        )
        res = subprocess.run(ssh_cmd, shell=True, capture_output=True, text=True)
        if res.returncode != 0:
            raise Exception(f"SSH Error en {self.worker_ip}: {res.stderr}")
        return res.stdout.strip()

    def prepare_qcow2_overlay(self, image_download_url: str, base_path: str, overlay_path: str):
        self._exec_remote_ssh(
            f"mkdir -p $(dirname {base_path}) $(dirname {overlay_path})"
        )

        check_cmd = f"test -s {base_path}"
        try:
            self._exec_remote_ssh(check_cmd)
        except Exception:
            download_cmd = f"wget -qO {base_path} {image_download_url}"
            self._exec_remote_ssh(download_cmd)

        qemu_cmd = (
            f"qemu-img create -f qcow2 "
            f"-b {base_path} -F qcow2 {overlay_path}"
        )
        self._exec_remote_ssh(qemu_cmd)

    def define_and_start_vm(self, vm_name: str, memory_mb: int, vcpus: int, disk_path: str) -> dict:
        conn = libvirt.open(self.uri)
        if not conn:
            raise Exception(f"No se pudo conectar a KVM en {self.worker_ip}")

        xml_config = f"""
        <domain type='kvm'>
          <name>{vm_name}</name>
          <memory unit='KiB'>{memory_mb * 1024}</memory>
          <vcpu>{vcpus}</vcpu>
          <os>
            <type arch='x86_64'>hvm</type>
            <boot dev='hd'/>
          </os>
          <devices>
            <disk type='file' device='disk'>
              <driver name='qemu' type='qcow2'/>
              <source file='{disk_path}'/>
              <target dev='vda' bus='virtio'/>
            </disk>
            <graphics type='vnc' port='-1' autoport='yes' listen='0.0.0.0'/>
          </devices>
        </domain>
        """
        try:
            dom = conn.createXML(xml_config, 0)
            time.sleep(1)

            pid_str = self._exec_remote_ssh(f"pgrep -f 'name {vm_name}' | head -n 1")
            pid = int(pid_str) if pid_str else -1

            conn.close()
            return {"vm_id": vm_name, "worker": self.worker_ip, "pid": pid}
        except Exception as e:
            conn.close()
            raise e

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