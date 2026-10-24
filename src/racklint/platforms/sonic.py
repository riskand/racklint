# racklint/src/racklint/platforms/sonic.py
"""The platform for switches that run SONiC.

It uses only names that `racklint` exports, as a platform in a package of its own would; a test checks it.

SONiC's gNMI server answers OpenConfig `/lldp/interfaces` as racklint reads it. Its `/components` has no
chassis serial that racklint recognises and, on 202411, no OS version. Both live in SONiC's databases, which
the same server serves under a request target (docs/spikes/sonic.md, findings 2 and 4).
"""

from collections.abc import Mapping

from racklint import (
    ActualSwitch,
    FirmwareItem,
    GnmiAccess,
    GnmiReader,
    GnmiRequest,
    OpenConfigSwitch,
    Update,
    openconfig_neighbours,
)

STATE_DB = "STATE_DB"
OTHERS = "OTHERS"
MANAGEMENT_PORT = "eth0"
# What SONiC's chassisd stores as the serial when the platform cannot tell it.
NOT_AVAILABLE = "N/A"

# What racklint reads from a SONiC switch, and where SONiC keeps it (docs/spikes/sonic.md, finding 4).
LLDP = GnmiRequest("/lldp/interfaces")
# The chassis serial, which pmon's chassis_db_init writes once from the platform's `get_serial()`.
CHASSIS_INFO = GnmiRequest("CHASSIS_INFO/chassis 1", target=STATE_DB, optional=True)
# The serial in the system EEPROM (ONIE TLV 0x23), which pmon's syseepromd writes. Not every platform has one.
EEPROM_SERIAL = GnmiRequest("EEPROM_INFO/0x23", target=STATE_DB, optional=True)
# The build of the OS, which sonic-gnmi reads from /etc/sonic/sonic_version.yml.
OS_VERSION = GnmiRequest("osversion/build", target=OTHERS)


def _serials(replies: Mapping[GnmiRequest, list[Update]]) -> tuple[str, ...]:
    """The chassis serial if it is reported, else the EEPROM's; none if neither is."""
    for request, name in ((CHASSIS_INFO, "serial"), (EEPROM_SERIAL, "Value")):
        serial = _field(replies[request], name)
        if serial and serial != NOT_AVAILABLE:
            return (serial,)
    return ()


def _field(updates: list[Update], name: str) -> str:
    """A field of the value a SONiC database or file answered; empty if the switch reports none.

    The value is a JSON object of strings. pygnmi has decoded it, so it arrives as a dict.
    """
    value = updates[0].get("val") if updates else None
    return str(value.get(name) or "") if isinstance(value, dict) else ""


def _os_version(updates: list[Update]) -> str:
    """The build version, unless the switch says it could not read it."""
    # sonic-gnmi (non_db_client.go, getBuildVersion) answers "sonic.NA" and the reason in `error` when it
    # cannot read /etc/sonic/sonic_version.yml.
    return "" if _field(updates, "error") else _field(updates, "build_version")


class SonicDialect:
    """SONiC: LLDP over OpenConfig; the serial and the OS version from SONiC's own databases."""

    requests = (LLDP, CHASSIS_INFO, EEPROM_SERIAL, OS_VERSION)

    def parse(self, replies: Mapping[GnmiRequest, list[Update]]) -> ActualSwitch:
        os_version = _os_version(replies[OS_VERSION])
        return ActualSwitch(
            serials=_serials(replies),
            firmware=(FirmwareItem(id="OS", version=os_version),) if os_version else (),
            # racklint checks fabric cabling, and SONiC's LLDP table includes its management port.
            neighbours=tuple(
                neighbour
                for neighbour in openconfig_neighbours(replies[LLDP])
                if neighbour.interface != MANAGEMENT_PORT
            ),
        )


SONIC_DIALECT = SonicDialect()


class Sonic(OpenConfigSwitch):
    """Switches that run SONiC, on its usual gNMI port, 8080.

    racklint always uses TLS, so the switch needs `GNMI|certs` in CONFIG_DB. It logs in with a username and a
    password, so `GNMI|gnmi` needs `user_auth = password`. SONiC checks each gNMI call with an SSH login to
    itself, so one read makes several logins.
    """

    name = "sonic"
    gnmi_port = 8080

    def reader(self, access: GnmiAccess) -> GnmiReader:
        return GnmiReader(access, dialect=SONIC_DIALECT)


SONIC = Sonic()
