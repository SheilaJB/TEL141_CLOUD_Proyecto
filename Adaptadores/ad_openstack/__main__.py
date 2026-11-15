"""Run the OpenStack adapter worker, or `python -m ad_openstack check`."""

import sys

from . import client, config
from .worker import main

if __name__ == "__main__":
    if sys.argv[1:] == ["check"]:
        # Prueba de conectividad: túnel + token admin + Nova/Neutron/Glance.
        config.require()
        try:
            for line in client.check():
                print(line)
        finally:
            client.close()
    else:
        main()
