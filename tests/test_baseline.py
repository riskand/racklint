# racklint/tests/test_baseline.py
from pathlib import Path

import pytest

from racklint.baseline import FirmwareBaseline
from racklint.errors import BaselineError
from racklint.models import ExpectedNode, ExpectedSwitch, FirmwareRule


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "baseline.yaml"
    path.write_text(text)
    return path


def test_loads_the_rules_of_each_device_type(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
device_types:
  gpu-node-h100:
    - match: "GPU_*"
      approved: ["96.00.89.00.01"]
""",
    )

    baseline = FirmwareBaseline.load(path)

    assert baseline.device_types == {
        "gpu-node-h100": (FirmwareRule(match="GPU_*", approved=("96.00.89.00.01",)),)
    }


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("device_types: [", id="not-yaml"),
        pytest.param("{}", id="no-device-types"),
        pytest.param("device_types:\n  x:\n    - match: BMC\n      approved: []\n", id="nothing-approved"),
        pytest.param("device_types:\n  x:\n    - match: BMC\n      approved: [1.4]\n", id="unquoted-version"),
    ],
)
def test_invalid_baselines_are_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(BaselineError, match="cannot load firmware baseline"):
        FirmwareBaseline.load(write(tmp_path, text))


def test_a_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="cannot load firmware baseline"):
        FirmwareBaseline.load(tmp_path / "missing.yaml")


def test_a_baseline_replaces_the_planned_rules_of_a_node() -> None:
    baseline = FirmwareBaseline(
        device_types={"gpu-node-h100": (FirmwareRule(match="BMC", approved=("9.9",)),)}
    )
    node = ExpectedNode(
        name="n01", device_type="gpu-node-h100", firmware=(FirmwareRule(match="X", approved=("1",)),)
    )
    other = ExpectedNode(name="n02", device_type="other-type", firmware=node.firmware)

    assert baseline.applied_to(node).firmware == (FirmwareRule(match="BMC", approved=("9.9",)),)
    assert baseline.applied_to(other).firmware == ()


def test_a_baseline_applies_to_a_plan_of_servers_and_switches() -> None:
    rule = FirmwareRule(match="OS", approved=("9.9",))
    baseline = FirmwareBaseline(device_types={"ixr-d2l": (rule,)})
    plan: list[ExpectedNode | ExpectedSwitch] = [
        ExpectedNode(name="n01", device_type="gpu-node-h100"),
        ExpectedSwitch(name="leaf1", device_type="ixr-d2l"),
    ]

    applied = [baseline.applied_to(node) for node in plan]

    assert [(node.name, node.firmware) for node in applied] == [("n01", ()), ("leaf1", (rule,))]
