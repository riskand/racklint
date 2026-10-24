# racklint/src/racklint/engine.py
"""Check every device: read it, run the checks on what it reports, and collect the results."""

import asyncio
from collections.abc import AsyncIterator, Coroutine, Sequence
from typing import Any, Protocol

from racklint.checks import Check, Notes, not_planned
from racklint.credentials import CredentialError, Credentials, CredentialSource, Identified
from racklint.errors import AuthenticationError, InventoryError
from racklint.models import Finding, Kind, NodeResult, Report, Status


class Planned(Identified, Protocol):
    """What the engine needs from a planned device of any kind."""

    @property
    def address(self) -> str | None: ...


# The check name and missing-plan item reported when a device has no address to read it at.
_ADDRESS = {Kind.SERVER: ("bmc", "BMC address"), Kind.SWITCH: ("management", "management address")}


class ActualReader[A](Protocol):
    async def read(self, address: str, credentials: Credentials) -> A:
        """Return what the device reports, or raise InventoryError."""
        ...


async def check_nodes[E: Planned, A](
    nodes: Sequence[E],
    reader: ActualReader[A],
    checks: Sequence[Check[E, A]],
    credentials: CredentialSource,
    concurrency: int,
) -> Report:
    """Check the devices with at most `concurrency` read at once. Results keep the order of `nodes`."""
    results = await asyncio.gather(*_bounded(nodes, reader, checks, credentials, concurrency))
    return Report(results=tuple(results))


async def iter_results[E: Planned, A](
    nodes: Sequence[E],
    reader: ActualReader[A],
    checks: Sequence[Check[E, A]],
    credentials: CredentialSource,
    concurrency: int,
) -> AsyncIterator[NodeResult]:
    """Like check_nodes, but yield each device's result as soon as it is ready, for live progress."""
    for finished in asyncio.as_completed(_bounded(nodes, reader, checks, credentials, concurrency)):
        yield await finished


def _bounded[E: Planned, A](
    nodes: Sequence[E],
    reader: ActualReader[A],
    checks: Sequence[Check[E, A]],
    credentials: CredentialSource,
    concurrency: int,
) -> list[Coroutine[Any, Any, NodeResult]]:
    """One check per device, sharing a limit of `concurrency` reads at a time."""
    slots = asyncio.Semaphore(concurrency)

    async def check_in_slot(node: E) -> NodeResult:
        async with slots:
            return await check_node(node, reader, checks, credentials)

    return [check_in_slot(node) for node in nodes]


async def check_node[E: Planned, A](
    node: E, reader: ActualReader[A], checks: Sequence[Check[E, A]], credentials: CredentialSource
) -> NodeResult:
    if node.address is None:
        check, item = _ADDRESS[node.kind]
        missing = Finding(check=check, message=not_planned(item))
        return NodeResult(
            node=node.name, kind=node.kind, address=None, status=Status.FAIL, findings=(missing,)
        )
    try:
        login = await credentials.credentials_for(node)
    except CredentialError as error:
        return error_result(node, str(error))
    try:
        actual = await reader.read(node.address, login)
    except AuthenticationError as error:
        return error_result(node, _refused(error, login))
    except InventoryError as error:
        return error_result(node, str(error))
    findings = tuple(
        Finding(check=check.name, message=message) for check in checks for message in check.run(node, actual)
    )
    notes = tuple(
        Finding(check=check.name, message=message)
        for check in checks
        if isinstance(check, Notes)
        for message in check.notes(node, actual)
    )
    status = Status.FAIL if findings else Status.PASS
    return NodeResult(
        node=node.name, kind=node.kind, address=node.address, status=status, findings=findings, notes=notes
    )


def _refused(error: AuthenticationError, login: Credentials) -> str:
    """Name the credentials that were refused, and what to do about a stale factory secret."""
    message = f"{error} with the {login.origin}"
    if login.factory:
        message += "; remove the factory secret once the device's password is rotated"
    return message


def error_result(node: Planned, message: str) -> NodeResult:
    """The device could not be checked: its result is an error, with the reason."""
    return NodeResult(
        node=node.name, kind=node.kind, address=node.address, status=Status.ERROR, error=message
    )
