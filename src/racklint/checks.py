# racklint/src/racklint/checks.py
"""The checks: each compares one aspect of a device's plan with what the device reports.

A check is pure. It returns one message per problem it finds, and no messages when the device is as planned.
Checks that make sense for every kind of device are typed by the small protocols below, so one class serves
servers and switches.
"""

from collections import Counter
from collections.abc import Sequence
from fnmatch import fnmatchcase
from typing import Protocol, runtime_checkable

from racklint.gpus import GPU_RULES, GpuRule
from racklint.models import (
    ActualNode,
    ActualSwitch,
    ExpectedNode,
    ExpectedSwitch,
    FirmwareItem,
    FirmwareRule,
    Gpu,
    GpuSpec,
    LldpNeighbour,
    PlannedLink,
    Processor,
)

SPEC = "config context 'racklint'"


def not_planned(item: str) -> str:
    return f"not planned in NetBox: {item}"


class Check[E, A](Protocol):
    name: str

    def run(self, expected: E, actual: A) -> list[str]: ...


@runtime_checkable
class Notes[E, A](Protocol):
    """A check that can also say what it could not check. Notes never fail a device."""

    def notes(self, expected: E, actual: A) -> list[str]: ...


class PlansSerial(Protocol):
    @property
    def serial(self) -> str | None: ...


class ReportsSerials(Protocol):
    @property
    def serials(self) -> tuple[str, ...]: ...


class PlansFirmware(Protocol):
    @property
    def device_type(self) -> str: ...

    @property
    def firmware(self) -> tuple[FirmwareRule, ...]: ...


class ReportsFirmware(Protocol):
    @property
    def firmware(self) -> tuple[FirmwareItem, ...]: ...


class SerialCheck:
    name = "serial"

    def run(self, expected: PlansSerial, actual: ReportsSerials) -> list[str]:
        if expected.serial is None:
            return [not_planned("serial")]
        if expected.serial in actual.serials:
            return []
        reported = ", ".join(actual.serials) or "none"
        return [f"expected {expected.serial}, device reports {reported}"]


class GpuCheck:
    name = "gpu"

    def __init__(self, rules: Sequence[GpuRule] = GPU_RULES) -> None:
        self._rules = tuple(rules)

    def run(self, expected: ExpectedNode, actual: ActualNode) -> list[str]:
        if expected.spec is None:
            return [not_planned(SPEC)]
        planned = expected.spec.gpu
        gpus = self._gpus(actual, planned)
        problems: list[str] = []
        if len(gpus) != planned.count:
            found = f"{len(gpus)} found, {planned.count} planned"
            problems.append(found if gpus else f"{found}; {_listed(actual.processors)}")
        problems += [
            f"{gpu.id} is {gpu.model!r}, planned {planned.model!r}"
            for gpu in gpus
            if gpu.model != planned.model
        ]
        return problems

    def _gpus(self, actual: ActualNode, planned: GpuSpec) -> tuple[Gpu, ...]:
        """The GPUs of the first rule that finds any. Rules never add up, so no GPU is counted twice."""
        for rule in self._rules:
            if gpus := rule.find(actual, planned):
                return gpus
        return ()


def _listed(processors: Sequence[Processor]) -> str:
    """What the BMC listed, for a finding, in the order first seen: 2 CPU 'Xeon', 8 Accelerator 'GH100'."""
    if not processors:
        return "the BMC lists no processors"
    counts = Counter((processor.type or "untyped", processor.model) for processor in processors)
    return "the BMC lists " + ", ".join(
        f"{count} {kind} {model!r}" if model else f"{count} {kind}" for (kind, model), count in counts.items()
    )


class MemoryCheck:
    name = "memory"

    def run(self, expected: ExpectedNode, actual: ActualNode) -> list[str]:
        if expected.spec is None:
            return [not_planned(SPEC)]
        planned = expected.spec.memory
        count = len(actual.dimms)
        total_mib = sum(dimm.capacity_mib for dimm in actual.dimms)
        if count == planned.dimm_count and total_mib == planned.total_gib * 1024:
            return []
        found = f"{count} DIMMs ({total_mib / 1024:g} GiB)"
        return [f"{found}, {planned.dimm_count} planned ({planned.total_gib} GiB)"]


class NicCheck:
    """Planned MACs must be reported; interfaces planned without a MAC are only counted.

    With require_macs=False, a BMC that reports no MAC on any port is checked by its port count, and a note
    says the MACs were not checked. A BMC that reports any MAC is held to the planned MACs.
    """

    name = "nic"

    def __init__(self, require_macs: bool = True) -> None:
        self._require_macs = require_macs

    def run(self, expected: ExpectedNode, actual: ActualNode) -> list[str]:
        if not expected.interfaces:
            return [not_planned("interfaces")]
        # A BMC that reports no MAC cannot be held to the planned ones.
        compared = () if self._macs_unknown(actual) else expected.interfaces
        reported = {port.mac for port in actual.ports if port.mac}
        problems = [
            f"{interface.name} ({interface.mac}) not found"
            for interface in compared
            if interface.mac and interface.mac not in reported
        ]
        if len(actual.ports) < len(expected.interfaces):
            problems.append(f"{len(actual.ports)} ports found, {len(expected.interfaces)} planned")
        return problems

    def notes(self, expected: ExpectedNode, actual: ActualNode) -> list[str]:
        if self._macs_unknown(actual) and any(interface.mac for interface in expected.interfaces):
            return ["MACs not checked: the BMC reports none"]
        return []

    def _macs_unknown(self, actual: ActualNode) -> bool:
        """The MACs may be unreported (require_macs is off), and the BMC reports none."""
        return not self._require_macs and not any(port.mac for port in actual.ports)


class FirmwareCheck:
    """Each planned rule must match a firmware item, and each item it matches must run an approved version."""

    name = "firmware"

    def run(self, expected: PlansFirmware, actual: ReportsFirmware) -> list[str]:
        if not expected.firmware:
            return [f"no firmware baseline for device type {expected.device_type}"]
        problems: list[str] = []
        for rule in expected.firmware:
            matched = [item for item in actual.firmware if fnmatchcase(item.id, rule.match)]
            if not matched:
                problems.append(f"no firmware item matches {rule.match!r}")
            problems += [
                f"{item.id} {item.version} not in approved [{', '.join(rule.approved)}]"
                for item in matched
                if item.version not in rule.approved
            ]
        return problems


class LinkCheck:
    """Each planned switch-to-switch link must be seen over LLDP on its port, as the planned far end.

    The far end may be reported by the interface's name or by its NetBox label, such as a SONiC port alias.
    A planned link NetBox does not let racklint check is reported, never dropped. A port NetBox records as
    physical, not management-only and without a cable must not see any LLDP neighbour. Management ports are
    never considered: switches see each other there.
    """

    name = "link"

    def run(self, expected: ExpectedSwitch, actual: ActualSwitch) -> list[str]:
        seen = {neighbour.interface: neighbour for neighbour in reversed(actual.neighbours)}
        problems = [
            problem for link in expected.links if (problem := _link_problem(link, seen.get(link.interface)))
        ]
        problems += [
            f"{link.interface} cannot be checked: {link.reason}" for link in expected.uncheckable_links
        ]
        problems += [
            f"{interface} not planned in NetBox, sees {_describe(neighbour)}"
            for interface in expected.uncabled_interfaces
            if (neighbour := seen.get(interface)) is not None
        ]
        return problems


def _link_problem(link: PlannedLink, neighbour: LldpNeighbour | None) -> str | None:
    planned = f"{link.interface} expected {link.peer_device} {link.peer_interface}"
    if neighbour is None:
        return f"{planned}, no LLDP neighbour"
    if _is_far_end(neighbour, link):
        return None
    return f"{planned}, sees {_describe(neighbour)}"


def _is_far_end(neighbour: LldpNeighbour, link: PlannedLink) -> bool:
    """Switches often report an FQDN as their name, and some put a MAC address in the port id."""
    same_device = _short_name(neighbour.system_name) == _short_name(link.peer_device)
    return same_device and bool(_peer_names(link) & {neighbour.port_id, neighbour.port_description})


def _peer_names(link: PlannedLink) -> set[str]:
    """The names a neighbour may report for the planned far interface: its NetBox name, or its label."""
    names = {link.peer_interface}
    if link.peer_interface_label:
        names.add(link.peer_interface_label)
    return names


def _short_name(name: str) -> str:
    return name.split(".")[0]


def _describe(neighbour: LldpNeighbour) -> str:
    return f"{neighbour.system_name} {neighbour.port_id}"


def server_checks() -> list[Check[ExpectedNode, ActualNode]]:
    return [SerialCheck(), GpuCheck(), MemoryCheck(), NicCheck(), FirmwareCheck()]


def switch_checks() -> list[Check[ExpectedSwitch, ActualSwitch]]:
    return [SerialCheck(), FirmwareCheck(), LinkCheck()]
