# racklint/tests/test_engine.py
import asyncio

from racklint.checks import SerialCheck
from racklint.credentials import CredentialError, Credentials, Identified
from racklint.engine import check_nodes, iter_results
from racklint.errors import AuthenticationError, InventoryError
from racklint.models import ActualNode, Finding, Kind, NodeResult, Status
from tests.factories import actual_node, credentials, expected_node, expected_switch


class FakeReader:
    """Answers from a dict of device address -> node, or -> error to raise, after a delay (default 10 ms)."""

    def __init__(
        self, nodes: dict[str, ActualNode | InventoryError], delays: dict[str, float] | None = None
    ) -> None:
        self._nodes = nodes
        self._delays = delays or {}
        self.in_flight = 0
        self.most_in_flight = 0
        self.read_with: dict[str, Credentials] = {}

    async def read(self, address: str, credentials: Credentials) -> ActualNode:
        self.read_with[address] = credentials
        self.in_flight += 1
        self.most_in_flight = max(self.most_in_flight, self.in_flight)
        await asyncio.sleep(self._delays.get(address, 0.01))
        self.in_flight -= 1
        result = self._nodes[address]
        if isinstance(result, InventoryError):
            raise result
        return result


class FakeCredentials:
    """A CredentialSource giving every device the same credentials, except the devices named in `missing`."""

    def __init__(self, *missing: str, login: Credentials | None = None) -> None:
        self._missing = missing
        self._login = login or credentials()
        self.asked: list[str] = []

    async def credentials_for(self, device: Identified) -> Credentials:
        self.asked.append(device.name)
        if device.name in self._missing:
            raise CredentialError(f"no credentials for {device.name}: set bmc-r02 not found in Vault")
        return self._login


class AlwaysFinds:
    name = "fake"

    def run(self, expected: object, actual: object) -> list[str]:
        return ["one", "two"]


class AlwaysNotes:
    name = "noted"

    def run(self, expected: object, actual: object) -> list[str]:
        return []

    def notes(self, expected: object, actual: object) -> list[str]:
        return ["first", "second"]


async def test_a_node_without_findings_passes() -> None:
    report = await check_nodes(
        [expected_node()],
        FakeReader({"192.0.2.11": actual_node()}),
        [SerialCheck()],
        FakeCredentials(),
        concurrency=4,
    )

    assert report.results == (
        NodeResult(node="n01", kind=Kind.SERVER, address="192.0.2.11", status=Status.PASS),
    )


async def test_findings_fail_the_node() -> None:
    report = await check_nodes(
        [expected_node()],
        FakeReader({"192.0.2.11": actual_node()}),
        [AlwaysFinds()],
        FakeCredentials(),
        concurrency=4,
    )

    assert report.results[0].status == Status.FAIL
    assert report.results[0].findings == (
        Finding(check="fake", message="one"),
        Finding(check="fake", message="two"),
    )


async def test_notes_are_on_the_result_and_the_node_still_passes() -> None:
    report = await check_nodes(
        [expected_node()],
        FakeReader({"192.0.2.11": actual_node()}),
        [SerialCheck(), AlwaysNotes()],
        FakeCredentials(),
        concurrency=4,
    )

    assert report.results == (
        NodeResult(
            node="n01",
            kind=Kind.SERVER,
            address="192.0.2.11",
            status=Status.PASS,
            notes=(Finding(check="noted", message="first"), Finding(check="noted", message="second")),
        ),
    )


async def test_a_failing_node_keeps_its_notes() -> None:
    report = await check_nodes(
        [expected_node()],
        FakeReader({"192.0.2.11": actual_node()}),
        [AlwaysFinds(), AlwaysNotes()],
        FakeCredentials(),
        concurrency=4,
    )

    assert report.results[0].status == Status.FAIL
    assert [note.message for note in report.results[0].notes] == ["first", "second"]


async def test_a_check_without_notes_adds_none() -> None:
    report = await check_nodes(
        [expected_node()],
        FakeReader({"192.0.2.11": actual_node()}),
        [AlwaysFinds()],
        FakeCredentials(),
        concurrency=4,
    )

    assert report.results[0].notes == ()


async def test_an_unreadable_bmc_is_an_error_and_no_check_runs() -> None:
    reader = FakeReader({"192.0.2.11": InventoryError("authentication failed")})

    report = await check_nodes([expected_node()], reader, [AlwaysFinds()], FakeCredentials(), concurrency=4)

    assert report.results == (
        NodeResult(
            node="n01",
            kind=Kind.SERVER,
            address="192.0.2.11",
            status=Status.ERROR,
            error="authentication failed",
        ),
    )


async def test_a_node_without_a_bmc_address_fails_without_being_read() -> None:
    report = await check_nodes(
        [expected_node(bmc_address=None)], FakeReader({}), [AlwaysFinds()], FakeCredentials(), concurrency=4
    )

    assert report.results == (
        NodeResult(
            node="n01",
            kind=Kind.SERVER,
            address=None,
            status=Status.FAIL,
            findings=(Finding(check="bmc", message="not planned in NetBox: BMC address"),),
        ),
    )


async def test_at_most_concurrency_bmcs_are_read_at_once_and_order_is_kept() -> None:
    nodes = [expected_node(name=f"n{index}", bmc_address=f"192.0.2.{index}") for index in range(1, 7)]
    reader = FakeReader({node.bmc_address: actual_node() for node in nodes if node.bmc_address})

    report = await check_nodes(nodes, reader, [SerialCheck()], FakeCredentials(), concurrency=2)

    assert reader.most_in_flight == 2
    assert [result.node for result in report.results] == [node.name for node in nodes]


async def test_iter_results_yields_each_result_as_soon_as_it_is_ready() -> None:
    slow = expected_node(name="slow", bmc_address="192.0.2.1")
    fast = expected_node(name="fast", bmc_address="192.0.2.2")
    reader = FakeReader({"192.0.2.1": actual_node(), "192.0.2.2": actual_node()}, delays={"192.0.2.1": 0.05})

    names = [
        result.node
        async for result in iter_results(
            [slow, fast], reader, [SerialCheck()], FakeCredentials(), concurrency=2
        )
    ]

    assert names == ["fast", "slow"]


async def test_a_switch_without_a_management_address_fails_without_being_read() -> None:
    report = await check_nodes(
        [expected_switch(address=None)], FakeReader({}), [AlwaysFinds()], FakeCredentials(), concurrency=4
    )

    assert report.results == (
        NodeResult(
            node="leaf1",
            kind=Kind.SWITCH,
            address=None,
            status=Status.FAIL,
            findings=(Finding(check="management", message="not planned in NetBox: management address"),),
        ),
    )


async def test_a_device_without_credentials_is_that_devices_error_and_the_others_are_read() -> None:
    nodes = [
        expected_node(name="n03", bmc_address="192.0.2.3"),
        expected_node(name="n04", bmc_address="192.0.2.4"),
    ]
    reader = FakeReader({"192.0.2.3": actual_node(), "192.0.2.4": actual_node()})

    report = await check_nodes(nodes, reader, [SerialCheck()], FakeCredentials("n04"), concurrency=4)

    assert report.results == (
        NodeResult(node="n03", kind=Kind.SERVER, address="192.0.2.3", status=Status.PASS),
        NodeResult(
            node="n04",
            kind=Kind.SERVER,
            address="192.0.2.4",
            status=Status.ERROR,
            error="no credentials for n04: set bmc-r02 not found in Vault",
        ),
    )
    assert list(reader.read_with) == ["192.0.2.3"]


async def test_the_reader_gets_the_credentials_looked_up_for_the_device() -> None:
    login = credentials(username="bmc-admin", origin="set bmc-dc1")
    reader = FakeReader({"192.0.2.11": actual_node()})

    await check_nodes([expected_node()], reader, [SerialCheck()], FakeCredentials(login=login), concurrency=4)

    assert reader.read_with == {"192.0.2.11": login}


async def test_a_device_without_an_address_is_not_looked_up() -> None:
    source = FakeCredentials()

    await check_nodes(
        [expected_node(bmc_address=None)], FakeReader({}), [SerialCheck()], source, concurrency=4
    )

    assert source.asked == []


async def test_a_refused_login_names_the_credentials_that_were_refused() -> None:
    reader = FakeReader({"192.0.2.11": AuthenticationError("authentication failed")})
    source = FakeCredentials(login=credentials(origin="set bmc-dc1"))

    report = await check_nodes([expected_node()], reader, [SerialCheck()], source, concurrency=4)

    assert report.results[0].status == Status.ERROR
    assert report.results[0].error == "authentication failed with the set bmc-dc1"


async def test_a_refused_factory_secret_says_to_remove_it_once_the_password_is_rotated() -> None:
    reader = FakeReader({"192.0.2.11": AuthenticationError("authentication failed")})
    login = credentials(origin="factory secret for serial SN-01", factory=True)

    source = FakeCredentials(login=login)

    report = await check_nodes([expected_node()], reader, [SerialCheck()], source, concurrency=4)

    assert report.results[0].error == (
        "authentication failed with the factory secret for serial SN-01; "
        "remove the factory secret once the device's password is rotated"
    )


async def test_an_error_that_is_not_a_refused_login_does_not_name_the_credentials() -> None:
    reader = FakeReader({"192.0.2.11": InventoryError("GET /redfish/v1 returned HTTP 500")})

    report = await check_nodes([expected_node()], reader, [SerialCheck()], FakeCredentials(), concurrency=4)

    assert report.results[0].error == "GET /redfish/v1 returned HTTP 500"
