# racklint/src/racklint/redfish.py
"""Read a node's hardware from its BMC over Redfish, without the host OS running.

The reader starts at the service root and follows @odata.id links, so it relies on no vendor-specific
paths. Every member of each ComputerSystem's Processors is reported; the GPU rules decide which are GPUs.
"""

import asyncio
import contextlib
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from racklint.credentials import Credentials
from racklint.errors import AuthenticationError, InventoryError
from racklint.http import describe_error
from racklint.models import ActualNode, Dimm, FirmwareItem, NetworkPort, Processor
from racklint.retry import ATTEMPTS, BACKOFF_SECONDS, pause

Resource = dict[str, Any]

SERVICE_ROOT = "/redfish/v1"
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
SESSION_TOKEN = "X-Auth-Token"
# The statuses with which a BMC refuses the credentials, and what to say about each.
REFUSED: dict[int, str] = {
    httpx.codes.UNAUTHORIZED: "authentication failed",
    httpx.codes.FORBIDDEN: "access denied (HTTP 403)",
}


class RedfishError(InventoryError):
    """The BMC could not be read."""


class RedfishAuthError(RedfishError, AuthenticationError):
    """The BMC refused the credentials."""


@dataclass(frozen=True)
class BmcAccess:
    """How to reach the BMCs: TLS settings and request limits for all of them.

    The credentials come per BMC, with each read.
    """

    scheme: str = "https"
    verify: ssl.SSLContext | bool = True
    max_requests: int = 4
    # How to reach the BMC; None for the network. The conformance kit passes recorded responses here.
    transport: httpx.AsyncBaseTransport | None = None


class RedfishReader:
    """The engine's ActualReader for BMCs that speak Redfish."""

    def __init__(self, access: BmcAccess) -> None:
        self._access = access

    async def read(self, address: str, credentials: Credentials) -> ActualNode:
        async with httpx.AsyncClient(
            base_url=f"{self._access.scheme}://{_url_host(address)}",
            verify=self._access.verify,
            transport=self._access.transport,
            timeout=TIMEOUT,
        ) as http:
            client = RedfishClient(http, self._access.max_requests)
            try:
                root = await _service_root(client, credentials)
                async with _logged_in(client, root, credentials):
                    return await read_inventory(client, root)
            except (KeyError, TypeError, ValueError) as error:
                raise RedfishError(f"unexpected Redfish payload: {error!r}") from error


class RedfishClient:
    """Talks to one BMC: GETs resources and logs in and out.

    At most `max_requests` run at a time, and transient failures are retried.
    """

    def __init__(self, http: httpx.AsyncClient, max_requests: int, backoff: float = BACKOFF_SECONDS) -> None:
        self._http = http
        self._slots = asyncio.Semaphore(max_requests)
        self._backoff = backoff
        self._basic: httpx.BasicAuth | None = None

    async def get(self, path: str) -> Resource:
        response = await self._send("GET", path)
        _raise_for_status(response, f"GET {path}")
        try:
            resource = response.json()
        except ValueError:
            resource = None
        if not isinstance(resource, dict):
            raise RedfishError(f"GET {path} returned a body that is not a JSON object")
        return resource

    async def login(self, path: str, credentials: Credentials) -> str:
        """Open a session at the sessions collection `path`. Every later request carries its token.

        Returns the session's path, for logout.
        """
        body = {"UserName": credentials.username, "Password": credentials.password.get_secret_value()}
        response = await self._send("POST", path, json=body)
        _raise_for_status(response, f"POST {path}")
        token = response.headers.get(SESSION_TOKEN)
        location = response.headers.get("Location") or _odata_id(response)
        if not token or not location:
            raise RedfishError(f"POST {path} returned no session token or location")
        self._http.headers[SESSION_TOKEN] = token
        self._basic = None  # the session replaces Basic authentication, if it was in use
        # Only the path: the BMC's Location may be a full URL, and the token goes to this BMC alone.
        return httpx.URL(location).path

    async def logout(self, location: str) -> None:
        """Close the session. Failures are ignored: the BMC's session timeout removes a session left over."""
        with contextlib.suppress(httpx.HTTPError):
            await self._http.delete(location)

    def use_basic_auth(self, credentials: Credentials) -> None:
        self._basic = httpx.BasicAuth(credentials.username, credentials.password.get_secret_value())

    async def _send(self, method: str, path: str, json: Resource | None = None) -> httpx.Response:
        async with self._slots:
            return await self._send_with_retries(method, path, json)

    async def _send_with_retries(self, method: str, path: str, json: Resource | None) -> httpx.Response:
        for attempt in range(ATTEMPTS - 1):
            # Connection errors, timeouts, 5xx and 429 are worth another try; any other answer is final.
            with contextlib.suppress(httpx.TransportError):
                response = await self._http.request(method, path, json=json, auth=self._basic)
                if not _is_transient(response.status_code):
                    return response
            await pause(attempt, self._backoff)
        try:
            return await self._http.request(method, path, json=json, auth=self._basic)
        except httpx.TransportError as error:
            raise RedfishError(f"{method} {path} failed: {describe_error(error)}") from error


async def _service_root(client: RedfishClient, credentials: Credentials) -> Resource:
    """The service root, which Redfish lets anyone read; with Basic authentication for a BMC that does not."""
    try:
        return await client.get(SERVICE_ROOT)
    except RedfishAuthError:
        client.use_basic_auth(credentials)
        return await client.get(SERVICE_ROOT)


@asynccontextmanager
async def _logged_in(client: RedfishClient, root: Resource, credentials: Credentials) -> AsyncIterator[None]:
    """One session for the whole read, or Basic authentication when the BMC has no session service."""
    sessions = (root.get("Links") or {}).get("Sessions")
    if sessions is None:
        client.use_basic_auth(credentials)
        yield
        return
    location = await client.login(sessions["@odata.id"], credentials)
    try:
        yield
    finally:
        await client.logout(location)


def _raise_for_status(response: httpx.Response, request: str) -> None:
    if response.status_code in REFUSED:
        raise RedfishAuthError(REFUSED[response.status_code])
    if response.is_error:
        raise RedfishError(f"{request} returned HTTP {response.status_code}")


def _odata_id(response: httpx.Response) -> str | None:
    """The `@odata.id` of a response's body, if it has one."""
    try:
        return str(response.json()["@odata.id"])
    except (ValueError, KeyError, TypeError):
        return None


def _is_transient(status_code: int) -> bool:
    return status_code == httpx.codes.TOO_MANY_REQUESTS or status_code >= httpx.codes.INTERNAL_SERVER_ERROR


def _url_host(address: str) -> str:
    """An IPv6 address needs brackets in a URL."""
    return f"[{address}]" if ":" in address else address


async def read_inventory(client: RedfishClient, root: Resource) -> ActualNode:
    """Walk the service from its root a level at a time, fetching each level's collections concurrently."""
    systems, chassis, update_service = await asyncio.gather(
        _members(client, root, "Systems"),
        _members(client, root, "Chassis"),
        _follow(client, root, "UpdateService"),
    )
    processors, modules, adapters, firmware = await asyncio.gather(
        _members_of_each(client, systems, "Processors"),
        _members_of_each(client, systems, "Memory"),
        _members_of_each(client, chassis, "NetworkAdapters"),
        _members(client, update_service, "FirmwareInventory"),
    )
    adapter_ports = await asyncio.gather(*(_adapter_ports(client, adapter) for adapter in adapters))
    return ActualNode(
        serials=tuple(system["SerialNumber"] for system in systems if system.get("SerialNumber")),
        processors=tuple(_processor(processor) for processor in processors),
        dimms=tuple(_dimm(module) for module in modules if _is_enabled(module)),
        ports=tuple(port for ports in adapter_ports for port in ports),
        firmware=tuple(_firmware_item(item) for item in firmware),
    )


async def _follow(client: RedfishClient, resource: Resource | None, key: str) -> Resource | None:
    """The resource that `resource[key]` links to, if there is such a link."""
    link = resource.get(key) if resource else None
    return await client.get(link["@odata.id"]) if link else None


async def _members(client: RedfishClient, resource: Resource | None, key: str) -> list[Resource]:
    """The members of the collection that `resource[key]` links to; none if there is no such link."""
    collection = await _follow(client, resource, key)
    if collection is None:
        return []
    return list(
        await asyncio.gather(*(client.get(member["@odata.id"]) for member in collection.get("Members", [])))
    )


async def _members_of_each(client: RedfishClient, resources: list[Resource], key: str) -> list[Resource]:
    nested = await asyncio.gather(*(_members(client, resource, key) for resource in resources))
    return [member for members in nested for member in members]


async def _adapter_ports(client: RedfishClient, adapter: Resource) -> list[NetworkPort]:
    """An adapter's ports: its NetworkDeviceFunctions, or its Ports when it has no functions."""
    functions = await _members(client, adapter, "NetworkDeviceFunctions")
    if functions:
        return [_port_from_function(function) for function in functions]
    return [_port_from_port(port) for port in await _members(client, adapter, "Ports")]


def _processor(processor: Resource) -> Processor:
    return Processor(
        id=processor["Id"],
        type=processor.get("ProcessorType") or "",
        model=processor.get("Model") or "",
    )


def _dimm(module: Resource) -> Dimm:
    return Dimm(id=module["Id"], capacity_mib=module["CapacityMiB"])


def _port_from_function(function: Resource) -> NetworkPort:
    ethernet = function.get("Ethernet") or {}
    return NetworkPort(
        id=function["Id"], mac=ethernet.get("PermanentMACAddress") or ethernet.get("MACAddress")
    )


def _port_from_port(port: Resource) -> NetworkPort:
    """A Port carries a MAC only in Ethernet.AssociatedMACAddresses (Port v1_4_0 and later), if at all."""
    macs = (port.get("Ethernet") or {}).get("AssociatedMACAddresses") or []
    return NetworkPort(id=port["Id"], mac=macs[0] if macs else None)


def _firmware_item(item: Resource) -> FirmwareItem:
    return FirmwareItem(id=item["Id"], version=item.get("Version") or "")


def _is_enabled(resource: Resource) -> bool:
    return (resource.get("Status") or {}).get("State") == "Enabled"
