from pathlib import Path
from typing import Any

import yaml


def load_capabilities(directory: Path | None, cluster_name: str) -> dict[str, bool]:
    if directory is None:
        return {}
    path = directory / f"{cluster_name}.yaml"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as stream:
        document: Any = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError(f"capabilities file {path} must contain a YAML object")
    supports = document.get("supports", {})
    if not isinstance(supports, dict) or any(
        not isinstance(name, str) or not isinstance(value, bool)
        for name, value in supports.items()
    ):
        raise ValueError(f"capabilities file {path} must define boolean supports flags")
    return supports
