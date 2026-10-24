# racklint/examples/no-hardware/node_files.py
"""The example's nodes, read from nodes/*.toml, as NetBox plans them or as their mock BMC reports them."""

import tomllib
from pathlib import Path
from typing import Any

from racklint import FirmwareRule, GpuSpec, MemorySpec, PlanContext

NODES_DIR = Path(__file__).parent / "nodes"
DEFAULTS = "defaults"

Node = dict[str, Any]


def node_names() -> list[str]:
    return sorted(path.stem for path in NODES_DIR.glob("*.toml") if path.stem != DEFAULTS)


def defaults() -> Node:
    return _read(DEFAULTS)


def planned(name: str) -> Node:
    """The node as NetBox plans it: the defaults, overridden by the node's own file."""
    node = _read(name)
    node.pop("actual", None)
    return _merge(defaults(), node) | {"name": name}


def actual(name: str) -> Node:
    """The node as its mock BMC reports it: the plan, overridden by the node's [actual] table."""
    return _merge(planned(name), _read(name).get("actual", {}))


def device_context(node: Node) -> Node | None:
    """The device's own config context: the credential set and the platform its node file names, if any."""
    names = {"credentials": node.get("credential_set"), "platform": node.get("platform")}
    context = {key: name for key, name in names.items() if name}
    return {"racklint": context} if context else None


def plan_context(node: Node, firmware: tuple[FirmwareRule, ...]) -> PlanContext:
    """The `racklint` config context NetBox holds for the node's device type."""
    return PlanContext(
        gpu=GpuSpec(count=node["gpu_count"], model=node["gpu_model"]),
        memory=MemorySpec(
            dimm_count=node["dimm_count"],
            total_gib=node["dimm_count"] * node["dimm_capacity_mib"] // 1024,
        ),
        firmware=firmware,
    )


def _read(name: str) -> Node:
    with (NODES_DIR / f"{name}.toml").open("rb") as file:
        return tomllib.load(file)


def _merge(base: Node, overrides: Node) -> Node:
    """Override `base` key by key. A table, such as firmware, is merged one level deep."""
    merged = dict(base)
    for key, value in overrides.items():
        merged[key] = (base[key] | value) if isinstance(value, dict) and key in base else value
    return merged
