# racklint/src/racklint/models.py
"""The state racklint works on: what NetBox plans, what a device reports, and the verdict on each device."""

import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    model_validator,
)

_MAC_SEPARATORS = re.compile(r"[:.\-]")
_MAC_DIGITS = re.compile(r"[0-9a-f]{12}")


def normalise_mac(value: str) -> str:
    """Return a MAC address as lower-case, colon-separated pairs, whichever separators it came with."""
    digits = _MAC_SEPARATORS.sub("", value.lower())
    if not _MAC_DIGITS.fullmatch(digits):
        raise ValueError(f"not a MAC address: {value!r}")
    return ":".join(digits[index : index + 2] for index in range(0, 12, 2))


MacAddress = Annotated[str, AfterValidator(normalise_mac)]

# A name that is safe as one segment of a secret's path: no slashes, no leading dot.
PATH_SEGMENT = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
CredentialSetName = Annotated[str, StringConstraints(pattern=PATH_SEGMENT)]
# The name of a platform, such as "openbmc" or "arista-eos".
PlatformName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$")]


class FrozenModel(BaseModel):
    """Immutable, with surrounding whitespace stripped from every string."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)


class Kind(StrEnum):
    SERVER = "server"
    SWITCH = "switch"


# What NetBox plans


class FirmwareRule(FrozenModel):
    """Firmware items whose Id matches `match`, an fnmatch glob, must run one of the `approved` versions."""

    match: str
    approved: tuple[str, ...] = Field(min_length=1)


class GpuSpec(FrozenModel):
    count: int
    model: str


class MemorySpec(FrozenModel):
    dimm_count: int
    total_gib: int


class HardwareSpec(FrozenModel):
    """A server's planned GPUs and memory."""

    gpu: GpuSpec
    memory: MemorySpec


class PlanContext(FrozenModel):
    """The `racklint` key of a device's config context."""

    kind: Kind = Kind.SERVER
    credentials: CredentialSetName | None = None
    platform: PlatformName | None = None
    gnmi_port: PositiveInt | None = None
    gpu: GpuSpec | None = None
    memory: MemorySpec | None = None
    firmware: tuple[FirmwareRule, ...] = ()

    @model_validator(mode="after")
    def _gpu_and_memory_together(self) -> Self:
        if (self.gpu is None) != (self.memory is None):
            raise ValueError("gpu and memory are planned together")
        return self

    @property
    def spec(self) -> HardwareSpec | None:
        if self.gpu is None or self.memory is None:
            return None
        return HardwareSpec(gpu=self.gpu, memory=self.memory)


class PlannedInterface(FrozenModel):
    name: str
    mac: MacAddress | None = None


class ExpectedNode(FrozenModel):
    """A server as NetBox plans it. None means NetBox does not record the value."""

    kind: Literal[Kind.SERVER] = Kind.SERVER
    name: str
    device_type: str
    serial: str | None = None
    credential_set: CredentialSetName | None = None
    platform: PlatformName | None = None
    bmc_address: str | None = None
    spec: HardwareSpec | None = None
    interfaces: tuple[PlannedInterface, ...] = ()
    firmware: tuple[FirmwareRule, ...] = ()

    @property
    def address(self) -> str | None:
        """Where racklint reads the server: its BMC."""
        return self.bmc_address


class PlannedLink(FrozenModel):
    """A planned cable from one of the switch's interfaces to an interface of another switch."""

    interface: str
    peer_device: str
    peer_interface: str
    # The far interface's NetBox label, such as a SONiC port alias.
    peer_interface_label: str | None = None


class UncheckableLink(FrozenModel):
    """A planned cable whose far end racklint cannot check against LLDP, and why."""

    interface: str
    reason: str


class ExpectedSwitch(FrozenModel):
    """A switch as NetBox plans it."""

    kind: Literal[Kind.SWITCH] = Kind.SWITCH
    name: str
    device_type: str
    serial: str | None = None
    credential_set: CredentialSetName | None = None
    platform: PlatformName | None = None
    gnmi_port: PositiveInt | None = None
    address: str | None = None
    firmware: tuple[FirmwareRule, ...] = ()
    links: tuple[PlannedLink, ...] = ()
    uncheckable_links: tuple[UncheckableLink, ...] = ()
    uncabled_interfaces: tuple[str, ...] = ()


# What a device reports


class Processor(FrozenModel):
    """One member of a system's Processors, as the BMC reports it."""

    id: str
    type: str
    model: str


class Gpu(FrozenModel):
    id: str
    model: str


class Dimm(FrozenModel):
    id: str
    capacity_mib: int


class NetworkPort(FrozenModel):
    id: str
    mac: MacAddress | None = None


class FirmwareItem(FrozenModel):
    id: str
    version: str


class ActualNode(FrozenModel):
    """A server as its BMC reports it."""

    serials: tuple[str, ...] = ()
    processors: tuple[Processor, ...] = ()
    dimms: tuple[Dimm, ...] = ()
    ports: tuple[NetworkPort, ...] = ()
    firmware: tuple[FirmwareItem, ...] = ()


class LldpNeighbour(FrozenModel):
    """What one of the switch's interfaces sees over LLDP."""

    interface: str
    system_name: str
    port_id: str
    port_description: str = ""


class ActualSwitch(FrozenModel):
    """A switch as it reports itself over gNMI. Its OS is the firmware item `OS`."""

    serials: tuple[str, ...] = ()
    firmware: tuple[FirmwareItem, ...] = ()
    neighbours: tuple[LldpNeighbour, ...] = ()


# The verdict


class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class Finding(FrozenModel):
    check: str
    message: str


class NodeResult(FrozenModel):
    node: str
    kind: Kind
    address: str | None
    status: Status
    findings: tuple[Finding, ...] = ()
    # What a check could not check. A note never fails a device.
    notes: tuple[Finding, ...] = ()
    error: str | None = None


class Report(FrozenModel):
    schema_version: Literal[1] = 1
    results: tuple[NodeResult, ...]

    def count(self, status: Status) -> int:
        return sum(1 for result in self.results if result.status == status)

    @property
    def passed(self) -> bool:
        return all(result.status == Status.PASS for result in self.results)
