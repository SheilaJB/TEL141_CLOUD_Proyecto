"""Safe, synchronous host command transport."""

import subprocess
import shlex
from collections.abc import Sequence
from pathlib import Path

from . import config
from .errors import RetryableError


class Endpoint(str):
    """`address` threaded through the adapter; carries the gateway SSH port."""

    port: int

    def __new__(cls, address: str, port: int) -> "Endpoint":
        obj = super().__new__(cls, f"{address}:{port}")
        obj.port = port
        return obj


def endpoint(target) -> Endpoint:
    return Endpoint(target.management_ip, target.ssh_port)


def _port_of(host: str) -> int:
    port = getattr(host, "port", None)
    if port is None:
        raise RuntimeError("host address has no SSH port; build it with host.endpoint()")
    return port


def _ssh_options(host: str) -> list[str]:
    options = [
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={config.SSH_TIMEOUT}",
    ]
    if config.SSH_KEY:
        options.extend(["-i", config.SSH_KEY])
    if config.KNOWN_HOSTS:
        options.extend(["-o", f"UserKnownHostsFile={config.KNOWN_HOSTS}"])
    return options


def run(host: str, command: Sequence[str], *, timeout: int | None = None) -> str:
    """Run a sudo command through SSH without shell=True or shell snippets."""
    # `host` se ignora: siempre se conecta al gw (config.GATEWAY_IP) por el puerto del servidor
    args = ["ssh", "-p", str(_port_of(host)), *_ssh_options(host)]
    args.append(f"{config.SSH_USER}@{config.GATEWAY_IP}")
    args.append(shlex.join(["sudo", *command]))
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout or config.COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RetryableError("COMMAND_TIMEOUT") from exc
    except OSError as exc:
        raise RetryableError("SSH_UNREACHABLE") from exc
    if result.returncode != 0:
        if result.returncode == 255:
            raise RetryableError("SSH_UNREACHABLE", result.stderr.strip())
        raise RuntimeError(result.stderr.strip() or "host command failed")
    return result.stdout.strip()


def upload(host: str, local_path: Path, remote_path: str) -> None:
    # `host` se ignora: siempre se conecta al gw (config.GATEWAY_IP) por el puerto del servidor
    args = ["scp", "-P", str(_port_of(host)), *_ssh_options(host)]
    args.extend([str(local_path), f"{config.SSH_USER}@{config.GATEWAY_IP}:{remote_path}"])
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise RetryableError("COMMAND_TIMEOUT") from exc
    except OSError as exc:
        raise RetryableError("SSH_UNREACHABLE") from exc
    if result.returncode != 0:
        raise RetryableError("SSH_UNREACHABLE", result.stderr.strip())
