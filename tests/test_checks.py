# racklint/tests/test_checks.py
import pytest

from racklint.checks import (
    Check,
    FirmwareCheck,
    GpuCheck,
    LinkCheck,
    MemoryCheck,
    NicCheck,
    Notes,
    SerialCheck,
    server_checks,
    switch_checks,
)
from racklint.models import (
    ActualNode,
    ActualSwitch,
    Dimm,
    ExpectedNode,
    ExpectedSwitch,
    FirmwareItem,
    Gpu,
    GpuSpec,
    HardwareSpec,
    LldpNeighbour,
    NetworkPort,
    PlannedInterface,
    PlannedLink,
    Processor,
    UncheckableLink,
)
from tests.factories import (
    GPU_FIRMWARE,
    GPU_MODEL,
    SPEC,
    SWITCH_OS,
    actual_node,
    actual_switch,
    expected_node,
    expected_switch,
)


def processors(count: int, kind: str, model: str = GPU_MODEL) -> tuple[Processor, ...]:
    return tuple(Processor(id=f"{kind}_{index}", type=kind, model=model) for index in range(count))


def planning_gpus(count: int) -> ExpectedNode:
    return expected_node(spec=HardwareSpec(gpu=GpuSpec(count=count, model=GPU_MODEL), memory=SPEC.memory))


def planning_labelled_link(label: str) -> ExpectedSwitch:
    """leaf1 planned with only its first link, to a far interface that NetBox labels `label`."""
    link = PlannedLink(
        interface="ethernet-1/1",
        peer_device="spine1",
        peer_interface="ethernet-1/1",
        peer_interface_label=label,
    )
    return expected_switch(links=(link,))


class FixedGpus:
    """A GPU rule that finds the same GPUs whatever the node reports."""

    def __init__(self, *gpus: Gpu) -> None:
        self._gpus = gpus

    def find(self, actual: ActualNode, planned: GpuSpec) -> tuple[Gpu, ...]:
        return self._gpus


def test_a_server_built_as_planned_passes_every_check() -> None:
    for check in server_checks():
        assert check.run(expected_node(), actual_node()) == [], check.name


def test_a_switch_built_as_planned_passes_every_check() -> None:
    for check in switch_checks():
        assert check.run(expected_switch(), actual_switch()) == [], check.name


@pytest.mark.parametrize(
    ("check", "expected", "actual", "messages"),
    [
        pytest.param(
            SerialCheck(),
            expected_node(serial=None),
            actual_node(),
            ["not planned in NetBox: serial"],
            id="serial-not-planned",
        ),
        pytest.param(
            SerialCheck(),
            expected_node(),
            actual_node(serials=("SN-10",)),
            ["expected SN-01, device reports SN-10"],
            id="serial-differs",
        ),
        pytest.param(
            SerialCheck(),
            expected_node(),
            actual_node(serials=()),
            ["expected SN-01, device reports none"],
            id="serial-not-reported",
        ),
        pytest.param(
            GpuCheck(),
            expected_node(spec=None),
            actual_node(),
            ["not planned in NetBox: config context 'racklint'"],
            id="gpu-not-planned",
        ),
        pytest.param(
            GpuCheck(),
            expected_node(),
            actual_node(processors=(Processor(id="GPU_0", type="GPU", model=GPU_MODEL),)),
            ["1 found, 2 planned"],
            id="gpu-count",
        ),
        pytest.param(
            GpuCheck(),
            expected_node(),
            actual_node(
                processors=(
                    Processor(id="GPU_0", type="GPU", model=GPU_MODEL),
                    Processor(id="GPU_1", type="GPU", model="NVIDIA A100-SXM4-80GB"),
                )
            ),
            ["GPU_1 is 'NVIDIA A100-SXM4-80GB', planned 'NVIDIA H100 80GB HBM3'"],
            id="gpu-model",
        ),
        pytest.param(
            GpuCheck(),
            expected_node(),
            actual_node(processors=processors(2, "Accelerator")),
            [],
            id="gpu-accelerators-of-the-planned-model",
        ),
        pytest.param(
            GpuCheck(),
            expected_node(),
            actual_node(processors=(*processors(2, "GPU"), *processors(1, "Accelerator", "BlueField-3"))),
            [],
            id="gpu-accelerator-of-another-model-is-not-counted",
        ),
        pytest.param(
            GpuCheck(),
            planning_gpus(8),
            actual_node(
                processors=(
                    *processors(2, "CPU", "Intel Xeon 8480+"),
                    *processors(8, "Accelerator", "GH100 80GB"),
                )
            ),
            ["0 found, 8 planned; the BMC lists 2 CPU 'Intel Xeon 8480+', 8 Accelerator 'GH100 80GB'"],
            id="gpu-zero-found-lists-the-processors",
        ),
        pytest.param(
            GpuCheck(),
            planning_gpus(8),
            actual_node(processors=()),
            ["0 found, 8 planned; the BMC lists no processors"],
            id="gpu-zero-found-no-processors",
        ),
        pytest.param(
            GpuCheck(),
            planning_gpus(8),
            actual_node(processors=(Processor(id="unknown0", type="", model=""),)),
            ["0 found, 8 planned; the BMC lists 1 untyped"],
            id="gpu-zero-found-untyped-processor",
        ),
        pytest.param(
            MemoryCheck(),
            expected_node(spec=None),
            actual_node(),
            ["not planned in NetBox: config context 'racklint'"],
            id="memory-not-planned",
        ),
        pytest.param(
            MemoryCheck(),
            expected_node(),
            actual_node(dimms=(Dimm(id="DIMM_0", capacity_mib=65536),)),
            ["1 DIMMs (64 GiB), 2 planned (128 GiB)"],
            id="memory-dimm-missing",
        ),
        pytest.param(
            MemoryCheck(),
            expected_node(),
            actual_node(dimms=(Dimm(id="DIMM_0", capacity_mib=65536), Dimm(id="DIMM_1", capacity_mib=32768))),
            ["2 DIMMs (96 GiB), 2 planned (128 GiB)"],
            id="memory-capacity",
        ),
        pytest.param(
            NicCheck(),
            expected_node(interfaces=()),
            actual_node(),
            ["not planned in NetBox: interfaces"],
            id="nic-not-planned",
        ),
        pytest.param(
            NicCheck(),
            expected_node(),
            actual_node(ports=(NetworkPort(id="0", mac="02:00:00:00:09:09"), NetworkPort(id="1"))),
            ["eth0 (02:00:00:00:01:01) not found"],
            id="nic-mac-missing",
        ),
        pytest.param(
            NicCheck(),
            expected_node(),
            actual_node(ports=(NetworkPort(id="0", mac="02:00:00:00:01:01"),)),
            ["1 ports found, 2 planned"],
            id="nic-port-count",
        ),
        pytest.param(
            NicCheck(),
            expected_node(),
            actual_node(ports=(NetworkPort(id="0"), NetworkPort(id="1"))),
            ["eth0 (02:00:00:00:01:01) not found"],
            id="nic-no-macs-reported",
        ),
        pytest.param(
            NicCheck(require_macs=False),
            expected_node(),
            actual_node(ports=(NetworkPort(id="0"), NetworkPort(id="1"))),
            [],
            id="nic-macs-not-required-none-reported",
        ),
        pytest.param(
            NicCheck(require_macs=False),
            expected_node(),
            actual_node(ports=(NetworkPort(id="0"),)),
            ["1 ports found, 2 planned"],
            id="nic-macs-not-required-port-count",
        ),
        pytest.param(
            NicCheck(require_macs=False),
            expected_node(interfaces=(PlannedInterface(name="eth0"), PlannedInterface(name="eth1"))),
            actual_node(ports=(NetworkPort(id="0"), NetworkPort(id="1"))),
            [],
            id="nic-macs-not-required-none-planned",
        ),
        pytest.param(
            NicCheck(require_macs=False),
            expected_node(),
            actual_node(ports=(NetworkPort(id="0", mac="02:00:00:00:09:09"), NetworkPort(id="1"))),
            ["eth0 (02:00:00:00:01:01) not found"],
            id="nic-macs-not-required-some-reported",
        ),
        pytest.param(
            NicCheck(require_macs=False),
            expected_node(),
            actual_node(),
            [],
            id="nic-macs-not-required-all-reported",
        ),
        pytest.param(
            FirmwareCheck(),
            expected_node(firmware=()),
            actual_node(),
            ["no firmware baseline for device type gpu-node-h100"],
            id="firmware-no-baseline",
        ),
        pytest.param(
            FirmwareCheck(),
            expected_node(),
            actual_node(
                firmware=(
                    FirmwareItem(id="BMC", version="1.3.2"),
                    FirmwareItem(id="GPU_0", version=GPU_FIRMWARE),
                )
            ),
            ["BMC 1.3.2 not in approved [1.4.0]"],
            id="firmware-not-approved",
        ),
        pytest.param(
            FirmwareCheck(),
            expected_node(),
            actual_node(firmware=(FirmwareItem(id="GPU_0", version=GPU_FIRMWARE),)),
            ["no firmware item matches 'BMC'"],
            id="firmware-item-missing",
        ),
    ],
)
def test_server_checks_report_each_problem(
    check: Check[ExpectedNode, ActualNode], expected: ExpectedNode, actual: ActualNode, messages: list[str]
) -> None:
    assert check.run(expected, actual) == messages


NO_MACS = actual_node(ports=(NetworkPort(id="0"), NetworkPort(id="1")))
MACS_NOT_CHECKED = ["MACs not checked: the BMC reports none"]


@pytest.mark.parametrize(
    ("check", "expected", "actual", "notes"),
    [
        pytest.param(NicCheck(), expected_node(), NO_MACS, [], id="required"),
        pytest.param(
            NicCheck(require_macs=False), expected_node(), NO_MACS, MACS_NOT_CHECKED, id="none-reported"
        ),
        pytest.param(
            NicCheck(require_macs=False),
            expected_node(interfaces=(PlannedInterface(name="eth0"), PlannedInterface(name="eth1"))),
            NO_MACS,
            [],
            id="none-planned",
        ),
        pytest.param(
            NicCheck(require_macs=False),
            expected_node(),
            actual_node(ports=(NetworkPort(id="0", mac="02:00:00:00:09:09"), NetworkPort(id="1"))),
            [],
            id="some-reported",
        ),
        pytest.param(NicCheck(require_macs=False), expected_node(), actual_node(), [], id="all-reported"),
    ],
)
def test_the_nic_check_notes_that_it_did_not_check_the_macs_only_when_it_may_skip_them(
    check: NicCheck, expected: ExpectedNode, actual: ActualNode, notes: list[str]
) -> None:
    assert check.notes(expected, actual) == notes


def test_a_check_with_notes_is_recognised_by_the_notes_protocol() -> None:
    assert isinstance(NicCheck(), Notes)
    assert not isinstance(SerialCheck(), Notes)


PLANNED_GPUS = (Gpu(id="GPU_0", model=GPU_MODEL), Gpu(id="GPU_1", model=GPU_MODEL))


def test_the_first_gpu_rule_that_finds_gpus_decides() -> None:
    later = FixedGpus(*PLANNED_GPUS, Gpu(id="GPU_2", model=GPU_MODEL))

    assert GpuCheck([FixedGpus(*PLANNED_GPUS), later]).run(expected_node(), actual_node()) == []


def test_a_gpu_rule_that_finds_none_is_passed_over() -> None:
    assert GpuCheck([FixedGpus(), FixedGpus(*PLANNED_GPUS)]).run(expected_node(), actual_node()) == []


def test_no_gpu_rule_finding_any_is_a_zero_count_that_lists_the_processors() -> None:
    actual = actual_node(processors=processors(1, "CPU", "Xeon"))

    assert GpuCheck([FixedGpus()]).run(expected_node(), actual) == [
        "0 found, 2 planned; the BMC lists 1 CPU 'Xeon'"
    ]


@pytest.mark.parametrize(
    ("check", "expected", "actual", "messages"),
    [
        pytest.param(
            FirmwareCheck(),
            expected_switch(),
            actual_switch(firmware=(FirmwareItem(id="OS", version="v26.3.3-392-g480f5fa2d04"),)),
            [f"OS v26.3.3-392-g480f5fa2d04 not in approved [{SWITCH_OS}]"],
            id="switch-os-not-approved",
        ),
        pytest.param(
            SerialCheck(),
            expected_switch(),
            actual_switch(serials=("SN-SW-09",)),
            ["expected SN-SW-01, device reports SN-SW-09"],
            id="switch-serial",
        ),
        pytest.param(
            LinkCheck(),
            expected_switch(),
            actual_switch(
                neighbours=(
                    LldpNeighbour(interface="ethernet-1/1", system_name="spine1", port_id="ethernet-1/2"),
                    LldpNeighbour(interface="ethernet-1/2", system_name="spine1", port_id="ethernet-1/1"),
                )
            ),
            [
                "ethernet-1/1 expected spine1 ethernet-1/1, sees spine1 ethernet-1/2",
                "ethernet-1/2 expected spine1 ethernet-1/2, sees spine1 ethernet-1/1",
            ],
            id="link-swapped",
        ),
        pytest.param(
            LinkCheck(),
            expected_switch(),
            actual_switch(
                neighbours=(
                    LldpNeighbour(interface="ethernet-1/1", system_name="spine1", port_id="ethernet-1/1"),
                )
            ),
            ["ethernet-1/2 expected spine1 ethernet-1/2, no LLDP neighbour"],
            id="link-missing",
        ),
        pytest.param(
            LinkCheck(),
            expected_switch(),
            actual_switch(
                neighbours=(
                    LldpNeighbour(
                        interface="ethernet-1/1", system_name="spine1.example.net", port_id="ethernet-1/1"
                    ),
                    LldpNeighbour(
                        interface="ethernet-1/2",
                        system_name="spine1",
                        port_id="aa:bb:cc:00:00:02",
                        port_description="ethernet-1/2",
                    ),
                )
            ),
            [],
            id="link-fqdn-and-port-description",
        ),
        pytest.param(
            LinkCheck(),
            planning_labelled_link("Eth1/1"),
            actual_switch(
                neighbours=(LldpNeighbour(interface="ethernet-1/1", system_name="spine1", port_id="Eth1/1"),)
            ),
            [],
            id="link-label",
        ),
        pytest.param(
            LinkCheck(),
            planning_labelled_link("Eth1/1"),
            actual_switch(
                neighbours=(LldpNeighbour(interface="ethernet-1/1", system_name="spine1", port_id="Eth1/2"),)
            ),
            ["ethernet-1/1 expected spine1 ethernet-1/1, sees spine1 Eth1/2"],
            id="link-neither-name-nor-label",
        ),
        pytest.param(
            LinkCheck(),
            expected_switch(),
            actual_switch(
                neighbours=(
                    *actual_switch().neighbours,
                    LldpNeighbour(interface="ethernet-1/3", system_name="spine1", port_id="ethernet-1/9"),
                )
            ),
            ["ethernet-1/3 not planned in NetBox, sees spine1 ethernet-1/9"],
            id="link-unplanned",
        ),
        pytest.param(
            LinkCheck(),
            expected_switch(
                uncheckable_links=(
                    UncheckableLink(interface="ethernet-1/6", reason="spine2 has no racklint config context"),
                )
            ),
            actual_switch(),
            ["ethernet-1/6 cannot be checked: spine2 has no racklint config context"],
            id="link-uncheckable",
        ),
    ],
)
def test_switch_checks_report_each_problem(
    check: Check[ExpectedSwitch, ActualSwitch],
    expected: ExpectedSwitch,
    actual: ActualSwitch,
    messages: list[str],
) -> None:
    assert check.run(expected, actual) == messages
