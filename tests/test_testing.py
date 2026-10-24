# racklint/tests/test_testing.py
import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

import httpx
import pytest

from racklint.checks import Notes
from racklint.gnmi import COMPONENTS, LLDP, GnmiNotFound, GnmiRequest
from racklint.models import (
    ActualNode,
    ActualSwitch,
    Dimm,
    ExpectedNode,
    FirmwareItem,
    GpuSpec,
    HardwareSpec,
    LldpNeighbour,
    MemorySpec,
    NetworkPort,
    PlannedInterface,
    Processor,
)
from racklint.platforms.openconfig import OPENCONFIG, SRLINUX, OpenConfigSwitch
from racklint.platforms.redfish import OPENBMC, REDFISH
from racklint.platforms.sonic import CHASSIS_INFO, EEPROM_SERIAL, OS_VERSION, SONIC
from racklint.platforms.sonic import LLDP as SONIC_LLDP
from racklint.testing import (
    ADDRESS,
    LOGIN,
    load_mockup,
    read_recorded_server,
    read_recorded_switch,
    recorded_bmc,
    recorded_switch,
)
from tests.factories import GPU_MODEL, actual_node, actual_switch, bmc_tree

RECORDINGS = Path(__file__).parent / "recordings"


def srlinux_responses() -> dict[GnmiRequest, dict[str, Any]]:
    directory = RECORDINGS / "srlinux"
    return {
        COMPONENTS: json.loads((directory / "components.json").read_text()),
        LLDP: json.loads((directory / "lldp-interfaces.json").read_text()),
    }


def sonic_responses(
    chassis: str = "chassis-info",
    os_version: str = "osversion-build",
    without: Collection[GnmiRequest] = (),
) -> dict[GnmiRequest, dict[str, Any]]:
    """The SONiC recording: `chassis` and `os_version` name those requests' files; `without` has no data."""
    files = {
        SONIC_LLDP: "lldp-interfaces",
        CHASSIS_INFO: chassis,
        EEPROM_SERIAL: "eeprom-info",
        OS_VERSION: os_version,
    }
    directory = RECORDINGS / "sonic"
    return {
        request: json.loads((directory / f"{name}.json").read_text())
        for request, name in files.items()
        if request not in without
    }


def sonic_switch(serials: tuple[str, ...], os_version: str | None = "SONiC.202411.example-1") -> ActualSwitch:
    return ActualSwitch(
        serials=serials,
        firmware=(FirmwareItem(id="OS", version=os_version),) if os_version else (),
        # The recording's LLDP table also holds eth0, the management port, which the platform leaves out.
        neighbours=(
            LldpNeighbour(
                interface="Ethernet0",
                system_name="spine1",
                port_id="fortyGigE0/0",
                port_description="Ethernet0",
            ),
            LldpNeighbour(
                interface="Ethernet4",
                system_name="spine1",
                port_id="fortyGigE0/4",
                port_description="Ethernet4",
            ),
        ),
    )


async def test_the_redfish_platform_reads_its_recording() -> None:
    assert await read_recorded_server(REDFISH, bmc_tree()) == actual_node()


async def test_the_openbmc_platform_reads_its_recording() -> None:
    node = await read_recorded_server(OPENBMC, load_mockup(RECORDINGS / "openbmc"))

    assert node == ActualNode(
        serials=("SN-OPENBMC-0001",),
        processors=(
            *(
                Processor(id=f"cpu{index}", type="CPU", model="Intel(R) Xeon(R) Platinum 8480+")
                for index in range(2)
            ),
            *(Processor(id=f"gpu{index}", type="Accelerator", model=GPU_MODEL) for index in range(8)),
        ),
        dimms=(Dimm(id="dimm0", capacity_mib=65536), Dimm(id="dimm1", capacity_mib=65536)),
        ports=(NetworkPort(id="port0"), NetworkPort(id="port1")),
        firmware=(FirmwareItem(id="3f2a9c1b", version="2.14.0-dev-1243-g7c1d1b0"),),
    )


async def test_the_openbmc_gpu_check_passes_on_its_recording_against_a_plan_of_eight_gpus() -> None:
    node = await read_recorded_server(OPENBMC, load_mockup(RECORDINGS / "openbmc"))
    planned = ExpectedNode(
        name="n02",
        device_type="gpu-node-h100",
        spec=HardwareSpec(
            gpu=GpuSpec(count=8, model=GPU_MODEL), memory=MemorySpec(dimm_count=2, total_gib=128)
        ),
    )
    [gpu] = [check for check in OPENBMC.checks() if check.name == "gpu"]

    assert gpu.run(planned, node) == []


async def test_the_openbmc_nic_check_passes_on_its_recording_with_a_note_that_the_macs_were_not_checked() -> (
    None
):
    node = await read_recorded_server(OPENBMC, load_mockup(RECORDINGS / "openbmc"))
    planned = ExpectedNode(
        name="n02",
        device_type="gpu-node-h100",
        interfaces=(
            PlannedInterface(name="eth0", mac="02:00:00:00:02:01"),
            PlannedInterface(name="eth1", mac="02:00:00:00:02:02"),
        ),
    )
    [nic] = [check for check in OPENBMC.checks() if check.name == "nic"]

    assert nic.run(planned, node) == []
    assert isinstance(nic, Notes)
    assert nic.notes(planned, node) == ["MACs not checked: the BMC reports none"]


@pytest.mark.parametrize("platform", [OPENCONFIG, SRLINUX])
async def test_the_switch_platforms_read_the_srlinux_recording(platform: OpenConfigSwitch) -> None:
    assert await read_recorded_switch(platform, srlinux_responses()) == actual_switch()


async def test_the_sonic_platform_reads_a_switch_with_both_serial_sources() -> None:
    switch = await read_recorded_switch(SONIC, sonic_responses())

    assert switch == sonic_switch(("SN-SONIC-CHASSIS",))


async def test_the_sonic_platform_reads_the_eeprom_serial_when_the_chassis_serial_is_na() -> None:
    switch = await read_recorded_switch(SONIC, sonic_responses(chassis="chassis-info-na"))

    assert switch == sonic_switch(("SN-SONIC-EEPROM",))


async def test_the_sonic_platform_reads_a_switch_with_only_the_eeprom_serial() -> None:
    switch = await read_recorded_switch(SONIC, sonic_responses(without=[CHASSIS_INFO]))

    assert switch == sonic_switch(("SN-SONIC-EEPROM",))


async def test_the_sonic_platform_reads_a_switch_with_neither_serial_as_one_with_none() -> None:
    switch = await read_recorded_switch(SONIC, sonic_responses(without=[CHASSIS_INFO, EEPROM_SERIAL]))

    assert switch == sonic_switch(())


async def test_the_sonic_platform_reports_no_os_version_when_the_switch_could_not_read_it() -> None:
    switch = await read_recorded_switch(SONIC, sonic_responses(os_version="osversion-build-error"))

    assert switch == sonic_switch(("SN-SONIC-CHASSIS",), os_version=None)


async def test_a_sonic_switch_with_no_os_version_is_not_found() -> None:
    with pytest.raises(GnmiNotFound):
        await read_recorded_switch(SONIC, sonic_responses(without=[OS_VERSION]))


def test_a_recording_in_the_mockup_layout_is_keyed_by_path(tmp_path: Path) -> None:
    for path, resource in {
        "redfish/v1": {"Id": "RootService"},
        "redfish/v1/Systems": {"Members": []},
    }.items():
        (tmp_path / path).mkdir(parents=True)
        (tmp_path / path / "index.json").write_text(json.dumps(resource))

    assert load_mockup(tmp_path) == {
        "/redfish/v1": {"Id": "RootService"},
        "/redfish/v1/Systems": {"Members": []},
    }


async def get(transport: httpx.MockTransport, method: str, path: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport, base_url=f"https://{ADDRESS}") as client:
        return await client.request(method, path)


async def test_a_bmc_answers_404_for_a_path_the_recording_lacks() -> None:
    response = await get(recorded_bmc(bmc_tree()), "GET", "/redfish/v1/Nothing")

    assert response.status_code == httpx.codes.NOT_FOUND


async def test_a_bmc_serves_a_resource_with_or_without_a_trailing_slash() -> None:
    transport = recorded_bmc(bmc_tree())

    assert (await get(transport, "GET", "/redfish/v1/Systems/")).json() == bmc_tree()["/redfish/v1/Systems"]


async def test_a_bmc_whose_service_root_links_sessions_accepts_a_login_and_its_logout() -> None:
    transport = recorded_bmc(load_mockup(RECORDINGS / "openbmc"))

    login = await get(transport, "POST", "/redfish/v1/SessionService/Sessions")
    logout = await get(transport, "DELETE", login.headers["Location"])

    assert (login.status_code, "X-Auth-Token" in login.headers) == (httpx.codes.CREATED, True)
    assert logout.status_code == httpx.codes.NO_CONTENT


async def test_a_bmc_without_a_session_service_refuses_a_login() -> None:
    response = await get(recorded_bmc(bmc_tree()), "POST", "/redfish/v1/SessionService/Sessions")

    assert response.status_code == httpx.codes.NOT_FOUND


def test_a_switch_has_no_data_for_a_required_request_the_recording_lacks() -> None:
    client = recorded_switch({COMPONENTS: srlinux_responses()[COMPONENTS]})

    with pytest.raises(GnmiNotFound, match=r"no recorded response for /lldp/interfaces"):
        client.get(ADDRESS, (COMPONENTS, LLDP), LOGIN)


def test_a_switch_has_no_updates_for_an_optional_request_the_recording_lacks() -> None:
    maybe = GnmiRequest("table/maybe", target="DB", optional=True)
    client = recorded_switch({COMPONENTS: srlinux_responses()[COMPONENTS]})

    assert client.get(ADDRESS, (maybe,), LOGIN) == {maybe: []}
