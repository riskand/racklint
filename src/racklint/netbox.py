# racklint/src/racklint/netbox.py
"""Read the planned state of devices from the NetBox REST API. racklint only ever sends GET requests."""

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import ValidationError

from racklint.errors import NetBoxError
from racklint.http import describe_error
from racklint.models import (
    ExpectedNode,
    ExpectedSwitch,
    Kind,
    PlanContext,
    PlannedInterface,
    PlannedLink,
    UncheckableLink,
)

Record = dict[str, Any]
Expected = ExpectedNode | ExpectedSwitch
Cabling = PlannedLink | UncheckableLink

CONFIG_CONTEXT_KEY = "racklint"
PAGE_SIZE = 250
TIMEOUT = httpx.Timeout(30.0)
# Cables in these states are part of the plan; a decommissioning cable is not.
PLANNED_CABLE_STATUSES = ["planned", "connected"]


def auth_header(token: str) -> str:
    """v2 tokens (NetBox 4.5+, prefixed nbt_) use the Bearer scheme; legacy tokens use Token."""
    scheme = "Bearer" if token.startswith("nbt_") else "Token"
    return f"{scheme} {token}"


def netbox_client(url: str, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=url,
        headers={"Authorization": auth_header(token), "Accept": "application/json"},
        timeout=TIMEOUT,
    )


class NetBoxSource:
    """Turns NetBox devices into ExpectedNodes and ExpectedSwitches, reading them and interfaces in bulk."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def rack(self, name: str, site: str | None = None) -> list[Expected]:
        query = {"name": name} if site is None else {"name": name, "site": site}
        racks = await self._list("dcim/racks", query)
        if not racks:
            raise NetBoxError(f"rack {name!r} not found")
        if len(racks) > 1:
            sites = ", ".join(sorted(rack["site"]["slug"] for rack in racks))
            raise NetBoxError(f"rack {name!r} exists in several sites ({sites}); pass --site")
        devices = await self._list("dcim/devices", {"rack_id": racks[0]["id"]})
        if not devices:
            raise NetBoxError(f"rack {name!r} has no devices")
        return await self._expected(devices)

    async def devices(self, names: Sequence[str]) -> list[Expected]:
        devices = await self._list("dcim/devices", {"name": list(names)})
        missing = set(names) - {device["name"] for device in devices}
        if missing:
            raise NetBoxError(f"devices not found: {', '.join(sorted(missing))}")
        return await self._expected(devices)

    async def _expected(self, devices: list[Record]) -> list[Expected]:
        contexts = {device["id"]: _plan_context(device) or PlanContext() for device in devices}
        interfaces = await self._list(
            "dcim/interfaces",
            {"device_id": [device["id"] for device in devices], "kind": "physical", "mgmt_only": "false"},
        )
        by_device: defaultdict[int, list[Record]] = defaultdict(list)
        for interface in interfaces:
            by_device[interface["device"]["id"]].append(interface)
        switch_ids = [device_id for device_id, context in contexts.items() if context.kind is Kind.SWITCH]
        cabling = await self._switch_cabling(switch_ids, by_device) if switch_ids else {}
        return [
            _expected_device(
                device, contexts[device["id"]], by_device[device["id"]], cabling.get(device["id"], [])
            )
            for device in devices
        ]

    async def _switch_cabling(
        self, switch_ids: list[int], by_device: dict[int, list[Record]]
    ) -> dict[int, list[Cabling]]:
        """Each switch's planned cables: the links LinkCheck checks, and the ones it cannot check.

        Servers send no LLDP at bring-up, so cables to a device planned as a server are left out. Whether the
        far end is a switch or a server comes from its own config context.
        """
        cables = await self._list("dcim/cables", {"device_id": switch_ids, "status": PLANNED_CABLE_STATUSES})
        planned_cables = {cable["id"] for cable in cables}
        cabled = {
            switch_id: [
                (interface["name"], _far_end(interface))
                for interface in by_device[switch_id]
                if (interface.get("cable") or {}).get("id") in planned_cables
            ]
            for switch_id in switch_ids
        }
        fars = [far for ports in cabled.values() for _, far in ports if far]
        peer_ids = sorted({far["device"]["id"] for far in fars})
        peers = await self._list("dcim/devices", {"id": peer_ids}) if peer_ids else []
        kinds = {peer["id"]: context.kind for peer in peers if (context := _plan_context(peer))}
        labels = await self._labels({far["id"] for far in fars})
        return {
            switch_id: [cabling for name, far in ports if (cabling := _cabling(name, far, kinds, labels))]
            for switch_id, ports in cabled.items()
        }

    async def _labels(self, interface_ids: set[int]) -> dict[int, str]:
        """The labels of the interfaces that have one; a cable's far end is a brief interface without it."""
        if not interface_ids:
            return {}
        interfaces = await self._list("dcim/interfaces", {"id": sorted(interface_ids)})
        return {interface["id"]: interface["label"] for interface in interfaces if interface.get("label")}

    async def _list(self, endpoint: str, query: Record) -> list[Record]:
        """Every object a list endpoint returns for `query`, page by page."""
        records: list[Record] = []
        while True:
            page = await self._get(f"/api/{endpoint}/", query | {"limit": PAGE_SIZE, "offset": len(records)})
            records.extend(page["results"])
            if not page["results"] or len(records) >= page["count"]:
                return records

    async def _get(self, path: str, params: Record) -> Record:
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            page: Record = response.json()
        except httpx.HTTPError as error:
            raise NetBoxError(f"NetBox request failed: {describe_error(error)}") from error
        except ValueError as error:
            raise NetBoxError(f"NetBox request failed: {error}") from error
        return page


def _plan_context(device: Record) -> PlanContext | None:
    """The device's `racklint` config context, or None when it has none."""
    context = (device.get("config_context") or {}).get(CONFIG_CONTEXT_KEY)
    if context is None:
        return None
    try:
        return PlanContext.model_validate(context)
    except ValidationError as error:
        raise NetBoxError(
            f"{device['name']}: invalid {CONFIG_CONTEXT_KEY!r} config context: {error}"
        ) from error


def _far_end(interface: Record) -> Record | None:
    """The interface at the far end of the interface's cable, or None when NetBox records no interface there.

    `connected_endpoints` is NetBox's path traced through patch panels; `link_peers` is the cable's own far
    end, used where NetBox has traced no path.
    """
    for ends in ("connected_endpoints", "link_peers"):
        if interface.get(f"{ends}_type") == "dcim.interface" and interface.get(ends):
            return dict(interface[ends][0])
    return None


def _cabling(
    interface: str, far: Record | None, kinds: dict[int, Kind], labels: dict[int, str]
) -> Cabling | None:
    """A link to check, a link that cannot be checked, or None for a cable to a server."""
    if far is None:
        return UncheckableLink(interface=interface, reason="its cable does not reach an interface in NetBox")
    peer = far["device"]
    kind = kinds.get(peer["id"])
    if kind is None:
        return UncheckableLink(
            interface=interface, reason=f"{peer['name']} has no {CONFIG_CONTEXT_KEY} config context"
        )
    if kind is Kind.SWITCH:
        return PlannedLink(
            interface=interface,
            peer_device=peer["name"],
            peer_interface=far["name"],
            peer_interface_label=labels.get(far["id"]),
        )
    return None


def _expected_device(
    device: Record, context: PlanContext, interfaces: list[Record], cabling: list[Cabling]
) -> Expected:
    if context.kind is Kind.SWITCH:
        return _expected_switch(device, context, interfaces, cabling)
    return _expected_server(device, context, interfaces)


def _address(ip: Record | None) -> str | None:
    return ip["address"].split("/")[0] if ip else None


def _expected_server(device: Record, context: PlanContext, interfaces: list[Record]) -> ExpectedNode:
    return ExpectedNode(
        name=device["name"],
        device_type=device["device_type"]["slug"],
        serial=device.get("serial") or None,
        credential_set=context.credentials,
        platform=context.platform,
        bmc_address=_address(device.get("oob_ip")),
        spec=context.spec,
        interfaces=tuple(_planned_interface(interface) for interface in interfaces),
        firmware=context.firmware,
    )


def _expected_switch(
    device: Record, context: PlanContext, interfaces: list[Record], cabling: list[Cabling]
) -> ExpectedSwitch:
    """A switch is managed at its out-of-band IP, or at its primary IP where teams record it there."""
    return ExpectedSwitch(
        name=device["name"],
        device_type=device["device_type"]["slug"],
        serial=device.get("serial") or None,
        credential_set=context.credentials,
        platform=context.platform,
        gnmi_port=context.gnmi_port,
        address=_address(device.get("oob_ip") or device.get("primary_ip")),
        firmware=context.firmware,
        links=tuple(link for link in cabling if isinstance(link, PlannedLink)),
        uncheckable_links=tuple(link for link in cabling if isinstance(link, UncheckableLink)),
        uncabled_interfaces=tuple(
            interface["name"] for interface in interfaces if not interface.get("cable")
        ),
    )


def _planned_interface(interface: Record) -> PlannedInterface:
    primary_mac = interface.get("primary_mac_address")
    return PlannedInterface(name=interface["name"], mac=primary_mac["mac_address"] if primary_mac else None)
