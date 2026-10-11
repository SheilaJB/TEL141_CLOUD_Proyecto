"""Safe, synchronous host command transport."""

import subprocess
from collections.abc import Sequence
from pathlib import Path

from . import config
from .errors import RetryableError


def run(host: str, command: Sequence[str], *, timeout: int | None = None) -> str:
    """Run a sudo command through SSH without shell=True or shell snippets."""
    args = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={config.SSH_TIMEOUT}",
    ]
    if config.SSH_KEY:
        args.extend(["-i", config.SSH_KEY])
    if config.KNOWN_HOSTS:
        args.extend(["-o", f"UserKnownHostsFile={config.KNOWN_HOSTS}"])
    args.append(f"{config.SSH_USER}@{host}")
    args.extend(["sudo", *command])
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
    args = [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={config.SSH_TIMEOUT}",
    ]
    if config.SSH_KEY:
        args.extend(["-i", config.SSH_KEY])
    if config.KNOWN_HOSTS:
        args.extend(["-o", f"UserKnownHostsFile={config.KNOWN_HOSTS}"])
    args.extend([str(local_path), f"{config.SSH_USER}@{host}:{remote_path}"])
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
