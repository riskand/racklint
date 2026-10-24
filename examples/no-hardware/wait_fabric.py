# racklint/examples/no-hardware/wait_fabric.py
"""Wait until the example's fabric can be checked: every switch answers gNMI, and sees an LLDP neighbour on
each port that fabric/switches.toml cables.

Usage: python wait_fabric.py, after containerlab has deployed the lab. It reads the switches the way racklint
does, one login each per round, and exits 0 once they are ready. It exits 1 if they are not ready after
TIMEOUT_SECONDS, or as soon as a switch refuses the login.
"""

import asyncio
import sys
import time
from typing import Any

from pydantic import SecretStr

from racklint.credentials import Credentials
from racklint.gnmi import GnmiAccess, GnmiAuthError, GnmiError, GnmiReader
from racklint.models import ActualSwitch
from seed_fabric import HERE, SWITCH_LOGIN, load_fabric

# The CA that containerlab generates for the lab; the racklint service reads the same file.
CA_BUNDLE = HERE / "fabric" / "clab-racklint" / ".tls" / "ca" / "ca.pem"
TIMEOUT_SECONDS = 600
PAUSE_SECONDS = 10


def planned_interfaces(fabric: dict[str, Any]) -> dict[str, set[str]]:
    """Each switch's interfaces that the plan cables, from both ends of every link."""
    interfaces: dict[str, set[str]] = {switch["name"]: set() for switch in fabric["switches"]}
    for link in fabric["links"]:
        for end in link:
            switch, interface = end.split(":")
            interfaces[switch].add(interface)
    return interfaces


def missing_interfaces(planned: set[str], switch: ActualSwitch) -> set[str]:
    """The planned interfaces on which the switch sees no neighbour yet.

    Which neighbour it sees is racklint's to judge: the example plants swapped uplinks that must stay visible.
    """
    return planned - {neighbour.interface for neighbour in switch.neighbours}


async def missing_now(
    reader: GnmiReader, credentials: Credentials, address: str, planned: set[str]
) -> set[str]:
    """What `missing_interfaces` finds on the switch at `address`; all of `planned` if it is unreadable."""
    try:
        switch = await reader.read(address, credentials)
    except GnmiAuthError:
        raise
    except GnmiError:
        return planned
    return missing_interfaces(planned, switch)


async def wait_for_fabric(
    reader: GnmiReader,
    credentials: Credentials,
    addresses: dict[str, str],
    planned: dict[str, set[str]],
    wait_seconds: float,
) -> dict[str, set[str]]:
    """Read the switches that are not ready, round after round, until all are or `wait_seconds` have passed.

    Returns the interfaces still missing, by switch name: empty when the fabric is ready.
    """
    missing = dict(planned)
    deadline = time.monotonic() + wait_seconds
    while True:
        names = list(missing)
        readings = await asyncio.gather(
            *(missing_now(reader, credentials, addresses[name], planned[name]) for name in names)
        )
        for name, still_missing in zip(names, readings, strict=True):
            if still_missing:
                missing[name] = still_missing
            else:
                del missing[name]
                print(f"{name} is ready")
        if not missing or time.monotonic() >= deadline:
            return missing
        await asyncio.sleep(PAUSE_SECONDS)


async def main() -> int:
    fabric = load_fabric()
    addresses = {switch["name"]: switch["mgmt_ip"] for switch in fabric["switches"]}
    username, password = SWITCH_LOGIN
    credentials = Credentials(
        username=username, password=SecretStr(password), origin="seed_fabric.SWITCH_LOGIN"
    )
    reader = GnmiReader(GnmiAccess(ca_bundle=CA_BUNDLE))
    try:
        missing = await wait_for_fabric(
            reader, credentials, addresses, planned_interfaces(fabric), TIMEOUT_SECONDS
        )
    except GnmiAuthError as error:
        # Never retried: another try could lock the account.
        print(error, file=sys.stderr)
        return 1
    for name, interfaces in missing.items():
        print(f"{name} has no LLDP neighbour on {', '.join(sorted(interfaces))}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
