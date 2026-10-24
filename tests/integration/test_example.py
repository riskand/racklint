# racklint/tests/integration/test_example.py
"""Run the no-hardware example's command against its Docker Compose stack and check the planted faults.

Every credential is read from the example's Vault. With RACKLINT_EXAMPLE_PROFILE=fabric the stack includes the
switches and the report has nine rows.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

EXAMPLE = Path(__file__).parents[2] / "examples" / "no-hardware"
FABRIC = os.environ.get("RACKLINT_EXAMPLE_PROFILE") == "fabric"
PROFILE = ["--profile", "fabric"] if FABRIC else []
COMMAND = [
    "docker",
    "compose",
    *PROFILE,
    "run",
    "--rm",
    "-T",
    "racklint",
    "check",
    "--rack",
    "R01",
    "--format",
    "json",
]


@pytest.fixture(scope="module")
def results() -> dict[str, Any]:
    run = subprocess.run(COMMAND, cwd=EXAMPLE, capture_output=True, text=True, timeout=900, check=False)
    assert run.returncode == 1, run.stderr
    return {result["node"]: result for result in json.loads(run.stdout)["results"]}


def reasons(result: dict[str, Any]) -> list[str]:
    return [f"{finding['check']}: {finding['message']}" for finding in result["findings"]]


def test_the_devices_built_as_planned_pass(results: dict[str, Any]) -> None:
    expected = {"n01", "n02", "n06"} | ({"leaf1"} if FABRIC else set())
    assert {name for name, result in results.items() if result["status"] == "pass"} == expected


def test_only_n02_passes_with_a_note_that_it_could_not_check_the_macs(results: dict[str, Any]) -> None:
    assert {name for name, result in results.items() if result["notes"]} == {"n02"}
    assert results["n02"]["notes"] == [{"check": "nic", "message": "MACs not checked: the BMC reports none"}]


def test_n04_errors_without_its_credentials(results: dict[str, Any]) -> None:
    assert results["n04"]["status"] == "error"
    assert results["n04"]["error"] == "no credentials for n04: set bmc-r02 not found in Vault"


def test_n03_fails_on_its_missing_gpu(results: dict[str, Any]) -> None:
    assert reasons(results["n03"]) == ["gpu: 7 found, 8 planned"]


def test_n05_fails_on_memory_and_firmware(results: dict[str, Any]) -> None:
    assert reasons(results["n05"]) == [
        "memory: 31 DIMMs (1984 GiB), 32 planned (2048 GiB)",
        "firmware: BMC 1.3.2 not in approved [1.4.0]",
    ]


@pytest.mark.skipif(not FABRIC, reason="needs the fabric profile")
def test_leaf2_fails_on_its_swapped_uplinks(results: dict[str, Any]) -> None:
    assert reasons(results["leaf2"]) == [
        "link: ethernet-1/1 expected spine1 ethernet-1/3, sees spine1 ethernet-1/4",
        "link: ethernet-1/2 expected spine1 ethernet-1/4, sees spine1 ethernet-1/3",
    ]


@pytest.mark.skipif(not FABRIC, reason="needs the fabric profile")
def test_leaf3_fails_on_its_os_version(results: dict[str, Any]) -> None:
    assert reasons(results["leaf3"]) == [
        "firmware: OS v26.3.3-392-g480f5fa2d04 not in approved [v26.7.2-519-g94fbd638fa8]"
    ]
