import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "Adaptadores"), str(ROOT / "Slice Manager" / "contracts"), str(ROOT / "Slice Manager")]
os.environ.update(OS_TUNNEL="false", OS_COMPUTE_HOSTS="5:worker1,6:worker2,7:worker3")

from temporalio.exceptions import ApplicationError

from ad_openstack import vm
from ad_openstack.errors import NonRetryableError
from contracts.queues import LINUX_QUEUE, OPENSTACK_QUEUE
from temporal_worker.worker.workflows.common import adapter_queue


def create_vm_request(server_id):
    return SimpleNamespace(
        slice_id=2,
        server_id=server_id,
        action=SimpleNamespace(
            target=SimpleNamespace(id="7cb60474-cd67-516d-a86f-4588717debe3"),
            params={
                "image": {"reference": "cirros", "cloud_init": False},
                "flavor": {"vcpu": 1, "ram_mb": 256, "disk_mb": 1000},
                "nics": [],
            },
        ),
    )


def run_create_vm(server_id):
    sent = {}

    def fake_post(service, path, body, **kwargs):
        sent["body"] = body["server"]
        return SimpleNamespace(json=lambda: {"server": {"id": "srv-1"}})

    built = lambda project_id, server_id: {
        "id": server_id, "status": "ACTIVE", "OS-EXT-SRV-ATTR:hypervisor_hostname": sent["body"]["availability_zone"].split(":")[1],
    }
    with mock.patch.object(vm.resources, "ensure_project", return_value="project-slice-2"), \
            mock.patch.object(vm.resources, "find_server", return_value=None), \
            mock.patch.object(vm, "_image_id", return_value="image-cirros"), \
            mock.patch.object(vm, "_ensure_flavor", return_value="g3-1c-256m-1g"), \
            mock.patch.object(vm, "_wait_built", side_effect=built), \
            mock.patch.object(vm.client, "post", side_effect=fake_post):
        facts = vm.create_vm(create_vm_request(server_id))
    return sent.get("body"), facts


class CasosPruebaPlacement(unittest.TestCase):

    def test_caso_4_eleccion_de_zona_de_disponibilidad(self):
        body, facts = run_create_vm(6)
        print(f"\nCaso 4a -> server_id=6 availability_zone={body['availability_zone']} host={facts['compute_host']}")
        self.assertEqual(body["availability_zone"], "nova:worker2")
        self.assertEqual(facts["compute_host"], "worker2")

        body, _ = run_create_vm(5)
        print(f"Caso 4b -> server_id=5 availability_zone={body['availability_zone']}")
        self.assertEqual(body["availability_zone"], "nova:worker1")

        with self.assertRaises(NonRetryableError) as ctx:
            run_create_vm(1)
        print(f"Caso 4c -> server_id=1 (Linux) error={ctx.exception.code}")
        self.assertEqual(ctx.exception.code, "COMPUTE_HOST_UNKNOWN")

    def test_caso_5_seleccion_de_plataforma(self):
        casos = {"openstack": OPENSTACK_QUEUE, "linux": LINUX_QUEUE, " OpenStack ": OPENSTACK_QUEUE}
        print()
        for cluster, esperado in casos.items():
            cola = adapter_queue(cluster)
            print(f"Caso 5 -> cluster={cluster!r} cola={cola}")
            self.assertEqual(cola, esperado)

        with self.assertRaises(ApplicationError) as ctx:
            adapter_queue("vmware")
        print(f"Caso 5 -> cluster='vmware' error={ctx.exception.type} non_retryable={ctx.exception.non_retryable}")
        self.assertTrue(ctx.exception.non_retryable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
