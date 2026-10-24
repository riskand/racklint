# racklint/tests/factories.py
"""A matching planned and actual node, and the NetBox and BMC responses that describe them.

Each test starts from these and states only what it changes.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import respx
from pydantic import SecretStr

from racklint.credentials import Credentials
from racklint.gnmi import GnmiRequest
from racklint.models import (
    ActualNode,
    ActualSwitch,
    Dimm,
    ExpectedNode,
    ExpectedSwitch,
    FirmwareItem,
    FirmwareRule,
    GpuSpec,
    HardwareSpec,
    Kind,
    LldpNeighbour,
    MemorySpec,
    NetworkPort,
    PlanContext,
    PlannedInterface,
    PlannedLink,
    Processor,
)

GPU_MODEL = "NVIDIA H100 80GB HBM3"
GPU_FIRMWARE = "96.00.89.00.01"
FIXTURES = Path(__file__).parent / "fixtures"
BMC_ADDRESS = "192.0.2.11"
SPEC = HardwareSpec(gpu=GpuSpec(count=2, model=GPU_MODEL), memory=MemorySpec(dimm_count=2, total_gib=128))
RULES = (
    FirmwareRule(match="BMC", approved=("1.4.0",)),
    FirmwareRule(match="GPU_*", approved=(GPU_FIRMWARE,)),
)
SWITCH_OS = "v26.7.2-519-g94fbd638fa8"
SWITCH_RULES = (FirmwareRule(match="OS", approved=(SWITCH_OS,)),)


PASSWORD = "bmc-secret"
# The client token Vault returns for an AppRole login.
CLIENT_TOKEN = "hvs.login-token"


def credentials(**changes: Any) -> Credentials:
    """The default server set's credentials; a password given in `changes` must be a SecretStr."""
    login = Credentials(username="admin", password=SecretStr(PASSWORD), origin="set default-server")
    return login.model_copy(update=changes)


def expected_node(**changes: Any) -> ExpectedNode:
    node = ExpectedNode(
        name="n01",
        device_type="gpu-node-h100",
        serial="SN-01",
        bmc_address="192.0.2.11",
        spec=SPEC,
        interfaces=(PlannedInterface(name="eth0", mac="02:00:00:00:01:01"), PlannedInterface(name="eth1")),
        firmware=RULES,
    )
    return node.model_copy(update=changes)


def actual_node(**changes: Any) -> ActualNode:
    node = ActualNode(
        serials=("SN-01",),
        processors=(
            Processor(id="CPU_0", type="CPU", model="Intel(R) Xeon(R) Platinum 8480+"),
            Processor(id="GPU_0", type="GPU", model=GPU_MODEL),
            Processor(id="GPU_1", type="GPU", model=GPU_MODEL),
        ),
        dimms=(Dimm(id="DIMM_0", capacity_mib=65536), Dimm(id="DIMM_1", capacity_mib=65536)),
        ports=(NetworkPort(id="0", mac="02:00:00:00:01:01"), NetworkPort(id="1", mac="02:00:00:00:01:02")),
        firmware=(
            FirmwareItem(id="BMC", version="1.4.0"),
            FirmwareItem(id="GPU_0", version=GPU_FIRMWARE),
            FirmwareItem(id="GPU_1", version=GPU_FIRMWARE),
        ),
    )
    return node.model_copy(update=changes)


def expected_switch(**changes: Any) -> ExpectedSwitch:
    switch = ExpectedSwitch(
        name="leaf1",
        device_type="ixr-d2l",
        serial="SN-SW-01",
        address="198.51.100.11",
        firmware=SWITCH_RULES,
        links=(
            PlannedLink(interface="ethernet-1/1", peer_device="spine1", peer_interface="ethernet-1/1"),
            PlannedLink(interface="ethernet-1/2", peer_device="spine1", peer_interface="ethernet-1/2"),
        ),
        uncabled_interfaces=("ethernet-1/3",),
    )
    return switch.model_copy(update=changes)


def actual_switch(**changes: Any) -> ActualSwitch:
    switch = ActualSwitch(
        serials=("SN-SW-01",),
        firmware=(FirmwareItem(id="OS", version=SWITCH_OS),),
        neighbours=(
            LldpNeighbour(interface="ethernet-1/1", system_name="spine1", port_id="ethernet-1/1"),
            LldpNeighbour(interface="ethernet-1/2", system_name="spine1", port_id="ethernet-1/2"),
            # Switches also see each other on their management ports; LinkCheck must ignore these.
            LldpNeighbour(interface="mgmt0", system_name="leaf2", port_id="mgmt0"),
        ),
    )
    return switch.model_copy(update=changes)


def gnmi_components(
    serial: str = "SN-SW-01", os_version: str = SWITCH_OS, extra: Sequence[dict[str, Any]] = ()
) -> list[dict[str, Any]]:
    """The updates a switch returns for /components, shaped as pygnmi returns them (SR Linux's layout)."""

    def component(name: str, kind: str, **state: str) -> dict[str, Any]:
        return {"name": name, "state": {"type": f"openconfig-platform-types:{kind}", **state}}

    components = [
        component("Chassis", "CHASSIS", **{"serial-no": serial}),
        component("ControlA", "CONTROLLER_CARD", **{"serial-no": serial, "software-version": os_version}),
        component("SoftwareModule-lldp_mgr", "SOFTWARE_MODULE", **{"software-version": os_version}),
        component("BootLoader-ControlA", "BOOT_LOADER", **{"software-version": "Sim bootloader"}),
        component("Ethernet-1/1-Port", "PORT"),
        *extra,
    ]
    return [{"path": "components", "val": {"openconfig-platform:components": {"component": components}}}]


def gnmi_lldp(neighbours: Sequence[tuple[str, str, str]]) -> list[dict[str, Any]]:
    """The updates a switch returns for /lldp/interfaces; a neighbour is (interface, system name, port id)."""
    by_interface: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for interface, system_name, port_id in neighbours:
        by_interface[interface].append(
            {
                "id": f"{system_name}/{port_id}",
                "state": {
                    "system-name": system_name,
                    "port-id": port_id,
                    "port-id-type": "openconfig-lldp-types:INTERFACE_NAME",
                    "port-description": "",
                },
            }
        )
    interfaces = [{"name": name, "neighbors": {"neighbor": found}} for name, found in by_interface.items()]
    return [{"path": "lldp/interfaces", "val": {"openconfig-lldp:interface": interfaces}}]


class FakeGnmiClient:
    """A GnmiClient giving one reply per call, then repeating the last; an exception reply is raised.

    It records each call's address and requests, and the username it was given.
    """

    def __init__(self, *replies: dict[GnmiRequest, list[dict[str, Any]]] | Exception) -> None:
        self._replies = replies
        self.calls: list[tuple[str, tuple[GnmiRequest, ...]]] = []
        self.usernames: list[str] = []

    def get(
        self, address: str, requests: Sequence[GnmiRequest], credentials: Credentials
    ) -> dict[GnmiRequest, list[dict[str, Any]]]:
        self.calls.append((address, tuple(requests)))
        self.usernames.append(credentials.username)
        reply = self._replies[min(len(self.calls), len(self._replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply


def bmc_tree() -> dict[str, Any]:
    """The Redfish resources of actual_node()'s BMC, keyed by URI. A fresh copy on every call."""
    tree: dict[str, Any] = json.loads((FIXTURES / "redfish_node.json").read_text())
    return tree


def remove_resource(tree: dict[str, Any], uri: str) -> None:
    """Delete a resource, and its link from the collection that lists it."""
    del tree[uri]
    for resource in tree.values():
        if "Members" in resource:
            resource["Members"] = [member for member in resource["Members"] if member["@odata.id"] != uri]


def serve_bmc(router: respx.MockRouter, tree: dict[str, Any], address: str = BMC_ADDRESS) -> respx.Route:
    """Answer every request to the BMC at `address` from `tree`."""

    def respond(request: httpx.Request) -> httpx.Response:
        resource = tree.get(request.url.path)
        return httpx.Response(404) if resource is None else httpx.Response(200, json=resource)

    return router.route(host=address).mock(side_effect=respond)


def page(*results: dict[str, Any], count: int | None = None) -> dict[str, Any]:
    """One page of a NetBox list endpoint."""
    return {"count": len(results) if count is None else count, "results": list(results)}


def netbox_device(device_id: int = 1, name: str = "n01", **changes: Any) -> dict[str, Any]:
    """A NetBox device record. Device 1 is planned exactly as expected_node()."""
    record = {
        "id": device_id,
        "name": name,
        "serial": f"SN-{device_id:02d}",
        "device_type": {"slug": "gpu-node-h100"},
        "oob_ip": {"address": f"192.0.2.{10 + device_id}/24"},
        "config_context": {
            "racklint": PlanContext(gpu=SPEC.gpu, memory=SPEC.memory, firmware=RULES).model_dump(mode="json")
        },
    }
    return record | changes


def netbox_switch(device_id: int = 11, name: str = "leaf1", **changes: Any) -> dict[str, Any]:
    """A NetBox switch record; with the ports from netbox_switch_ports(), planned as expected_switch()."""
    record = {
        "id": device_id,
        "name": name,
        "serial": "SN-SW-01",
        "device_type": {"slug": "ixr-d2l"},
        "oob_ip": None,
        "primary_ip": {"address": "198.51.100.11/24"},
        "config_context": {
            "racklint": PlanContext(kind=Kind.SWITCH, firmware=SWITCH_RULES).model_dump(mode="json")
        },
    }
    return record | changes


def far_interface(interface_id: int, device_id: int, device: str, name: str) -> dict[str, Any]:
    """A far-end interface, as NetBox nests it in `link_peers` and `connected_endpoints`: with no label."""
    return {"id": interface_id, "device": {"id": device_id, "name": device}, "name": name}


def netbox_port(
    name: str, cable: int | None = None, far: dict[str, Any] | None = None, **changes: Any
) -> dict[str, Any]:
    """A leaf1 interface. A cable straight to the interface `far` makes it link peer and traced end."""
    ends = [far] if far else []
    end_type = "dcim.interface" if far else None
    record = {
        "device": {"id": 11},
        "name": name,
        "primary_mac_address": None,
        "cable": {"id": cable} if cable else None,
        "link_peers": ends,
        "link_peers_type": end_type,
        "connected_endpoints": ends,
        "connected_endpoints_type": end_type,
    }
    return record | changes


def netbox_switch_ports() -> list[dict[str, Any]]:
    """leaf1's ports: two cabled to spine1, one cabled to a server, one without a cable."""
    return [
        netbox_port("ethernet-1/1", 101, far_interface(2101, 21, "spine1", "ethernet-1/1")),
        netbox_port("ethernet-1/2", 102, far_interface(2102, 21, "spine1", "ethernet-1/2")),
        netbox_port("ethernet-1/4", 103, far_interface(1001, 1, "n01", "eth0")),
        netbox_port("ethernet-1/3"),
    ]


def netbox_far_interfaces(labels: dict[int, str] | None = None) -> dict[str, Any]:
    """NetBox's answer when asked for the labels of netbox_switch_ports()' far ends; empty unless given."""
    labels = labels or {}
    return page(
        *({"id": interface_id, "label": labels.get(interface_id, "")} for interface_id in (2101, 2102, 1001))
    )


def netbox_interface(device_id: int, name: str, mac: str | None = None) -> dict[str, Any]:
    return {
        "device": {"id": device_id},
        "name": name,
        "primary_mac_address": {"mac_address": mac} if mac else None,
    }


def netbox_interfaces(device_id: int = 1) -> list[dict[str, Any]]:
    """The interfaces of expected_node(): eth0 with its MAC recorded, eth1 without."""
    return [netbox_interface(device_id, "eth0", "02:00:00:00:01:01"), netbox_interface(device_id, "eth1")]


def kv_secret(**data: Any) -> dict[str, Any]:
    """What Vault's KV version 2 returns for a read: the secret's map at data.data."""
    return {
        "request_id": "5b6a8c7e-0000-0000-0000-000000000000",
        "lease_id": "",
        "renewable": False,
        "lease_duration": 0,
        "data": {
            "data": data,
            "metadata": {
                "created_time": "2026-10-22T02:24:06.945319214Z",
                "custom_metadata": None,
                "deletion_time": "",
                "destroyed": False,
                "version": 1,
            },
        },
        "wrap_info": None,
        "warnings": None,
        "auth": None,
    }


def approle_login(token: str = CLIENT_TOKEN) -> dict[str, Any]:
    """What Vault returns for an AppRole login: the token at auth.client_token."""
    return {
        "auth": {
            "client_token": token,
            "accessor": "fd6c9a00-d2dc-3b11-0be5-af7ae0e1d374",
            "policies": ["default"],
            "token_policies": ["default"],
            "metadata": {"role_name": "racklint"},
            "lease_duration": 1200,
            "renewable": True,
        },
        "data": None,
        "lease_duration": 0,
        "renewable": False,
        "lease_id": "",
        "wrap_info": None,
        "warnings": None,
    }


def serve_netbox(router: respx.MockRouter, url: str) -> None:
    """Answer the NetBox calls for rack R01, which holds n01 planned as expected_node()."""
    router.get(f"{url}/api/dcim/racks/").respond(json=page({"id": 1, "site": {"slug": "dc1"}}))
    router.get(f"{url}/api/dcim/devices/").respond(json=page(netbox_device()))
    router.get(f"{url}/api/dcim/interfaces/").respond(json=page(*netbox_interfaces()))
