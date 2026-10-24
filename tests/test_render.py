# racklint/tests/test_render.py
import io
import json

from rich.console import Console

from racklint.models import Finding, Kind, NodeResult, Report, Status
from racklint.render import JsonRenderer, Renderer, TableRenderer

REPORT = Report(
    results=(
        NodeResult(
            node="n01",
            kind=Kind.SERVER,
            address="192.0.2.11",
            status=Status.PASS,
            notes=(Finding(check="nic", message="MACs not checked: the BMC reports none"),),
        ),
        NodeResult(
            node="n05",
            kind=Kind.SERVER,
            address="192.0.2.15",
            status=Status.FAIL,
            findings=(
                Finding(check="memory", message="31 DIMMs (1984 GiB), 32 planned (2048 GiB)"),
                Finding(check="firmware", message="BMC 1.3.2 not in approved [1.4.0]"),
            ),
            notes=(Finding(check="gpu", message="the BMC reports no GPU firmware"),),
        ),
        NodeResult(
            node="n06",
            kind=Kind.SERVER,
            address="192.0.2.16",
            status=Status.ERROR,
            error="authentication failed",
        ),
    )
)


def render(renderer: Renderer) -> str:
    output = io.StringIO()
    renderer.render(REPORT, Console(file=output, width=120))
    return output.getvalue()


def test_the_table_shows_each_node_with_its_reasons() -> None:
    text = render(TableRenderer())

    for expected in (
        "n01",
        "server",
        "PASS",
        "FAIL",
        "memory: 31 DIMMs (1984 GiB), 32 planned (2048 GiB)",
        "firmware: BMC 1.3.2 not in approved [1.4.0]",
        "ERROR",
        "authentication failed",
        "1 passed, 1 failed, 1 errored",
    ):
        assert expected in text


def test_the_table_shows_notes_after_the_findings_of_a_device() -> None:
    text = render(TableRenderer())

    assert "note: nic: MACs not checked: the BMC reports none" in text
    assert text.index("firmware: BMC 1.3.2") < text.index("note: gpu: the BMC reports no GPU firmware")


def test_json_is_the_report() -> None:
    assert Report.model_validate_json(render(JsonRenderer())) == REPORT


def test_json_has_the_notes_and_schema_version_1() -> None:
    report = json.loads(render(JsonRenderer()))

    assert report["schema_version"] == 1
    assert report["results"][0]["notes"] == [
        {"check": "nic", "message": "MACs not checked: the BMC reports none"}
    ]
