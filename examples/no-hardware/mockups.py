# racklint/examples/no-hardware/mockups.py
"""Render one Redfish tree per example node, in the layout the DMTF Redfish-Mockup-Server serves.

Usage: python mockups.py OUTPUT_DIR
Writes OUTPUT_DIR/<node>/redfish/v1/.../index.json for every node in nodes/.
"""

import json
import sys
from itertools import batched
from pathlib import Path
from typing import Any

from node_files import Node, actual, node_names

Resource = dict[str, Any]

ROOT = "/redfish/v1"
SYSTEM = f"{ROOT}/Systems/1"
CHASSIS = f"{ROOT}/Chassis/1"
MANAGER = f"{ROOT}/Managers/BMC"
UPDATE_SERVICE = f"{ROOT}/UpdateService"
FIRMWARE_INVENTORY = f"{UPDATE_SERVICE}/FirmwareInventory"
ENABLED = {"State": "Enabled", "Health": "OK"}
ABSENT = {"State": "Absent"}


class Tree:
    """Redfish resources keyed by their URI."""

    def __init__(self) -> None:
        self.resources: dict[str, Resource] = {}

    def add(self, uri: str, odata_type: str, fields: Resource) -> str:
        self.resources[uri] = {"@odata.id": uri, "@odata.type": odata_type, **fields}
        return uri

    def add_collection(self, uri: str, odata_type: str, name: str, members: list[str]) -> str:
        fields = {"Name": name, "Members@odata.count": len(members), "Members": [link(m) for m in members]}
        return self.add(uri, odata_type, fields)


def link(uri: str) -> Resource:
    return {"@odata.id": uri}


def build_tree(node: Node) -> dict[str, Resource]:
    """Every resource the node's BMC serves, keyed by URI."""
    tree = Tree()
    add_system(tree, node)
    add_chassis(tree, node)
    add_manager(tree, node)
    add_update_service(tree, node)
    tree.add(
        ROOT,
        "#ServiceRoot.v1_15_0.ServiceRoot",
        {
            "Id": "RootService",
            "Name": "Root Service",
            "RedfishVersion": "1.17.0",
            "Systems": link(f"{ROOT}/Systems"),
            "Chassis": link(f"{ROOT}/Chassis"),
            "Managers": link(f"{ROOT}/Managers"),
            "UpdateService": link(UPDATE_SERVICE),
        },
    )
    return tree.resources


def add_system(tree: Tree, node: Node) -> None:
    gpu_type = node.get("gpu_processor_type", "GPU")
    processors = [
        add_processor(tree, f"CPU_{index}", "CPU", node["cpu_model"]) for index in range(node["cpu_count"])
    ] + [
        add_processor(tree, f"GPU_{index}", gpu_type, node["gpu_model"]) for index in range(node["gpu_count"])
    ]
    tree.add_collection(
        f"{SYSTEM}/Processors", "#ProcessorCollection.ProcessorCollection", "Processors", processors
    )
    modules = [
        tree.add(
            f"{SYSTEM}/Memory/DIMM_{slot}",
            "#Memory.v1_17_0.Memory",
            {"Id": f"DIMM_{slot}", "Name": f"DIMM {slot}", **dimm(node, slot)},
        )
        for slot in range(node["dimm_slots"])
    ]
    tree.add_collection(f"{SYSTEM}/Memory", "#MemoryCollection.MemoryCollection", "Memory", modules)
    tree.add(
        SYSTEM,
        "#ComputerSystem.v1_20_0.ComputerSystem",
        {
            "Id": "1",
            "Name": "System",
            "SystemType": "Physical",
            "Manufacturer": node["manufacturer"],
            "Model": node["model"],
            "SerialNumber": node["serial"],
            "PowerState": "Off",
            "Status": ENABLED,
            "Processors": link(f"{SYSTEM}/Processors"),
            "Memory": link(f"{SYSTEM}/Memory"),
            "Links": {"Chassis": [link(CHASSIS)], "ManagedBy": [link(MANAGER)]},
        },
    )
    tree.add_collection(
        f"{ROOT}/Systems", "#ComputerSystemCollection.ComputerSystemCollection", "Computer Systems", [SYSTEM]
    )


def add_processor(tree: Tree, processor_id: str, processor_type: str, model: str) -> str:
    return tree.add(
        f"{SYSTEM}/Processors/{processor_id}",
        "#Processor.v1_18_0.Processor",
        {
            "Id": processor_id,
            "Name": processor_id,
            "ProcessorType": processor_type,
            "Model": model,
            "Status": ENABLED,
        },
    )


def dimm(node: Node, slot: int) -> Resource:
    """Slots beyond the node's DIMM count are empty, as BMCs report them."""
    if slot >= node["dimm_count"]:
        return {"Status": ABSENT}
    return {"CapacityMiB": node["dimm_capacity_mib"], "MemoryDeviceType": "DDR5", "Status": ENABLED}


def adapters(node: Node) -> list[tuple[str, ...]]:
    """The node's MACs grouped by network adapter."""
    return list(batched(node["macs"], node["ports_per_adapter"]))


def add_functions(tree: Tree, adapter: str, macs: tuple[str, ...]) -> str:
    """The adapter's NetworkDeviceFunctions, one per MAC. Returns the collection's URI."""
    functions = [
        tree.add(
            f"{adapter}/NetworkDeviceFunctions/{port}",
            "#NetworkDeviceFunction.v1_9_0.NetworkDeviceFunction",
            {
                "Id": str(port),
                "Name": f"Port {port}",
                "NetDevFuncType": "Ethernet",
                "Ethernet": {"PermanentMACAddress": mac.upper(), "MACAddress": mac.upper()},
                "Status": ENABLED,
            },
        )
        for port, mac in enumerate(macs)
    ]
    return tree.add_collection(
        f"{adapter}/NetworkDeviceFunctions",
        "#NetworkDeviceFunctionCollection.NetworkDeviceFunctionCollection",
        "Network Device Functions",
        functions,
    )


def add_ports(tree: Tree, adapter: str, count: int) -> str:
    """The adapter's Ports, as bmcweb lists them: no MAC address. Returns the collection's URI."""
    ports = [
        tree.add(
            f"{adapter}/Ports/{port}",
            "#Port.v1_4_0.Port",
            {"Id": str(port), "Name": f"Port {port}", "Status": ENABLED},
        )
        for port in range(count)
    ]
    return tree.add_collection(f"{adapter}/Ports", "#PortCollection.PortCollection", "Ports", ports)


def add_chassis(tree: Tree, node: Node) -> None:
    adapter_uris = []
    for index, macs in enumerate(adapters(node)):
        adapter = f"{CHASSIS}/NetworkAdapters/NIC_{index}"
        if node.get("nic_ports_without_macs", False):
            nic_links = {"Ports": link(add_ports(tree, adapter, len(macs)))}
        else:
            nic_links = {"NetworkDeviceFunctions": link(add_functions(tree, adapter, macs))}
        adapter_uris.append(
            tree.add(
                adapter,
                "#NetworkAdapter.v1_9_0.NetworkAdapter",
                {
                    "Id": f"NIC_{index}",
                    "Name": f"Network Adapter {index}",
                    "Manufacturer": "NVIDIA",
                    "Model": "ConnectX-7",
                    **nic_links,
                    "Status": ENABLED,
                },
            )
        )
    tree.add_collection(
        f"{CHASSIS}/NetworkAdapters",
        "#NetworkAdapterCollection.NetworkAdapterCollection",
        "Network Adapters",
        adapter_uris,
    )
    tree.add(
        CHASSIS,
        "#Chassis.v1_23_0.Chassis",
        {
            "Id": "1",
            "Name": "Chassis",
            "ChassisType": "RackMount",
            "Manufacturer": node["manufacturer"],
            "Model": node["model"],
            "SerialNumber": node["serial"],
            "Status": ENABLED,
            "NetworkAdapters": link(f"{CHASSIS}/NetworkAdapters"),
            "Links": {"ComputerSystems": [link(SYSTEM)], "ManagedBy": [link(MANAGER)]},
        },
    )
    tree.add_collection(f"{ROOT}/Chassis", "#ChassisCollection.ChassisCollection", "Chassis", [CHASSIS])


def add_manager(tree: Tree, node: Node) -> None:
    tree.add(
        MANAGER,
        "#Manager.v1_17_0.Manager",
        {
            "Id": "BMC",
            "Name": "Manager",
            "ManagerType": "BMC",
            "FirmwareVersion": node["firmware"]["BMC"],
            "Status": ENABLED,
        },
    )
    tree.add_collection(f"{ROOT}/Managers", "#ManagerCollection.ManagerCollection", "Managers", [MANAGER])


def add_update_service(tree: Tree, node: Node) -> None:
    items = [
        tree.add(
            f"{FIRMWARE_INVENTORY}/{item_id}",
            "#SoftwareInventory.v1_9_0.SoftwareInventory",
            {"Id": item_id, "Name": item_id, "Version": version, "Updateable": True, "Status": ENABLED},
        )
        for item_id, version in firmware_items(node)
    ]
    tree.add_collection(
        FIRMWARE_INVENTORY,
        "#SoftwareInventoryCollection.SoftwareInventoryCollection",
        "Firmware Inventory",
        items,
    )
    tree.add(
        UPDATE_SERVICE,
        "#UpdateService.v1_11_0.UpdateService",
        {"Id": "UpdateService", "Name": "Update Service", "FirmwareInventory": link(FIRMWARE_INVENTORY)},
    )


def firmware_items(node: Node) -> list[tuple[str, str]]:
    """One item each for the BMC and BIOS, one per GPU and one per network adapter."""
    firmware = node["firmware"]
    return [
        ("BMC", firmware["BMC"]),
        ("BIOS", firmware["BIOS"]),
        *((f"GPU_{index}", firmware["GPU"]) for index in range(node["gpu_count"])),
        *((f"NIC_{index}", firmware["NIC"]) for index in range(len(adapters(node)))),
    ]


def write_tree(tree: dict[str, Resource], out_dir: Path) -> None:
    for uri, resource in tree.items():
        path = out_dir / uri.lstrip("/") / "index.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(resource, indent=2) + "\n")


def main(out_dir: Path) -> None:
    for name in node_names():
        write_tree(build_tree(actual(name)), out_dir / name)
        print(f"rendered {out_dir / name}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
