# racklint/examples/no-hardware/test_example.py
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import seed_fabric
from mockups import build_tree, write_tree
from node_files import actual, device_context, node_names, planned
from racklint.models import ActualSwitch, LldpNeighbour
from seed import Vault, seed_vault
from wait_fabric import missing_interfaces, planned_interfaces


def links(value: Any) -> Iterator[str]:
    """Every @odata.id link below `value`; a resource's own @odata.id is not a link."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "@odata.id" and len(value) == 1:
                yield item
            else:
                yield from links(item)
    elif isinstance(value, list):
        for item in value:
            yield from links(item)


def test_there_are_six_nodes() -> None:
    assert node_names() == ["n01", "n02", "n03", "n04", "n05", "n06"]


@pytest.mark.parametrize("name", node_names())
def test_every_link_resolves(name: str) -> None:
    tree = build_tree(actual(name))

    assert set(links(list(tree.values()))) <= set(tree)


def test_actual_applies_the_planted_faults_and_planned_does_not() -> None:
    assert (planned("n03")["gpu_count"], actual("n03")["gpu_count"]) == (8, 7)
    assert actual("n05")["firmware"] == {
        "BMC": "1.3.2",
        "BIOS": "2.3.1",
        "GPU": "96.00.89.00.01",
        "NIC": "28.39.1002",
    }
    assert planned("n05")["firmware"]["BMC"] == "1.4.0"


def test_n02_reports_its_gpus_as_accelerators_and_n01_as_gpus() -> None:
    for name, kind in (("n01", "GPU"), ("n02", "Accelerator")):
        tree = build_tree(actual(name))

        gpus = [
            (resource["ProcessorType"], resource["Model"])
            for uri, resource in tree.items()
            if "/Processors/GPU_" in uri
        ]
        assert gpus == [(kind, planned(name)["gpu_model"])] * 8, name


@pytest.mark.parametrize(("name", "listed"), [("n01", "NetworkDeviceFunctions"), ("n02", "Ports")])
def test_n01_lists_its_nic_functions_and_n02_its_nic_ports(name: str, listed: str) -> None:
    tree = build_tree(actual(name))

    adapters = [
        resource for resource in tree.values() if resource["@odata.type"].startswith("#NetworkAdapter.")
    ]
    assert [{"NetworkDeviceFunctions", "Ports"} & adapter.keys() for adapter in adapters] == [{listed}] * 2


def test_n02s_nic_ports_report_no_mac() -> None:
    tree = build_tree(actual("n02"))

    ports = [resource for uri, resource in tree.items() if "/Ports/" in uri]
    assert len(ports) == 4
    assert not any("Ethernet" in port for port in ports)


def test_slots_beyond_the_dimm_count_are_absent() -> None:
    tree = build_tree(actual("n05"))

    assert tree["/redfish/v1/Systems/1/Memory/DIMM_30"]["Status"]["State"] == "Enabled"
    assert tree["/redfish/v1/Systems/1/Memory/DIMM_31"]["Status"] == {"State": "Absent"}


def test_the_tree_is_written_in_the_mockup_server_layout(tmp_path: Path) -> None:
    write_tree(build_tree(actual("n01")), tmp_path)

    root = json.loads((tmp_path / "redfish/v1/index.json").read_text())
    assert root["Systems"] == {"@odata.id": "/redfish/v1/Systems"}


def test_only_n04_names_a_credential_set() -> None:
    assert [name for name in node_names() if "credential_set" in planned(name)] == ["n04"]
    assert planned("n04")["credential_set"] == "bmc-r02"


def test_a_credential_set_goes_into_the_devices_own_config_context() -> None:
    assert device_context(planned("n04")) == {"racklint": {"credentials": "bmc-r02"}}
    assert device_context(planned("n01")) is None


def test_n02_names_the_openbmc_platform_in_its_config_context_and_n01_names_none() -> None:
    assert device_context(planned("n02")) == {"racklint": {"platform": "openbmc"}}
    assert device_context(planned("n01")) is None


def test_a_node_naming_a_credential_set_and_a_platform_gets_both() -> None:
    node = planned("n01") | {"credential_set": "bmc-r02", "platform": "openbmc"}

    assert device_context(node) == {"racklint": {"credentials": "bmc-r02", "platform": "openbmc"}}


def written_to_vault(seed_secrets: Callable[[Vault], None]) -> dict[str, dict[str, str]]:
    """What a seed function writes to Vault: each request's path and the secret in its body."""
    written: dict[str, dict[str, str]] = {}

    def accept(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Vault-Token"] == "root-token"
        written[request.url.path] = json.loads(request.content)["data"]
        return httpx.Response(200, json={})

    client = httpx.Client(
        base_url="http://vault.test",
        headers={"X-Vault-Token": "root-token"},
        transport=httpx.MockTransport(accept),
    )
    seed_secrets(Vault(client))
    return written


def test_the_seed_writes_the_default_server_set_and_n02s_factory_secret_but_not_bmc_r02() -> None:
    login = {"username": "admin", "password": "racklint-example"}

    assert written_to_vault(seed_vault) == {
        "/v1/secret/data/racklint/sets/default-server": login,
        "/v1/secret/data/racklint/factory/SN-R01-02": login,
    }


def test_the_fabric_seed_writes_the_default_switch_set() -> None:
    assert written_to_vault(seed_fabric.seed_vault) == {
        "/v1/secret/data/racklint/sets/default-switch": {"username": "admin", "password": "NokiaSrl1!"}
    }


def test_a_write_that_vault_refuses_stops_the_seed() -> None:
    client = httpx.Client(
        base_url="http://vault.test", transport=httpx.MockTransport(lambda request: httpx.Response(403))
    )

    with pytest.raises(httpx.HTTPStatusError):
        seed_vault(Vault(client))


def test_the_plan_cables_two_interfaces_on_each_leaf_and_six_on_the_spine() -> None:
    interfaces = planned_interfaces(seed_fabric.load_fabric())

    assert interfaces["leaf1"] == {"ethernet-1/1", "ethernet-1/2"}
    assert interfaces["spine1"] == {f"ethernet-1/{number}" for number in range(1, 7)}
    assert set(interfaces) == {"leaf1", "leaf2", "leaf3", "spine1"}


def seen_by(*neighbours: tuple[str, str]) -> ActualSwitch:
    """A switch with a neighbour on each (interface, neighbour's system name) given."""
    return ActualSwitch(
        neighbours=tuple(
            LldpNeighbour(interface=interface, system_name=system_name, port_id="ethernet-1/1")
            for interface, system_name in neighbours
        )
    )


def test_a_switch_that_sees_a_neighbour_on_every_planned_interface_is_not_missing_any() -> None:
    switch = seen_by(("ethernet-1/1", "spine1"), ("ethernet-1/2", "spine1"))

    assert missing_interfaces({"ethernet-1/1", "ethernet-1/2"}, switch) == set()


def test_an_interface_without_a_neighbour_is_missing() -> None:
    switch = seen_by(("ethernet-1/1", "spine1"))

    assert missing_interfaces({"ethernet-1/1", "ethernet-1/2"}, switch) == {"ethernet-1/2"}
    assert missing_interfaces({"ethernet-1/1"}, ActualSwitch()) == {"ethernet-1/1"}


def test_a_neighbour_that_is_not_the_planned_one_still_counts() -> None:
    """Wrong cabling is racklint's finding to report, so the wait must not hide it by waiting for the plan."""
    switch = seen_by(("ethernet-1/1", "leaf3"))

    assert missing_interfaces({"ethernet-1/1"}, switch) == set()
