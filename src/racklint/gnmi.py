# racklint/src/racklint/gnmi.py
"""Read a switch over gNMI: serial number, OS version and LLDP neighbours.

A GnmiDialect says which gNMI requests read a switch and how to parse the answers. OPENCONFIG, the default,
reads OpenConfig models.

The gNMI calls go through a GnmiClient. The default one uses pygnmi, installed with the optional `network`
extra and imported only when a switch is read, so `import racklint` never pulls in gRPC.
"""

import asyncio
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from racklint.credentials import Credentials
from racklint.errors import AuthenticationError, InventoryError, RacklintError
from racklint.models import ActualSwitch, FirmwareItem, LldpNeighbour
from racklint.retry import ATTEMPTS, BACKOFF_SECONDS, pause

# OpenConfig lets a device report its running software on any of these components; the first one found wins.
OS_COMPONENT_TYPES = ("OPERATING_SYSTEM", "CONTROLLER_CARD", "CHASSIS")
TIMEOUT_SECONDS = 15
# The gRPC status codes with which a switch refuses the credentials.
REFUSED = ("UNAUTHENTICATED", "PERMISSION_DENIED")
# The gRPC status code with which a switch says it has no data at a path.
NOT_FOUND = "NOT_FOUND"
ENCODING = "json_ietf"

Update = dict[str, Any]


class GnmiError(InventoryError):
    """The switch could not be read over gNMI."""


class GnmiAuthError(GnmiError, AuthenticationError):
    """The switch refused the credentials. Never retried: another try could lock the account."""


class GnmiNotFound(GnmiError):
    """The switch has no data at a requested path. Never retried: the answer will not change."""


@dataclass(frozen=True)
class GnmiRequest:
    """One gNMI path to Get, with the request target it needs; None for the switch's default data tree."""

    path: str
    target: str | None = None
    optional: bool = False  # a switch with no data at the path is not an error


COMPONENTS = GnmiRequest("/components")
LLDP = GnmiRequest("/lldp/interfaces")


class GnmiClient(Protocol):
    def get(
        self, address: str, requests: Sequence[GnmiRequest], credentials: Credentials
    ) -> dict[GnmiRequest, list[Update]]:
        """Blocking. The updates each request returned, as pygnmi shapes them.

        Raise GnmiError on failure, and GnmiNotFound if the switch has no data for a request that is not
        optional. An optional request with no data returns no updates.
        """
        ...


class GnmiDialect(Protocol):
    """Which gNMI requests read a switch, and how their answers become what the switch reports."""

    @property
    def requests(self) -> Sequence[GnmiRequest]: ...

    def parse(self, replies: Mapping[GnmiRequest, list[Update]]) -> ActualSwitch: ...


class OpenConfigDialect:
    """OpenConfig models: /components for the serial and the OS version, /lldp/interfaces for LLDP."""

    requests = (COMPONENTS, LLDP)

    def parse(self, replies: Mapping[GnmiRequest, list[Update]]) -> ActualSwitch:
        return parse_switch(replies[COMPONENTS], replies[LLDP])


OPENCONFIG = OpenConfigDialect()


@dataclass(frozen=True)
class GnmiAccess:
    """How to reach the switches: port and TLS settings for all of them. The credentials come per read."""

    port: int = 57400
    verify_tls: bool = True
    ca_bundle: Path | None = None
    # How to reach the switch; None for the network. The conformance kit passes recorded responses here.
    client: GnmiClient | None = None


class GnmiReader:
    """The engine's ActualReader for switches that speak gNMI. Its dialect says what to ask and parse."""

    def __init__(
        self,
        access: GnmiAccess,
        client: GnmiClient | None = None,
        backoff: float = BACKOFF_SECONDS,
        dialect: GnmiDialect = OPENCONFIG,
    ) -> None:
        self._client = client or access.client or PygnmiClient(access)
        self._backoff = backoff
        self._dialect = dialect

    async def read(self, address: str, credentials: Credentials) -> ActualSwitch:
        replies = await self._get_with_retries(address, credentials)
        try:
            return self._dialect.parse(replies)
        except (KeyError, TypeError, ValueError) as error:
            raise GnmiError(f"unexpected gNMI payload: {error!r}") from error

    async def _get_with_retries(
        self, address: str, credentials: Credentials
    ) -> dict[GnmiRequest, list[Update]]:
        # A switch that is still starting refuses connections or times out; that is worth another try. A
        # refused login is not, and neither is a path without data: both pass straight through.
        for attempt in range(ATTEMPTS - 1):
            try:
                return await self._get(address, credentials)
            except (GnmiAuthError, GnmiNotFound):
                raise
            except GnmiError:
                await pause(attempt, self._backoff)
        return await self._get(address, credentials)

    async def _get(self, address: str, credentials: Credentials) -> dict[GnmiRequest, list[Update]]:
        return await asyncio.to_thread(self._client.get, address, self._dialect.requests, credentials)


class PygnmiClient:
    """GnmiClient on pygnmi.

    Without a CA file, pygnmi trusts whatever certificate the switch presents, so verifying TLS needs a CA
    bundle; switching verification off is the explicit way to accept any certificate.
    """

    def __init__(self, access: GnmiAccess) -> None:
        try:
            import pygnmi.client  # noqa: F401 - the optional dependency; fail early if it is missing
        except ImportError as error:
            raise RacklintError(
                'reading switches needs the network extra: pip install "racklint[network]"'
            ) from error
        if access.verify_tls and access.ca_bundle is None:
            raise RacklintError(
                "verifying switch TLS certificates needs a CA bundle: pygnmi cannot use the system store"
            )
        self._access = access

    def get(
        self, address: str, requests: Sequence[GnmiRequest], credentials: Credentials
    ) -> dict[GnmiRequest, list[Update]]:
        from pygnmi.client import gNMIclient

        access = self._access
        tls: dict[str, Any] = (
            {"path_cert": str(access.ca_bundle)} if access.verify_tls else {"skip_verify": True}
        )
        try:
            with gNMIclient(
                target=(address, access.port),
                username=credentials.username,
                password=credentials.password.get_secret_value(),
                gnmi_timeout=TIMEOUT_SECONDS,
                **tls,
            ) as client:
                return _get_requests(client, requests)
        except Exception as error:  # pygnmi, gRPC and the OS raise their own types; all mean "not readable"
            raise _failure(error)(f"gNMI request to {address} failed: {error}") from error


def _get_requests(client: Any, requests: Sequence[GnmiRequest]) -> dict[GnmiRequest, list[Update]]:
    """The updates of each request, from a Get of its own.

    gNMI does not order the notifications of a Get with several paths, and sonic-gnmi builds them from Go
    maps, so each answer is tied to its request by being the only one.
    """
    return {request: _get_request(client, request) for request in requests}


def _get_request(client: Any, request: GnmiRequest) -> list[Update]:
    """The updates of one request. A switch with no data for an optional request gives none."""
    try:
        return response_updates(client.get(path=[request.path], target=request.target, encoding=ENCODING))
    except Exception as error:
        if request.optional and _status(error) == NOT_FOUND:
            return []
        raise


def _failure(error: Exception) -> type[GnmiError]:
    """The kind of GnmiError for what went wrong, from the gRPC status behind it."""
    status = _status(error)
    if status in REFUSED:
        return GnmiAuthError
    return GnmiNotFound if status == NOT_FOUND else GnmiError


def _status(error: Exception) -> str | None:
    """The name of the gRPC status behind an error, such as `UNAUTHENTICATED`, if it has one.

    pygnmi keeps gRPC's error as `orig_exc`, and gRPC's error gives its status from `code()`. Both are read by
    name, so racklint needs no gRPC types.
    """
    code = getattr(getattr(error, "orig_exc", None), "code", None)
    return getattr(code(), "name", None) if callable(code) else None


def parse_switch(components: list[Update], lldp: list[Update]) -> ActualSwitch:
    serials, os_version = openconfig_components(components)
    return ActualSwitch(
        serials=serials,
        firmware=(FirmwareItem(id="OS", version=os_version),) if os_version else (),
        neighbours=tuple(openconfig_neighbours(lldp)),
    )


def openconfig_components(updates: list[Update]) -> tuple[tuple[str, ...], str | None]:
    """The serial numbers of the `CHASSIS` components of `/components`, and the OS version if reported."""
    parts = [entry.get("state", entry) for entry in _entries(updates, "component")]
    serials = tuple(
        part["serial-no"]
        for part in parts
        if _identity(part.get("type")) == "CHASSIS" and part.get("serial-no")
    )
    return serials, _os_version(parts)


def openconfig_neighbours(updates: list[Update]) -> Iterator[LldpNeighbour]:
    """The LLDP neighbours of every interface of `/lldp/interfaces`."""
    for interface in _entries(updates, "interface"):
        for neighbour in _find_list(interface, "neighbor") or []:
            state = neighbour.get("state", neighbour)
            yield LldpNeighbour(
                interface=interface["name"],
                system_name=state.get("system-name", ""),
                port_id=state.get("port-id", ""),
                port_description=state.get("port-description", ""),
            )


def response_updates(response: dict[str, Any]) -> list[Update]:
    """The updates of a gNMI GetResponse, as pygnmi shapes it."""
    return [
        update
        for notification in response.get("notification", [])
        for update in notification.get("update", [])
    ]


def _entries(updates: list[Update], list_name: str) -> Iterator[dict[str, Any]]:
    """The entries of the YANG list `list_name`, wherever it sits in the updates' values."""
    for update in updates:
        found = _find_list(_strip_keys(update.get("val")), list_name)
        if found is not None:
            yield from found


def _find_list(value: Any, name: str) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == name and isinstance(item, list):
                return item
            found = _find_list(item, name)
            if found is not None:
                return found
    return None


def _strip_keys(value: Any) -> Any:
    """Drop JSON-IETF module prefixes from keys: `openconfig-platform:component` becomes `component`."""
    if isinstance(value, dict):
        return {key.split(":")[-1]: _strip_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_keys(item) for item in value]
    return value


def _identity(value: Any) -> str:
    """An identity without its module: `openconfig-platform-types:CHASSIS` becomes `CHASSIS`."""
    return str(value or "").split(":")[-1]


def _os_version(parts: list[dict[str, Any]]) -> str | None:
    for wanted in OS_COMPONENT_TYPES:
        for part in parts:
            if _identity(part.get("type")) == wanted and part.get("software-version"):
                return str(part["software-version"])
    return None
