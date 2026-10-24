# racklint/tests/test_models.py
import pytest
from pydantic import ValidationError

from racklint.models import (
    ExpectedNode,
    ExpectedSwitch,
    GpuSpec,
    Kind,
    NodeResult,
    PlanContext,
    PlannedInterface,
    PlannedLink,
    Report,
    Status,
)


@pytest.mark.parametrize(
    "raw", ["A0:88:C2:00:01:01", "a0-88-c2-00-01-01", "a088.c200.0101", " a0:88:c2:00:01:01 "]
)
def test_mac_addresses_are_normalised(raw: str) -> None:
    assert PlannedInterface(name="eth0", mac=raw).mac == "a0:88:c2:00:01:01"


@pytest.mark.parametrize("raw", ["a0:88:c2:00:01", "a0:88:c2:00:01:0g", "not a mac"])
def test_invalid_mac_addresses_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError, match="not a MAC address"):
        PlannedInterface(name="eth0", mac=raw)


def result(node: str, status: Status) -> NodeResult:
    return NodeResult(node=node, kind=Kind.SERVER, address=None, status=status)


def test_report_counts_results_by_status() -> None:
    report = Report(
        results=(result("n01", Status.PASS), result("n02", Status.FAIL), result("n03", Status.PASS))
    )

    assert [report.count(status) for status in Status] == [2, 1, 0]


@pytest.mark.parametrize(
    ("statuses", "passed"),
    [
        ((Status.PASS, Status.PASS), True),
        ((Status.PASS, Status.FAIL), False),
        ((Status.PASS, Status.ERROR), False),
    ],
)
def test_report_passes_only_if_every_node_passes(statuses: tuple[Status, ...], passed: bool) -> None:
    report = Report(results=tuple(result(f"n{index}", status) for index, status in enumerate(statuses)))

    assert report.passed is passed


def test_a_plan_context_needs_gpu_and_memory_together() -> None:
    with pytest.raises(ValidationError, match="planned together"):
        PlanContext(gpu=GpuSpec(count=8, model="NVIDIA H100 80GB HBM3"))


def test_a_plan_context_without_gpu_and_memory_has_no_spec() -> None:
    assert PlanContext(kind=Kind.SWITCH).spec is None


def test_a_server_is_read_at_its_bmc_address() -> None:
    node = ExpectedNode(name="n01", device_type="gpu-node-h100", bmc_address="192.0.2.11")

    assert (node.kind, node.address) == (Kind.SERVER, "192.0.2.11")


def test_a_plan_context_names_a_credential_set() -> None:
    assert PlanContext(credentials="bmc-dc1").credentials == "bmc-dc1"
    assert PlanContext().credentials is None


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", "", "has space"])
def test_a_credential_set_name_must_be_safe_as_a_path_segment(name: str) -> None:
    with pytest.raises(ValidationError, match="credentials"):
        PlanContext(credentials=name)


def test_a_plan_context_names_a_platform_and_a_gnmi_port() -> None:
    context = PlanContext(kind=Kind.SWITCH, platform="arista-eos", gnmi_port=6030)

    assert (context.platform, context.gnmi_port) == ("arista-eos", 6030)


def test_a_device_names_no_platform_and_no_gnmi_port_by_default() -> None:
    assert (PlanContext().platform, PlanContext().gnmi_port) == (None, None)
    assert ExpectedNode(name="n01", device_type="gpu-node-h100").platform is None
    switch = ExpectedSwitch(name="leaf1", device_type="ixr-d2l")
    assert (switch.platform, switch.gnmi_port) == (None, None)


@pytest.mark.parametrize("name", ["OpenBMC", "open bmc", "-x", "a/b", "", "open_bmc"])
def test_a_platform_name_is_lower_case_letters_digits_and_hyphens(name: str) -> None:
    with pytest.raises(ValidationError, match="platform"):
        PlanContext(platform=name)


@pytest.mark.parametrize("port", [0, -1])
def test_a_gnmi_port_must_be_positive(port: int) -> None:
    with pytest.raises(ValidationError, match="gnmi_port"):
        PlanContext(gnmi_port=port)


def test_a_planned_link_has_no_peer_interface_label_by_default() -> None:
    link = PlannedLink(interface="Ethernet0", peer_device="spine1", peer_interface="Ethernet4")

    assert link.peer_interface_label is None
