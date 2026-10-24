# racklint/src/racklint/render.py
"""Present a report: a table for people, JSON for pipelines."""

from typing import Protocol

from rich.console import Console
from rich.table import Table
from rich.text import Text

from racklint.models import NodeResult, Report, Status

STATUS_STYLES = {Status.PASS: "bold green", Status.FAIL: "bold red", Status.ERROR: "bold yellow"}


class Renderer(Protocol):
    def render(self, report: Report, console: Console) -> None: ...


class TableRenderer:
    def render(self, report: Report, console: Console) -> None:
        table = Table("Node", "Kind", "Address", "Status", "Reasons")
        for result in report.results:
            # Text, not str: reasons such as "approved [1.4.0]" must not be read as rich markup.
            table.add_row(
                Text(result.node),
                Text(result.kind),
                Text(result.address or "-"),
                Text(result.status.upper(), style=STATUS_STYLES[result.status]),
                Text("\n".join(_reasons(result))),
            )
        console.print(table)
        console.print(
            f"{report.count(Status.PASS)} passed, {report.count(Status.FAIL)} failed, "
            f"{report.count(Status.ERROR)} errored",
            highlight=False,
        )


class JsonRenderer:
    def render(self, report: Report, console: Console) -> None:
        console.out(report.model_dump_json(indent=2), highlight=False)


def _reasons(result: NodeResult) -> list[str]:
    if result.error is not None:
        return [result.error]
    findings = [f"{finding.check}: {finding.message}" for finding in result.findings]
    notes = [f"note: {note.check}: {note.message}" for note in result.notes]
    return findings + notes
