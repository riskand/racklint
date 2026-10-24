# racklint/src/racklint/runner.py
"""Run a plan: check every device with the reader and the checks of its platform."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from racklint.checks import Check
from racklint.credentials import CredentialSource
from racklint.engine import ActualReader, Planned, check_nodes, error_result
from racklint.errors import RacklintError
from racklint.gnmi import GnmiAccess
from racklint.models import ExpectedNode, ExpectedSwitch, NodeResult, Report
from racklint.platforms import (
    DEFAULT_SERVER,
    DEFAULT_SWITCH,
    Platforms,
    UnknownPlatform,
)
from racklint.redfish import BmcAccess

# A device and its place in the plan.
type Placed[E] = tuple[int, E]


async def check_plan(
    nodes: Sequence[ExpectedNode | ExpectedSwitch],
    platforms: Platforms,
    credentials: CredentialSource,
    bmc: BmcAccess | None,
    gnmi: GnmiAccess | None,
    concurrency: int,
) -> Report:
    """Check every device with its platform's reader and checks. Results keep the order of `nodes`.

    `bmc` and `gnmi` are the access settings for the servers and the switches; the caller gives those of
    every kind it plans. A device whose platform is unknown is that device's error.
    """
    results: dict[int, NodeResult] = {}
    # Groups are keyed by platform name, so a platform need not be hashable.
    server_groups: defaultdict[str, list[Placed[ExpectedNode]]] = defaultdict(list)
    switch_groups: defaultdict[tuple[str, int], list[Placed[ExpectedSwitch]]] = defaultdict(list)
    for place, node in enumerate(nodes):
        try:
            if isinstance(node, ExpectedNode):
                server = platforms.server(node.platform or DEFAULT_SERVER)
                server_groups[server.name].append((place, node))
            else:
                switch = platforms.switch(node.platform or DEFAULT_SWITCH)
                port = node.gnmi_port or switch.gnmi_port or _required(gnmi, "switches").port
                switch_groups[switch.name, port].append((place, node))
        except UnknownPlatform as error:
            results[place] = error_result(node, str(error))
    # Every reader is built before anything is read, so a missing setting fails fast.
    server_readers = {
        name: platforms.server(name).reader(_required(bmc, "servers")) for name in server_groups
    }
    switch_readers = {
        (name, port): platforms.switch(name).reader(replace(_required(gnmi, "switches"), port=port))
        for name, port in switch_groups
    }
    for name, servers in server_groups.items():
        results |= await _check_group(
            servers, server_readers[name], platforms.server(name).checks(), credentials, concurrency
        )
    for (name, port), switches in switch_groups.items():
        results |= await _check_group(
            switches, switch_readers[name, port], platforms.switch(name).checks(), credentials, concurrency
        )
    return Report(results=tuple(results[place] for place in range(len(nodes))))


async def _check_group[E: Planned, A](
    placed: list[Placed[E]],
    reader: ActualReader[A],
    checks: Sequence[Check[E, A]],
    credentials: CredentialSource,
    concurrency: int,
) -> dict[int, NodeResult]:
    """Check devices that share a reader. Each result goes to its device's place in the plan."""
    report = await check_nodes([node for _, node in placed], reader, checks, credentials, concurrency)
    return {place: result for (place, _), result in zip(placed, report.results, strict=True)}


def _required[A: (BmcAccess, GnmiAccess)](access: A | None, devices: str) -> A:
    """The access settings for a kind of device in the plan."""
    if access is None:
        raise RacklintError(f"the plan has {devices}, but check_plan was given no access settings for them")
    return access
