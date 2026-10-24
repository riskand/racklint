# racklint/examples/no-hardware/seed_fabric.py
"""Plan the example's fabric in NetBox: the switches, their management addresses, cables and config context,
and write the switches' credentials into Vault.

Usage: python seed_fabric.py, with NETBOX_URL, TOKEN_FILE, VAULT_ADDR and VAULT_TOKEN set, after seed.py.
Idempotent, like seed.py.
"""

import os
import tomllib
from pathlib import Path
from typing import Any

import httpx

from racklint import FirmwareBaseline, Kind, PlanContext
from seed import OOB_PREFIX_LENGTH, SITE_SLUG, NetBox, Record, Vault, ensure_interface, vault_client

HERE = Path(__file__).parent
ROLE_SLUG = "switch"
# SR Linux's default login, which containerlab leaves in place.
SWITCH_LOGIN = ("admin", "NokiaSrl1!")


def load_fabric() -> dict[str, Any]:
    """The plan in fabric/switches.toml."""
    with (HERE / "fabric" / "switches.toml").open("rb") as file:
        return tomllib.load(file)


def seed_fabric(netbox: NetBox) -> None:
    fabric = load_fabric()
    rules = FirmwareBaseline.load(HERE / "baseline.yaml").device_types[fabric["device_type"]]
    site = netbox.ensure(
        "dcim/sites/", {"slug": SITE_SLUG}, {"name": "DC1", "slug": SITE_SLUG, "status": "active"}
    )
    manufacturer_slug = fabric["manufacturer"].lower()
    manufacturer = netbox.ensure(
        "dcim/manufacturers/",
        {"slug": manufacturer_slug},
        {"name": fabric["manufacturer"], "slug": manufacturer_slug},
    )
    device_type = netbox.ensure(
        "dcim/device-types/",
        {"slug": fabric["device_type"]},
        {"manufacturer": manufacturer["id"], "model": fabric["model"], "slug": fabric["device_type"]},
    )
    role = netbox.ensure(
        "dcim/device-roles/", {"slug": ROLE_SLUG}, {"name": "Switch", "slug": ROLE_SLUG, "color": "2196f3"}
    )
    context_name = f"racklint {fabric['device_type']}"
    data = {
        "racklint": PlanContext(kind=Kind.SWITCH, platform="srlinux", firmware=rules).model_dump(mode="json")
    }
    context = netbox.ensure(
        "extras/config-contexts/",
        {"name": context_name},
        {"name": context_name, "device_types": [device_type["id"]], "data": data},
    )
    netbox.update("extras/config-contexts/", context, {"data": data})
    devices = {
        switch["name"]: seed_switch(
            netbox, switch, site=site, device_type=device_type, role=role, serial=fabric["serial"]
        )
        for switch in fabric["switches"]
    }
    for a_end, b_end in fabric["links"]:
        cable(netbox, *(port(netbox, devices, end, fabric["interface_type"]) for end in (a_end, b_end)))


def seed_vault(vault: Vault) -> None:
    vault.write("sets/default-switch", SWITCH_LOGIN)


def seed_switch(
    netbox: NetBox, switch: Record, *, site: Record, device_type: Record, role: Record, serial: str
) -> Record:
    rack = netbox.ensure(
        "dcim/racks/",
        {"site_id": site["id"], "name": switch["rack"]},
        {"site": site["id"], "name": switch["rack"], "status": "active"},
    )
    device = netbox.ensure(
        "dcim/devices/",
        {"site_id": site["id"], "name": switch["name"]},
        {
            "name": switch["name"],
            "site": site["id"],
            "rack": rack["id"],
            "device_type": device_type["id"],
            "role": role["id"],
            "serial": serial,
            "status": "staged",
        },
    )
    mgmt = ensure_interface(netbox, device, "mgmt0", "1000base-t", mgmt_only=True)
    address = f"{switch['mgmt_ip']}/{OOB_PREFIX_LENGTH}"
    oob_ip = netbox.ensure(
        "ipam/ip-addresses/",
        {"address": address},
        {
            "address": address,
            "status": "active",
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": mgmt["id"],
        },
    )
    netbox.update("dcim/devices/", device, {"oob_ip": oob_ip["id"]})
    return device


def port(netbox: NetBox, devices: dict[str, Record], end: str, interface_type: str) -> Record:
    device_name, interface_name = end.split(":")
    return ensure_interface(netbox, devices[device_name], interface_name, interface_type)


def cable(netbox: NetBox, a_end: Record, b_end: Record) -> None:
    """Cable two interfaces, unless the first already has a cable (the seed is re-run)."""
    if a_end.get("cable"):
        return
    netbox.create(
        "dcim/cables/",
        {
            "a_terminations": [{"object_type": "dcim.interface", "object_id": a_end["id"]}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": b_end["id"]}],
            "status": "planned",
        },
    )


def main() -> None:
    url = os.environ["NETBOX_URL"]
    token = Path(os.environ["TOKEN_FILE"]).read_text()
    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
        seed_fabric(NetBox(client))
    with vault_client() as client:
        seed_vault(Vault(client))
    print("seeded the fabric: leaf1-leaf3 in R01, spine1 in R02, and their credentials in Vault")


if __name__ == "__main__":
    main()
