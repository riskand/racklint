# racklint/src/racklint/platforms/redfish.py
"""The platforms for servers whose BMC speaks Redfish."""

from collections.abc import Sequence
from typing import Literal

from racklint.checks import Check, GpuCheck, NicCheck, server_checks
from racklint.gpus import OpenBmcAccelerators
from racklint.models import ActualNode, ExpectedNode, Kind
from racklint.redfish import BmcAccess, RedfishReader


class RedfishServer:
    """Any Redfish BMC, read as the DMTF standard describes it. The default platform of a server."""

    name = "redfish"
    kind: Literal[Kind.SERVER] = Kind.SERVER

    def reader(self, access: BmcAccess) -> RedfishReader:
        return RedfishReader(access)

    def checks(self) -> Sequence[Check[ExpectedNode, ActualNode]]:
        return server_checks()


class OpenBmc(RedfishServer):
    """Upstream OpenBMC (bmcweb), which reports GPUs only as accelerators and lists NIC ports without MACs."""

    name = "openbmc"

    def checks(self) -> Sequence[Check[ExpectedNode, ActualNode]]:
        return [_for_openbmc(check) for check in super().checks()]


def _for_openbmc(check: Check[ExpectedNode, ActualNode]) -> Check[ExpectedNode, ActualNode]:
    """The check as OpenBMC needs it: its own GPU rule, and NICs counted when no MAC is reported."""
    if isinstance(check, GpuCheck):
        return GpuCheck(rules=(OpenBmcAccelerators(),))
    if isinstance(check, NicCheck):
        return NicCheck(require_macs=False)
    return check


REDFISH = RedfishServer()
OPENBMC = OpenBmc()
