# racklint/tests/test_platforms.py
import ast
import sys
from importlib import metadata
from pathlib import Path

import pytest

from racklint.checks import Check, Notes
from racklint.errors import RacklintError
from racklint.gnmi import GnmiAccess, GnmiReader, GnmiRequest, Update
from racklint.models import ActualNode, ExpectedNode, FirmwareItem, Kind, NetworkPort, Processor
from racklint.platforms import (
    DEFAULT_SERVER,
    DEFAULT_SWITCH,
    ENTRY_POINTS,
    Platforms,
    ServerPlatform,
    UnknownPlatform,
    load_platforms,
    sonic,
)
from racklint.platforms.openconfig import OPENCONFIG, SRLINUX, OpenConfigSwitch
from racklint.platforms.redfish import OPENBMC, REDFISH
from racklint.platforms.sonic import (
    CHASSIS_INFO,
    EEPROM_SERIAL,
    LLDP,
    OS_VERSION,
    SONIC,
    SONIC_DIALECT,
)
from racklint.redfish import BmcAccess, RedfishReader
from tests.factories import GPU_MODEL, FakeGnmiClient, actual_node, expected_node, gnmi_lldp


class AristaEos(OpenConfigSwitch):
    """A platform as another package would write it."""

    name = "arista-eos"
    gnmi_port = 6030


ARISTA_EOS = AristaEos()
NOT_A_PLATFORM = object()
THIS_MODULE = __name__


def entry_point(name: str, value: str) -> metadata.EntryPoint:
    return metadata.EntryPoint(name=name, value=value, group=ENTRY_POINTS)


def check_named(platform: ServerPlatform, name: str) -> Check[ExpectedNode, ActualNode]:
    [check] = [check for check in platform.checks() if check.name == name]
    return check


def gpu_problems(platform: ServerPlatform, processors: tuple[Processor, ...]) -> list[str]:
    """What the platform's GpuCheck finds wrong in a node planned with two GPUs that reports `processors`."""
    return check_named(platform, "gpu").run(expected_node(), actual_node(processors=processors))


def test_the_installed_entry_points_give_the_five_built_in_platforms() -> None:
    platforms = load_platforms()

    assert platforms.server(DEFAULT_SERVER) is REDFISH
    assert platforms.server("openbmc") is OPENBMC
    assert platforms.switch(DEFAULT_SWITCH) is OPENCONFIG
    assert platforms.switch("srlinux") is SRLINUX
    assert platforms.switch("sonic") is SONIC


def test_a_platform_from_another_package_is_found() -> None:
    platforms = load_platforms([entry_point("arista-eos", f"{THIS_MODULE}:ARISTA_EOS")])

    assert platforms.switch("arista-eos") is ARISTA_EOS


def test_two_entry_points_with_one_name_stop_the_load_and_are_both_named() -> None:
    claims = [
        entry_point("redfish", "racklint.platforms.redfish:REDFISH"),
        entry_point("redfish", f"{THIS_MODULE}:ARISTA_EOS"),
    ]

    with pytest.raises(RacklintError) as error:
        load_platforms(claims)

    assert str(error.value) == (
        "racklint platform 'redfish' is claimed twice: "
        f"racklint.platforms.redfish:REDFISH and {THIS_MODULE}:ARISTA_EOS"
    )


def test_a_platform_must_have_the_name_of_its_entry_point() -> None:
    with pytest.raises(RacklintError) as error:
        load_platforms([entry_point("eos", f"{THIS_MODULE}:ARISTA_EOS")])

    assert str(error.value) == (
        f"{ENTRY_POINTS} entry point 'eos' ({THIS_MODULE}:ARISTA_EOS) "
        "is a platform named 'arista-eos': the names must match"
    )


def test_an_entry_point_must_be_a_server_or_a_switch_platform() -> None:
    with pytest.raises(RacklintError) as error:
        load_platforms([entry_point("neither", f"{THIS_MODULE}:NOT_A_PLATFORM")])

    assert str(error.value) == (
        f"{ENTRY_POINTS} entry point 'neither' ({THIS_MODULE}:NOT_A_PLATFORM) "
        "is neither a server platform nor a switch platform"
    )


def test_an_unknown_name_says_how_to_fix_it() -> None:
    platforms = Platforms([REDFISH, OPENCONFIG])
    message = "no racklint platform 'arista-eos': install one, or remove racklint.platform"

    with pytest.raises(UnknownPlatform) as server:
        platforms.server("arista-eos")
    with pytest.raises(UnknownPlatform) as switch:
        platforms.switch("arista-eos")

    assert (str(server.value), str(switch.value)) == (message, message)


@pytest.mark.parametrize(
    ("kind", "name", "other"),
    [(Kind.SERVER, "srlinux", "switch"), (Kind.SWITCH, "openbmc", "server")],
)
def test_a_platform_of_the_other_kind_is_unknown_to_that_kind(kind: Kind, name: str, other: str) -> None:
    platforms = Platforms([OPENBMC, SRLINUX])
    ask = platforms.server if kind is Kind.SERVER else platforms.switch

    with pytest.raises(UnknownPlatform) as error:
        ask(name)

    assert str(error.value) == (
        f"racklint platform {name!r} is a {other} platform, not a {kind} platform: "
        "name another, or remove racklint.platform"
    )


def test_each_platform_reads_with_its_own_kind_of_reader() -> None:
    access = GnmiAccess(client=FakeGnmiClient({}))

    assert isinstance(REDFISH.reader(BmcAccess()), RedfishReader)
    assert isinstance(OPENBMC.reader(BmcAccess()), RedfishReader)
    assert isinstance(OPENCONFIG.reader(access), GnmiReader)
    assert isinstance(SRLINUX.reader(access), GnmiReader)
    assert isinstance(SONIC.reader(access), GnmiReader)


def test_the_openconfig_platforms_name_no_gnmi_port_of_their_own() -> None:
    assert (OPENCONFIG.gnmi_port, SRLINUX.gnmi_port) == (None, None)


def test_sonic_names_its_usual_gnmi_port() -> None:
    assert SONIC.gnmi_port == 8080


def accelerators(count: int) -> tuple[Processor, ...]:
    return tuple(
        Processor(id=f"accelerator{index}", type="Accelerator", model=GPU_MODEL) for index in range(count)
    )


def test_openbmc_counts_the_accelerators_of_the_planned_model_as_gpus() -> None:
    assert gpu_problems(OPENBMC, accelerators(2)) == []
    assert gpu_problems(OPENBMC, accelerators(1)) == ["1 found, 2 planned"]


def test_openbmc_does_not_count_a_processor_of_type_gpu() -> None:
    [problem] = gpu_problems(OPENBMC, actual_node().processors)

    assert problem.startswith("0 found, 2 planned")


def test_redfish_counts_both_the_gpus_and_the_accelerators() -> None:
    assert gpu_problems(REDFISH, actual_node().processors) == []
    assert gpu_problems(REDFISH, accelerators(2)) == []


def test_openbmc_judges_a_server_with_the_checks_of_the_default_platform() -> None:
    assert [check.name for check in OPENBMC.checks()] == [check.name for check in REDFISH.checks()]


def test_openbmc_counts_nic_ports_when_the_bmc_reports_no_macs_and_notes_it() -> None:
    nic = check_named(OPENBMC, "nic")
    actual = actual_node(ports=(NetworkPort(id="port0"), NetworkPort(id="port1")))

    assert nic.run(expected_node(), actual) == []
    assert isinstance(nic, Notes)
    assert nic.notes(expected_node(), actual) == ["MACs not checked: the BMC reports none"]


def test_redfish_fails_the_macs_that_a_bmc_does_not_report() -> None:
    actual = actual_node(ports=(NetworkPort(id="port0"), NetworkPort(id="port1")))

    assert check_named(REDFISH, "nic").run(expected_node(), actual) == ["eth0 (02:00:00:00:01:01) not found"]


def test_sonic_judges_a_switch_with_the_checks_of_the_default_platform() -> None:
    assert [check.name for check in SONIC.checks()] == [check.name for check in OPENCONFIG.checks()]


def imported_modules(path: Path) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules.append("." * node.level + (node.module or ""))
    return modules


def test_sonic_imports_only_racklint_and_the_standard_library() -> None:
    source = Path(sonic.__file__)

    foreign = [
        module
        for module in imported_modules(source)
        if module != "racklint" and module.split(".")[0] not in sys.stdlib_module_names
    ]

    assert foreign == []


def native(**fields: str) -> list[Update]:
    """What a SONiC database or file answers: one update whose value is an object of strings."""
    return [{"path": "table/key", "val": fields}]


def sonic_replies(
    chassis: list[Update], eeprom: list[Update], os_version: list[Update] | None = None
) -> dict[GnmiRequest, list[Update]]:
    return {
        LLDP: gnmi_lldp([]),
        CHASSIS_INFO: chassis,
        EEPROM_SERIAL: eeprom,
        OS_VERSION: os_version if os_version is not None else native(build_version="SONiC.1"),
    }


@pytest.mark.parametrize(
    ("chassis", "eeprom", "serials"),
    [
        pytest.param(native(serial="SN-A"), native(Value="SN-B"), ("SN-A",), id="both"),
        pytest.param(native(serial="N/A"), native(Value="SN-B"), ("SN-B",), id="chassis serial is N/A"),
        pytest.param(native(serial=""), native(Value="SN-B"), ("SN-B",), id="chassis serial is empty"),
        pytest.param(native(model="EX"), native(Value="SN-B"), ("SN-B",), id="chassis has no serial field"),
        pytest.param([{"path": "x", "val": None}], native(Value="SN-B"), ("SN-B",), id="chassis is empty"),
        pytest.param([], native(Value="SN-B"), ("SN-B",), id="no chassis info"),
        pytest.param([], native(Name="Serial Number"), (), id="eeprom has no value field"),
        pytest.param(native(serial="N/A"), native(Value="N/A"), (), id="N/A in both"),
        pytest.param([], [], (), id="neither"),
    ],
)
def test_sonic_takes_the_serial_from_the_chassis_unless_it_reports_none_and_then_the_eeprom(
    chassis: list[Update], eeprom: list[Update], serials: tuple[str, ...]
) -> None:
    assert SONIC_DIALECT.parse(sonic_replies(chassis, eeprom)).serials == serials


def test_sonic_reports_the_build_version_as_the_firmware_item_os() -> None:
    switch = SONIC_DIALECT.parse(sonic_replies([], [], native(build_version="SONiC.202411.1", error="")))

    assert switch.firmware == (FirmwareItem(id="OS", version="SONiC.202411.1"),)


@pytest.mark.parametrize(
    "os_version",
    [
        [],
        native(error="no such file"),
        native(build_version=""),
        native(build_version="sonic.NA", error="no"),
    ],
)
def test_sonic_reports_no_os_when_the_switch_gives_no_build_version(os_version: list[Update]) -> None:
    assert SONIC_DIALECT.parse(sonic_replies([], [], os_version)).firmware == ()
