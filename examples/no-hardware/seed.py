# racklint/examples/no-hardware/seed.py
"""Write the example's plan into NetBox (site dc1, rack R01, the nodes in nodes/), provision an API token, and
write the servers' credentials into Vault.

Usage: python seed.py, with NETBOX_URL, SUPERUSER_NAME, SUPERUSER_PASSWORD, TOKEN_FILE, VAULT_ADDR and
VAULT_TOKEN set. Every NetBox object is looked up before it is created, and a Vault secret written again only
gets a new version, so running the seed twice changes nothing that racklint reads.
"""

import os
from pathlib import Path
from typing import Any

import httpx

from node_files import Node, defaults, device_context, node_names, plan_context, planned
from racklint import FirmwareBaseline

Record = dict[str, Any]

BASELINE = Path(__file__).parent / "baseline.yaml"
SITE_SLUG = "dc1"
RACK_NAME = "R01"
ROLE_SLUG = "gpu-node"
OOB_PREFIX_LENGTH = 24
BMC_INTERFACE_TYPE = "1000base-t"
DATA_INTERFACE_TYPE = "100gbase-x-qsfp28"
# The mock BMCs accept any login. The password is an example value, like every password here.
SERVER_LOGIN = ("admin", "racklint-example")
# The node whose factory secret shows where one goes in Vault.
FACTORY_SECRET_NODE = "n02"


class NetBox:
    """The few NetBox API calls the seed needs."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def ensure(self, endpoint: str, query: Record, payload: Record) -> Record:
        """Return the object `query` finds, or create it from `payload` if there is none."""
        found = self._request("GET", endpoint, params=query)["results"]
        return found[0] if found else self.create(endpoint, payload)

    def create(self, endpoint: str, payload: Record) -> Record:
        return self._request("POST", endpoint, json=payload)

    def update(self, endpoint: str, record: Record, fields: Record) -> None:
        self._request("PATCH", f"{endpoint}{record['id']}/", json=fields)

    def _request(self, method: str, path: str, **kwargs: Any) -> Record:
        response = self._client.request(method, f"/api/{path}", **kwargs)
        response.raise_for_status()
        record: Record = response.json()
        return record


class Vault:
    """The one Vault call the seed needs: writing a username and password to KV version 2."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def write(self, path: str, login: tuple[str, str]) -> None:
        username, password = login
        response = self._client.post(
            f"/v1/secret/data/racklint/{path}", json={"data": {"username": username, "password": password}}
        )
        response.raise_for_status()


def vault_client() -> httpx.Client:
    """A client for the Vault at VAULT_ADDR, logged in with VAULT_TOKEN."""
    return httpx.Client(
        base_url=os.environ["VAULT_ADDR"], headers={"X-Vault-Token": os.environ["VAULT_TOKEN"]}, timeout=30
    )


def provision_token(url: str, username: str, password: str) -> str:
    """Create a v2 API token for the superuser and return it in the form NetBox expects after "Bearer"."""
    response = httpx.post(
        f"{url}/api/users/tokens/provision/", json={"username": username, "password": password}, timeout=30
    )
    response.raise_for_status()
    token = response.json()
    return f"nbt_{token['key']}.{token['token']}"


def seed(netbox: NetBox) -> None:
    hardware = defaults()
    manufacturer_slug = hardware["manufacturer"].lower().replace(" ", "-")
    site = netbox.ensure(
        "dcim/sites/", {"slug": SITE_SLUG}, {"name": SITE_SLUG.upper(), "slug": SITE_SLUG, "status": "active"}
    )
    rack = netbox.ensure(
        "dcim/racks/",
        {"site_id": site["id"], "name": RACK_NAME},
        {"site": site["id"], "name": RACK_NAME, "status": "active"},
    )
    manufacturer = netbox.ensure(
        "dcim/manufacturers/",
        {"slug": manufacturer_slug},
        {"name": hardware["manufacturer"], "slug": manufacturer_slug},
    )
    device_type = netbox.ensure(
        "dcim/device-types/",
        {"slug": hardware["device_type"]},
        {"manufacturer": manufacturer["id"], "model": hardware["model"], "slug": hardware["device_type"]},
    )
    role = netbox.ensure(
        "dcim/device-roles/", {"slug": ROLE_SLUG}, {"name": "GPU node", "slug": ROLE_SLUG, "color": "4caf50"}
    )
    context_name = f"racklint {hardware['device_type']}"
    rules = FirmwareBaseline.load(BASELINE).device_types[hardware["device_type"]]
    data = {"racklint": plan_context(hardware, rules).model_dump(mode="json")}
    context = netbox.ensure(
        "extras/config-contexts/",
        {"name": context_name},
        {"name": context_name, "device_types": [device_type["id"]], "data": data},
    )
    # A stack seeded earlier may hold an older context; bring it up to date.
    netbox.update("extras/config-contexts/", context, {"data": data})
    for name in node_names():
        seed_node(netbox, planned(name), site=site, rack=rack, device_type=device_type, role=role)


def seed_vault(vault: Vault) -> None:
    """The default set for servers, and a factory secret by serial. n04's set, bmc-r02, is left out."""
    vault.write("sets/default-server", SERVER_LOGIN)
    vault.write(f"factory/{planned(FACTORY_SECRET_NODE)['serial']}", SERVER_LOGIN)


def seed_node(
    netbox: NetBox, node: Node, *, site: Record, rack: Record, device_type: Record, role: Record
) -> None:
    device = netbox.ensure(
        "dcim/devices/",
        {"site_id": site["id"], "name": node["name"]},
        {
            "name": node["name"],
            "site": site["id"],
            "rack": rack["id"],
            "device_type": device_type["id"],
            "role": role["id"],
            "serial": node["serial"],
            "status": "staged",
        },
    )
    bmc = ensure_interface(netbox, device, "bmc", BMC_INTERFACE_TYPE, mgmt_only=True)
    address = f"{node['bmc_ip']}/{OOB_PREFIX_LENGTH}"
    oob_ip = netbox.ensure(
        "ipam/ip-addresses/",
        {"address": address},
        {
            "address": address,
            "status": "active",
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": bmc["id"],
        },
    )
    netbox.update("dcim/devices/", device, {"oob_ip": oob_ip["id"]})
    if (context := device_context(node)) is not None:
        netbox.update("dcim/devices/", device, {"local_context_data": context})
    for index, mac in enumerate(node["macs"]):
        interface = ensure_interface(netbox, device, f"eth{index}", DATA_INTERFACE_TYPE)
        if node["macs_recorded"]:
            record_mac(netbox, interface, mac)


def ensure_interface(netbox: NetBox, device: Record, name: str, kind: str, mgmt_only: bool = False) -> Record:
    return netbox.ensure(
        "dcim/interfaces/",
        {"device_id": device["id"], "name": name},
        {"device": device["id"], "name": name, "type": kind, "mgmt_only": mgmt_only},
    )


def record_mac(netbox: NetBox, interface: Record, mac: str) -> None:
    mac_address = netbox.ensure(
        "dcim/mac-addresses/",
        {"mac_address": mac},
        {"mac_address": mac, "assigned_object_type": "dcim.interface", "assigned_object_id": interface["id"]},
    )
    netbox.update("dcim/interfaces/", interface, {"primary_mac_address": mac_address["id"]})


def main() -> None:
    url = os.environ["NETBOX_URL"]
    token = provision_token(url, os.environ["SUPERUSER_NAME"], os.environ["SUPERUSER_PASSWORD"])
    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
        seed(NetBox(client))
    with vault_client() as client:
        seed_vault(Vault(client))
    token_file = Path(os.environ["TOKEN_FILE"])
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token)
    print(f"seeded rack {RACK_NAME} with {len(node_names())} nodes, and their credentials in Vault")


if __name__ == "__main__":
    main()
