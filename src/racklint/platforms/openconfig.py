# racklint/src/racklint/platforms/openconfig.py
"""The platforms for switches that speak gNMI with OpenConfig models."""

from collections.abc import Sequence
from typing import Literal

from racklint.checks import Check, switch_checks
from racklint.gnmi import GnmiAccess, GnmiReader
from racklint.models import ActualSwitch, ExpectedSwitch, Kind


class OpenConfigSwitch:
    """Any switch with OpenConfig models over gNMI. The default platform of a switch."""

    name = "openconfig"
    kind: Literal[Kind.SWITCH] = Kind.SWITCH
    gnmi_port: int | None = None

    def reader(self, access: GnmiAccess) -> GnmiReader:
        return GnmiReader(access)

    def checks(self) -> Sequence[Check[ExpectedSwitch, ActualSwitch]]:
        return switch_checks()


class SrLinux(OpenConfigSwitch):
    """Nokia SR Linux. A named place for its differences from OpenConfig; there are none yet."""

    name = "srlinux"


OPENCONFIG = OpenConfigSwitch()
SRLINUX = SrLinux()
