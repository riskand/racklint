# racklint/src/racklint/testing.py
"""Read recorded responses through a platform, so a platform is tested without hardware.

A recording is what a device returned; racklint's CONTRIBUTING.md says what one may hold. The functions below
read a recording through the platform's own reader, built as a run builds it, and return what the platform
reports. The test compares that with what the platform should report. Nothing touches the network.
"""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from racklint.credentials import Credentials
from racklint.gnmi import GnmiAccess, GnmiClient, GnmiNotFound, GnmiRequest, Update, response_updates
from racklint.models import ActualNode, ActualSwitch
from racklint.platforms import ServerPlatform, SwitchPlatform
from racklint.redfish import SERVICE_ROOT, SESSION_TOKEN, BmcAccess, Resource

# The address and login a recording is read with. Nothing checks them.
ADDRESS = "192.0.2.1"
LOGIN = Credentials(username="recorded", password=SecretStr("recorded"), origin="a recording")
SESSION_TOKEN_VALUE = "recorded-session-token"


def load_mockup(directory: Path) -> dict[str, Resource]:
    """A Redfish recording in the layout of DMTF's Redfish-Mockup-Server: `<path>/index.json` per resource."""
    return {
        f"/{index.parent.relative_to(directory).as_posix()}": json.loads(index.read_text())
        for index in sorted(directory.rglob("index.json"))
    }


def recorded_bmc(resources: Mapping[str, Resource]) -> httpx.MockTransport:
    """Serves the resources by path; 404 for any other; accepts a session login and its logout.

    A session is offered only if the recording's service root links a session collection. Otherwise the
    reader falls back to Basic authentication, as with a BMC that has no session service.
    """
    sessions: str = resources.get(SERVICE_ROOT, {}).get("Links", {}).get("Sessions", {}).get("@odata.id", "")
    session = f"{sessions}/1"

    def respond(request: httpx.Request) -> httpx.Response:
        # A BMC serves a resource with or without a trailing slash; a recording is keyed without.
        path = request.url.path.rstrip("/")
        if sessions and request.method == "POST" and path == sessions:
            headers = {SESSION_TOKEN: SESSION_TOKEN_VALUE, "Location": session}
            return httpx.Response(httpx.codes.CREATED, headers=headers, json={"@odata.id": session})
        if sessions and request.method == "DELETE" and path == session:
            return httpx.Response(httpx.codes.NO_CONTENT)
        resource = resources.get(path)
        if resource is None:
            return httpx.Response(httpx.codes.NOT_FOUND)
        return httpx.Response(httpx.codes.OK, json=resource)

    return httpx.MockTransport(respond)


class _RecordedSwitch:
    """A GnmiClient that answers each request from its recorded GetResponse."""

    def __init__(self, responses: Mapping[GnmiRequest, dict[str, Any]]) -> None:
        self._responses = responses

    def get(
        self, address: str, requests: Sequence[GnmiRequest], credentials: Credentials
    ) -> dict[GnmiRequest, list[Update]]:
        return {request: self._updates(request) for request in requests}

    def _updates(self, request: GnmiRequest) -> list[Update]:
        # A switch with no data at a path answers NOT_FOUND, which is no updates for an optional request.
        if request in self._responses:
            return response_updates(self._responses[request])
        if request.optional:
            return []
        raise GnmiNotFound(f"no recorded response for {request.path}")


def recorded_switch(responses: Mapping[GnmiRequest, dict[str, Any]]) -> GnmiClient:
    """Answers each gNMI request with its recorded GetResponse.

    A request the recording lacks is a path the switch has no data for: no updates if it is optional,
    otherwise GnmiNotFound, which is not retried, so a recording missing a request fails at once.
    """
    return _RecordedSwitch(responses)


async def read_recorded_server(platform: ServerPlatform, resources: Mapping[str, Resource]) -> ActualNode:
    """What the platform's reader reports for a BMC that serves the recorded resources."""
    reader = platform.reader(BmcAccess(transport=recorded_bmc(resources)))
    return await reader.read(ADDRESS, LOGIN)


async def read_recorded_switch(
    platform: SwitchPlatform, responses: Mapping[GnmiRequest, dict[str, Any]]
) -> ActualSwitch:
    """What the platform's reader reports for a switch that answers with the recorded GetResponses."""
    reader = platform.reader(GnmiAccess(client=recorded_switch(responses), verify_tls=False))
    return await reader.read(ADDRESS, LOGIN)
