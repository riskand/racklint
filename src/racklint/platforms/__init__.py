# racklint/src/racklint/platforms/__init__.py
"""Platforms: one per device family, saying how to read its devices and which checks judge them.

A platform comes from racklint or from any installed package, through the `racklint.platforms` entry point
group. A device names its platform with `racklint.platform` in its NetBox config context.
"""

from collections.abc import Iterable, Sequence
from importlib import metadata
from typing import Literal, Protocol

from racklint.checks import Check
from racklint.engine import ActualReader
from racklint.errors import RacklintError
from racklint.gnmi import GnmiAccess
from racklint.models import ActualNode, ActualSwitch, ExpectedNode, ExpectedSwitch, Kind
from racklint.redfish import BmcAccess

ENTRY_POINTS = "racklint.platforms"
DEFAULT_SERVER = "redfish"
DEFAULT_SWITCH = "openconfig"


class ServerPlatform(Protocol):
    """One family of servers: how to read their BMCs, and which checks judge them."""

    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> Literal[Kind.SERVER]: ...

    def reader(self, access: BmcAccess) -> ActualReader[ActualNode]: ...

    def checks(self) -> Sequence[Check[ExpectedNode, ActualNode]]: ...


class SwitchPlatform(Protocol):
    """One family of switches: how to read them, and which checks judge them."""

    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> Literal[Kind.SWITCH]: ...

    @property
    def gnmi_port(self) -> int | None:
        """Its usual gNMI port; None for RACKLINT_GNMI_PORT."""
        ...

    def reader(self, access: GnmiAccess) -> ActualReader[ActualSwitch]: ...

    def checks(self) -> Sequence[Check[ExpectedSwitch, ActualSwitch]]: ...


class UnknownPlatform(RacklintError):
    """No installed platform has the name a device asks for."""


class Platforms:
    """The installed platforms, by name."""

    def __init__(self, platforms: Iterable[ServerPlatform | SwitchPlatform]) -> None:
        self._platforms = {platform.name: platform for platform in platforms}

    def server(self, name: str) -> ServerPlatform:
        platform = self._named(name)
        if platform.kind is not Kind.SERVER:
            raise _wrong_kind(platform, Kind.SERVER)
        return platform

    def switch(self, name: str) -> SwitchPlatform:
        platform = self._named(name)
        if platform.kind is not Kind.SWITCH:
            raise _wrong_kind(platform, Kind.SWITCH)
        return platform

    def _named(self, name: str) -> ServerPlatform | SwitchPlatform:
        try:
            return self._platforms[name]
        except KeyError:
            raise UnknownPlatform(
                f"no racklint platform {name!r}: install one, or remove racklint.platform"
            ) from None


def _wrong_kind(platform: ServerPlatform | SwitchPlatform, wanted: Kind) -> UnknownPlatform:
    return UnknownPlatform(
        f"racklint platform {platform.name!r} is a {platform.kind} platform, not a {wanted} platform: "
        "name another, or remove racklint.platform"
    )


def load_platforms(entry_points: Iterable[metadata.EntryPoint] | None = None) -> Platforms:
    """Every platform in the entry point group; `entry_points` replaces the installed ones in tests."""
    if entry_points is None:
        entry_points = metadata.entry_points(group=ENTRY_POINTS)
    claimed: dict[str, metadata.EntryPoint] = {}
    platforms: list[ServerPlatform | SwitchPlatform] = []
    for entry_point in entry_points:
        if (first := claimed.get(entry_point.name)) is not None:
            raise RacklintError(
                f"racklint platform {entry_point.name!r} is claimed twice: "
                f"{first.value} and {entry_point.value}"
            )
        claimed[entry_point.name] = entry_point
        platforms.append(_load(entry_point))
    return Platforms(platforms)


def _load(entry_point: metadata.EntryPoint) -> ServerPlatform | SwitchPlatform:
    """The platform the entry point names, which must be of one kind and have the entry point's name."""
    platform: ServerPlatform | SwitchPlatform = entry_point.load()
    where = f"{ENTRY_POINTS} entry point {entry_point.name!r} ({entry_point.value})"
    if not isinstance(getattr(platform, "kind", None), Kind):
        raise RacklintError(f"{where} is neither a server platform nor a switch platform")
    if (name := getattr(platform, "name", None)) != entry_point.name:
        raise RacklintError(f"{where} is a platform named {name!r}: the names must match")
    return platform
