# racklint/tests/test_runner.py
from dataclasses import dataclass, field
from typing import Literal

import pytest

from racklint.checks import Check, SerialCheck
from racklint.credentials import CredentialLookup, Credentials, StaticStore
from racklint.errors import RacklintError
from racklint.gnmi import GnmiAccess
from racklint.models import ActualNode, ActualSwitch, ExpectedNode, ExpectedSwitch, Kind, Status
from racklint.platforms import Platforms
from racklint.redfish import BmcAccess
from racklint.runner import check_plan
from tests.factories import actual_node, actual_switch, credentials, expected_node, expected_switch

type Plan = list[ExpectedNode | ExpectedSwitch]

BMC = BmcAccess()
GNMI = GnmiAccess(port=3333)
LOOKUP = CredentialLookup(StaticStore({"default-server": credentials(), "default-switch": credentials()}))


class FakeReader[A]:
    """Reports the same device for every address, and remembers which addresses it was asked for."""

    def __init__(self, actual: A) -> None:
        self._actual = actual
        self.addresses: list[str] = []

    async def read(self, address: str, credentials: Credentials) -> A:
        self.addresses.append(address)
        return self._actual


class FakeServers:
    """A server platform whose readers report the planned server."""

    kind: Literal[Kind.SERVER] = Kind.SERVER

    def __init__(self, name: str) -> None:
        self.name = name
        self.readers: list[FakeReader[ActualNode]] = []

    def reader(self, access: BmcAccess) -> FakeReader[ActualNode]:
        self.readers.append(FakeReader(actual_node()))
        return self.readers[-1]

    def checks(self) -> list[Check[ExpectedNode, ActualNode]]:
        return [SerialCheck()]


class FakeSwitches:
    """A switch platform whose readers report the planned switch, with the port each was built for."""

    kind: Literal[Kind.SWITCH] = Kind.SWITCH

    def __init__(self, name: str, gnmi_port: int | None = None) -> None:
        self.name = name
        self.gnmi_port = gnmi_port
        self.readers: list[FakeReader[ActualSwitch]] = []
        self.ports: list[int] = []

    def reader(self, access: GnmiAccess) -> FakeReader[ActualSwitch]:
        self.readers.append(FakeReader(actual_switch()))
        self.ports.append(access.port)
        return self.readers[-1]

    def checks(self) -> list[Check[ExpectedSwitch, ActualSwitch]]:
        return [SerialCheck()]


@dataclass(eq=True)
class UnhashableServers:
    """A platform written as a plain dataclass, which is not hashable."""

    name: str
    kind: Literal[Kind.SERVER] = Kind.SERVER
    readers: list[FakeReader[ActualNode]] = field(default_factory=list)

    def reader(self, access: BmcAccess) -> FakeReader[ActualNode]:
        self.readers.append(FakeReader(actual_node()))
        return self.readers[-1]

    def checks(self) -> list[Check[ExpectedNode, ActualNode]]:
        return [SerialCheck()]


def server(name: str, platform: str | None = None) -> ExpectedNode:
    """The server n01 at 192.0.2.11, n02 at 192.0.2.12 and so on."""
    return expected_node(name=name, platform=platform, bmc_address=f"192.0.2.{10 + int(name[1:])}")


def switch(name: str, platform: str | None = None, gnmi_port: int | None = None) -> ExpectedSwitch:
    """The switch leaf1 at 198.51.100.11, leaf2 at 198.51.100.12 and so on."""
    return expected_switch(
        name=name, platform=platform, gnmi_port=gnmi_port, address=f"198.51.100.{10 + int(name[4:])}"
    )


async def test_a_device_naming_no_platform_is_read_by_the_default_platform_of_its_kind() -> None:
    redfish, openconfig = FakeServers("redfish"), FakeSwitches("openconfig")
    platforms = Platforms([redfish, openconfig, FakeServers("openbmc")])

    report = await check_plan([server("n01"), switch("leaf1")], platforms, LOOKUP, BMC, GNMI, concurrency=4)

    assert [result.status for result in report.results] == [Status.PASS, Status.PASS]
    assert [reader.addresses for reader in redfish.readers] == [["192.0.2.11"]]
    assert [reader.addresses for reader in openconfig.readers] == [["198.51.100.11"]]


async def test_a_device_is_read_by_the_platform_it_names() -> None:
    redfish, openbmc = FakeServers("redfish"), FakeServers("openbmc")
    srlinux = FakeSwitches("srlinux")
    platforms = Platforms([redfish, openbmc, FakeSwitches("openconfig"), srlinux])
    nodes: Plan = [server("n01", "openbmc"), switch("leaf1", "srlinux")]

    report = await check_plan(nodes, platforms, LOOKUP, BMC, GNMI, concurrency=4)

    assert report.passed
    assert (len(redfish.readers), len(openbmc.readers), len(srlinux.readers)) == (0, 1, 1)


async def test_a_device_with_a_platform_that_is_not_installed_is_its_error_and_the_others_are_read() -> None:
    redfish = FakeServers("redfish")
    nodes: Plan = [server("n01"), server("n02", "arista-eos"), server("n03")]

    report = await check_plan(nodes, Platforms([redfish]), LOOKUP, BMC, None, concurrency=4)

    assert [result.status for result in report.results] == [Status.PASS, Status.ERROR, Status.PASS]
    assert (
        report.results[1].error
        == "no racklint platform 'arista-eos': install one, or remove racklint.platform"
    )
    assert report.results[1].address == "192.0.2.12"
    assert redfish.readers[0].addresses == ["192.0.2.11", "192.0.2.13"]


async def test_a_platform_of_the_other_kind_is_that_devices_error() -> None:
    platforms = Platforms([FakeServers("redfish"), FakeSwitches("openconfig"), FakeSwitches("srlinux")])
    nodes: Plan = [server("n01", "srlinux"), switch("leaf1", "redfish"), switch("leaf2")]

    report = await check_plan(nodes, platforms, LOOKUP, BMC, GNMI, concurrency=4)

    assert [result.status for result in report.results] == [Status.ERROR, Status.ERROR, Status.PASS]
    assert [result.error for result in report.results[:2]] == [
        "racklint platform 'srlinux' is a switch platform, not a server platform: "
        "name another, or remove racklint.platform",
        "racklint platform 'redfish' is a server platform, not a switch platform: "
        "name another, or remove racklint.platform",
    ]


async def test_a_platform_need_not_be_hashable() -> None:
    redfish = UnhashableServers("redfish")
    nodes: Plan = [server("n01"), server("n02")]

    report = await check_plan(nodes, Platforms([redfish]), LOOKUP, BMC, None, concurrency=4)

    assert report.passed
    assert [reader.addresses for reader in redfish.readers] == [["192.0.2.11", "192.0.2.12"]]


async def test_devices_of_one_platform_share_a_reader() -> None:
    redfish = FakeServers("redfish")
    nodes: Plan = [server("n01"), server("n02"), server("n03")]

    await check_plan(nodes, Platforms([redfish]), LOOKUP, BMC, None, concurrency=4)

    assert [reader.addresses for reader in redfish.readers] == [["192.0.2.11", "192.0.2.12", "192.0.2.13"]]


async def test_switches_are_read_on_the_port_of_the_device_then_of_the_platform_then_of_the_setting() -> None:
    sonic, openconfig = FakeSwitches("sonic", gnmi_port=8080), FakeSwitches("openconfig")
    nodes: Plan = [
        switch("leaf1", "sonic", gnmi_port=9999),
        switch("leaf2", "sonic"),
        switch("leaf3", "sonic"),
        switch("leaf4"),
    ]

    await check_plan(nodes, Platforms([sonic, openconfig]), LOOKUP, None, GNMI, concurrency=4)

    assert sonic.ports == [9999, 8080]
    assert [reader.addresses for reader in sonic.readers] == [
        ["198.51.100.11"],
        ["198.51.100.12", "198.51.100.13"],
    ]
    assert openconfig.ports == [3333]


async def test_results_keep_the_order_of_the_plan() -> None:
    platforms = Platforms([FakeServers("redfish"), FakeServers("openbmc"), FakeSwitches("openconfig")])
    nodes: Plan = [
        switch("leaf1"),
        server("n01", "openbmc"),
        server("n02", "arista-eos"),
        server("n03"),
        switch("leaf2"),
        server("n04", "openbmc"),
    ]

    report = await check_plan(nodes, platforms, LOOKUP, BMC, GNMI, concurrency=4)

    assert [result.node for result in report.results] == [node.name for node in nodes]


async def test_an_empty_plan_gives_an_empty_report() -> None:
    assert (await check_plan([], Platforms([]), LOOKUP, None, None, concurrency=4)).results == ()


@pytest.mark.parametrize(
    ("bmc", "gnmi", "devices"),
    [(None, GNMI, "servers"), (BMC, None, "switches")],
)
async def test_a_plan_with_a_kind_that_has_no_access_settings_is_an_error_before_anything_is_read(
    bmc: BmcAccess | None, gnmi: GnmiAccess | None, devices: str
) -> None:
    redfish = FakeServers("redfish")
    platforms = Platforms([redfish, FakeSwitches("openconfig")])

    with pytest.raises(RacklintError, match=f"the plan has {devices}"):
        await check_plan([server("n01"), switch("leaf1")], platforms, LOOKUP, bmc, gnmi, concurrency=4)

    assert all(reader.addresses == [] for reader in redfish.readers)
