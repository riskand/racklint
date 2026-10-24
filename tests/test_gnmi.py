# racklint/tests/test_gnmi.py
import builtins
import sys
from collections.abc import Callable, Mapping
from types import ModuleType, SimpleNamespace
from typing import Any, NamedTuple, Self

import pytest

from racklint.errors import AuthenticationError, InventoryError, RacklintError
from racklint.gnmi import (
    COMPONENTS,
    LLDP,
    OPENCONFIG,
    GnmiAccess,
    GnmiAuthError,
    GnmiError,
    GnmiNotFound,
    GnmiReader,
    GnmiRequest,
    PygnmiClient,
    Update,
    openconfig_components,
    openconfig_neighbours,
    parse_switch,
)
from racklint.models import ActualSwitch, FirmwareItem, LldpNeighbour
from racklint.retry import ATTEMPTS
from tests.factories import (
    PASSWORD,
    SWITCH_OS,
    FakeGnmiClient,
    actual_switch,
    credentials,
    gnmi_components,
    gnmi_lldp,
)

ACCESS = GnmiAccess(verify_tls=False)
NEIGHBOURS = [
    ("ethernet-1/1", "spine1", "ethernet-1/1"),
    ("ethernet-1/2", "spine1", "ethernet-1/2"),
    ("mgmt0", "leaf2", "mgmt0"),
]


A = GnmiRequest("/a")
B = GnmiRequest("/b")
NATIVE = GnmiRequest("table/key", target="DB")
OTHER_NATIVE = GnmiRequest("table/other", target="DB")
MAYBE = GnmiRequest("table/maybe", target="DB", optional=True)


class Get(NamedTuple):
    """A Get as pygnmi's client is called."""

    paths: list[str]
    target: str | None
    encoding: str = "json_ietf"


def replies(components: list[dict[str, Any]] | None = None) -> dict[GnmiRequest, list[dict[str, Any]]]:
    return {COMPONENTS: components or gnmi_components(), LLDP: gnmi_lldp(NEIGHBOURS)}


def get_response(*paths: str) -> dict[str, Any]:
    """A GetResponse with a notification for each path, holding one update whose value is the path."""
    return {"notification": [{"update": [{"path": path, "val": path}]} for path in paths]}


def update_of(path: str) -> list[Update]:
    return [{"path": path, "val": path}]


def pygnmi_error(status: str, details: str) -> Exception:
    """An error as pygnmi raises it: its own exception, keeping gRPC's error and its status as `orig_exc`."""

    class Grpc(Exception):
        def code(self) -> SimpleNamespace:
            return SimpleNamespace(name=status)

    class Wrapped(Exception):
        def __init__(self) -> None:
            super().__init__(f"GRPC ERROR Host: 198.51.100.11:57400, Error: {details}")
            self.orig_exc = Grpc()

    return Wrapped()


def install_pygnmi(monkeypatch: pytest.MonkeyPatch, client: type) -> None:
    """Make `from pygnmi.client import gNMIclient` give `client`."""
    client_module = ModuleType("pygnmi.client")
    monkeypatch.setattr(client_module, "gNMIclient", client, raising=False)
    monkeypatch.setitem(sys.modules, "pygnmi", ModuleType("pygnmi"))
    monkeypatch.setitem(sys.modules, "pygnmi.client", client_module)


def pygnmi_answering(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[list[str], str | None], dict[str, Any]]
) -> list[Get]:
    """Install a pygnmi whose Get returns `answer(paths, target)`; return the Gets it is asked, in order."""
    gets: list[Get] = []

    class Client:
        def __init__(self, **options: Any) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *details: object) -> None:
            pass

        def get(self, path: list[str], target: str | None, encoding: str) -> dict[str, Any]:
            gets.append(Get(path, target, encoding))
            return answer(path, target)

    install_pygnmi(monkeypatch, Client)
    return gets


async def test_reads_serial_os_and_lldp_neighbours() -> None:
    client = FakeGnmiClient(replies())

    switch = await GnmiReader(ACCESS, client).read("198.51.100.11", credentials())

    assert switch == actual_switch()
    assert client.calls == [("198.51.100.11", (COMPONENTS, LLDP))]


async def test_reads_through_the_client_of_the_access_settings() -> None:
    client = FakeGnmiClient(replies())

    switch = await GnmiReader(GnmiAccess(verify_tls=False, client=client)).read(
        "198.51.100.11", credentials()
    )

    assert switch == actual_switch()
    assert client.calls == [("198.51.100.11", (COMPONENTS, LLDP))]


async def test_a_client_given_to_the_reader_wins_over_the_access_settings() -> None:
    from_access, given = FakeGnmiClient(replies()), FakeGnmiClient(replies())

    await GnmiReader(GnmiAccess(verify_tls=False, client=from_access), given).read(
        "198.51.100.11", credentials()
    )

    assert (len(from_access.calls), len(given.calls)) == (0, 1)


async def test_each_read_uses_the_credentials_it_is_given() -> None:
    client = FakeGnmiClient(replies())
    reader = GnmiReader(ACCESS, client)

    await reader.read("198.51.100.11", credentials(username="leaf-admin"))
    await reader.read("198.51.100.12", credentials(username="spine-admin"))

    assert client.usernames == ["leaf-admin", "spine-admin"]


def test_an_operating_system_component_wins_over_the_controller_card() -> None:
    operating_system = {
        "name": "OS",
        "state": {"type": "openconfig-platform-types:OPERATING_SYSTEM", "software-version": "9.1"},
    }

    switch = parse_switch(gnmi_components(extra=[operating_system]), [])

    assert switch.firmware == (FirmwareItem(id="OS", version="9.1"),)


def test_a_switch_reporting_no_version_reports_no_os() -> None:
    assert parse_switch(gnmi_components(os_version=""), []).firmware == ()


def test_neighbours_are_read_from_every_interface() -> None:
    switch = parse_switch([], gnmi_lldp([("ethernet-1/1", "spine1.example.net", "ethernet-1/7")]))

    assert switch.neighbours == (
        LldpNeighbour(interface="ethernet-1/1", system_name="spine1.example.net", port_id="ethernet-1/7"),
    )


async def test_a_switch_that_is_still_starting_is_read_on_a_later_try() -> None:
    client = FakeGnmiClient(GnmiError("gNMI request to 198.51.100.11 failed: connection refused"), replies())

    switch = await GnmiReader(ACCESS, client, backoff=0).read("198.51.100.11", credentials())

    assert switch == actual_switch()
    assert len(client.calls) == 2


async def test_a_switch_that_never_answers_is_an_inventory_error_after_every_try() -> None:
    client = FakeGnmiClient(GnmiError("gNMI request to 198.51.100.11 failed: timeout"))

    with pytest.raises(InventoryError, match="timeout"):
        await GnmiReader(ACCESS, client, backoff=0).read("198.51.100.11", credentials())
    assert len(client.calls) == ATTEMPTS


async def test_a_refused_login_is_not_tried_again() -> None:
    client = FakeGnmiClient(GnmiAuthError("gNMI request to 198.51.100.11 failed: authentication failed"))

    with pytest.raises(GnmiAuthError):
        await GnmiReader(ACCESS, client, backoff=0).read("198.51.100.11", credentials())
    assert len(client.calls) == 1


async def test_a_path_without_data_is_not_tried_again() -> None:
    client = FakeGnmiClient(GnmiNotFound("gNMI request to 198.51.100.11 failed: no such key"))

    with pytest.raises(GnmiNotFound):
        await GnmiReader(ACCESS, client, backoff=0).read("198.51.100.11", credentials())
    assert len(client.calls) == 1


async def test_a_reader_asks_for_the_requests_of_its_dialect_and_parses_with_them() -> None:
    class Dialect:
        requests = (A, B)

        def parse(self, replies: Mapping[GnmiRequest, list[Update]]) -> ActualSwitch:
            return ActualSwitch(serials=(replies[A][0]["val"], replies[B][0]["val"]))

    client = FakeGnmiClient({A: update_of("serial-a"), B: update_of("serial-b")})

    switch = await GnmiReader(ACCESS, client, dialect=Dialect()).read("198.51.100.11", credentials())

    assert switch.serials == ("serial-a", "serial-b")
    assert client.calls == [("198.51.100.11", (A, B))]


async def test_an_unexpected_payload_is_an_inventory_error() -> None:
    # An interface without its name.
    nameless = {"neighbors": {"neighbor": [{"state": {"system-name": "spine1"}}]}}
    lldp = [{"path": "x", "val": {"interface": [nameless]}}]
    reader = GnmiReader(ACCESS, FakeGnmiClient({COMPONENTS: [], LLDP: lldp}))

    with pytest.raises(GnmiError, match="unexpected gNMI payload"):
        await reader.read("198.51.100.11", credentials())


def test_pygnmi_is_required_only_when_switches_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def without_pygnmi(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("pygnmi"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_pygnmi)

    with pytest.raises(RacklintError, match=r"racklint\[network\]"):
        PygnmiClient(ACCESS)


def test_verifying_tls_needs_a_ca_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pygnmi", ModuleType("pygnmi"))
    monkeypatch.setitem(sys.modules, "pygnmi.client", ModuleType("pygnmi.client"))

    with pytest.raises(RacklintError, match="CA bundle"):
        PygnmiClient(GnmiAccess())


def test_a_grpc_unauthenticated_status_is_a_refused_login(monkeypatch: pytest.MonkeyPatch) -> None:
    error = pygnmi_error("UNAUTHENTICATED", "authentication failed")

    class RefusingClient:
        def __init__(self, **options: Any) -> None:
            pass

        def __enter__(self) -> Any:
            raise error

        def __exit__(self, *details: object) -> None:
            pass

    install_pygnmi(monkeypatch, RefusingClient)

    with pytest.raises(GnmiAuthError, match="authentication failed"):
        PygnmiClient(ACCESS).get("198.51.100.11", [COMPONENTS], credentials())


def test_pygnmi_logs_in_with_the_credentials_of_the_read(monkeypatch: pytest.MonkeyPatch) -> None:
    options: dict[str, Any] = {}

    class RecordingClient:
        def __init__(self, **given: Any) -> None:
            options.update(given)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *details: object) -> None:
            pass

        def get(self, path: list[str], target: str | None, encoding: str) -> dict[str, Any]:
            return {"notification": []}

    install_pygnmi(monkeypatch, RecordingClient)

    PygnmiClient(ACCESS).get("198.51.100.11", [COMPONENTS], credentials(username="leaf-admin"))

    assert options["username"] == "leaf-admin"
    assert options["password"] == PASSWORD


def test_each_request_is_sent_in_a_get_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    gets = pygnmi_answering(monkeypatch, lambda paths, target: get_response(*paths))

    updates = PygnmiClient(ACCESS).get("198.51.100.11", [A, NATIVE, B, MAYBE], credentials())

    assert gets == [
        Get(["/a"], None),
        Get(["table/key"], "DB"),
        Get(["/b"], None),
        Get(["table/maybe"], "DB"),
    ]
    assert updates == {
        A: update_of("/a"),
        NATIVE: update_of("table/key"),
        B: update_of("/b"),
        MAYBE: update_of("table/maybe"),
    }


def test_requests_with_one_target_are_two_gets(monkeypatch: pytest.MonkeyPatch) -> None:
    gets = pygnmi_answering(monkeypatch, lambda paths, target: get_response(*paths))

    PygnmiClient(ACCESS).get("198.51.100.11", [NATIVE, OTHER_NATIVE], credentials())

    assert gets == [Get(["table/key"], "DB"), Get(["table/other"], "DB")]


def test_an_optional_request_the_switch_has_no_data_for_gives_no_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(paths: list[str], target: str | None) -> dict[str, Any]:
        if paths == [MAYBE.path]:
            raise pygnmi_error("NOT_FOUND", "no such key")
        return get_response(*paths)

    pygnmi_answering(monkeypatch, answer)

    updates = PygnmiClient(ACCESS).get("198.51.100.11", [NATIVE, MAYBE], credentials())

    assert updates == {NATIVE: update_of("table/key"), MAYBE: []}


def test_a_required_request_the_switch_has_no_data_for_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def answer(paths: list[str], target: str | None) -> dict[str, Any]:
        raise pygnmi_error("NOT_FOUND", "no such key")

    pygnmi_answering(monkeypatch, answer)

    with pytest.raises(GnmiNotFound, match="no such key"):
        PygnmiClient(ACCESS).get("198.51.100.11", [NATIVE], credentials())


@pytest.mark.parametrize(
    ("status", "failure"),
    [("PERMISSION_DENIED", GnmiAuthError), ("UNAVAILABLE", GnmiError)],
)
def test_an_optional_request_fails_like_any_other_but_for_not_found(
    monkeypatch: pytest.MonkeyPatch, status: str, failure: type[GnmiError]
) -> None:
    def answer(paths: list[str], target: str | None) -> dict[str, Any]:
        raise pygnmi_error(status, "refused")

    pygnmi_answering(monkeypatch, answer)

    with pytest.raises(failure) as error:
        PygnmiClient(ACCESS).get("198.51.100.11", [MAYBE], credentials())
    assert type(error.value) is failure


def test_a_get_may_return_no_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    pygnmi_answering(monkeypatch, lambda paths, target: {})

    assert PygnmiClient(ACCESS).get("198.51.100.11", [A], credentials()) == {A: []}


def test_a_refused_gnmi_login_is_an_authentication_error() -> None:
    assert issubclass(GnmiAuthError, AuthenticationError)


def test_the_os_version_is_reported_as_the_firmware_item_os() -> None:
    assert parse_switch(gnmi_components(), []).firmware == (FirmwareItem(id="OS", version=SWITCH_OS),)


def test_the_openconfig_dialect_asks_for_the_components_and_the_lldp_interfaces() -> None:
    assert OPENCONFIG.requests == (GnmiRequest("/components"), GnmiRequest("/lldp/interfaces"))


def test_the_openconfig_dialect_parses_what_parse_switch_does() -> None:
    assert OPENCONFIG.parse(replies()) == parse_switch(gnmi_components(), gnmi_lldp(NEIGHBOURS))


def test_the_components_helper_gives_the_chassis_serials_and_the_os_version() -> None:
    assert openconfig_components(gnmi_components()) == (("SN-SW-01",), SWITCH_OS)
    assert openconfig_components([]) == ((), None)


def test_the_neighbours_helper_gives_the_neighbours_of_every_interface() -> None:
    neighbours = list(openconfig_neighbours(gnmi_lldp(NEIGHBOURS)))

    assert [(n.interface, n.system_name, n.port_id) for n in neighbours] == NEIGHBOURS
