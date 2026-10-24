# racklint

**Check servers and switches against the inventory before they go into service.**

racklint is a commissioning validator for bare-metal and GPU clusters. It reads what each server reports through
its BMC over [Redfish](https://www.dmtf.org/standards/redfish), and what each switch reports over
[gNMI](https://gnmi.openconfig.net) with OpenConfig models, and compares that with what
[NetBox](https://netbox.dev) says should be there. Every mismatch is flagged: a wrong GPU count, a wrong serial
number, firmware not on the approved list, a missing NIC, a cable that lands on the wrong switch port.

You can use it in two ways: as the `racklint` command, or as a Python library.

## Why

When a rack is brought up, the question is not "what hardware is in it?" but "is the hardware in it what we
ordered and planned for?" Existing tools such as [netbox-sync](https://github.com/bb-Ricardo/netbox-sync) and
[netbox-agent](https://github.com/Solvik/netbox-agent) answer the first question: they read the hardware and
write it into NetBox, so NetBox ends up describing whatever was found.

racklint works the other way. NetBox holds the planned state and stays the source of truth. racklint checks the
hardware against it, changes nothing, and fails loudly on any difference. That makes it useful as a gate: a
device only moves to the next stage of bring-up once racklint passes it.

It is read-only. It sends only GET requests to NetBox and to the BMCs, and only gNMI Get requests to the
switches. The one exception is logging in: a BMC session is opened with a POST and deleted when the read ends,
and an AppRole login to Vault is a POST. The host OS does not need to be running.

## Install

```bash
pip install racklint
```

To check switches as well, install the `network` extra, which adds the gNMI client
([pygnmi](https://github.com/akarneliuk/pygnmi)):

```bash
pip install "racklint[network]"
```

racklint needs Python 3.12 or later. Without the extra, a plan that contains switches stops with a
configuration error that names the extra to install.

## Quickstart

Point racklint at NetBox, give it one login for every BMC, and check a rack:

```bash
export RACKLINT_NETBOX_URL=https://netbox.example.net
export RACKLINT_NETBOX_TOKEN=nbt_...
export RACKLINT_BMC_USERNAME=admin
export RACKLINT_BMC_PASSWORD_FILE=/run/secrets/bmc-password
racklint check --rack R01
```

Each server in the rack needs a serial, an out-of-band IP and a `racklint` config context with its planned GPUs,
memory and firmware: see [What NetBox must hold](#what-netbox-must-hold). To check the rack's switches too,
install the `network` extra and set `RACKLINT_SWITCH_USERNAME`, `RACKLINT_SWITCH_PASSWORD_FILE` and
`RACKLINT_SWITCH_CA_BUNDLE`. The command exits `0` only when every device passes.

[Settings](#settings) lists every setting, and [Credentials](#credentials) covers Vault and credentials files.
No NetBox to hand? [Try it without hardware](#try-it-without-hardware).

## Command line

`racklint --version` prints the installed version.

### Settings

The command reads its settings from `RACKLINT_*` environment variables, or from a `.env` file in the working
directory.

| Setting | Default | Meaning |
|---|---|---|
| `RACKLINT_NETBOX_URL` | required | Base URL of NetBox, for example `https://netbox.example.net` |
| `RACKLINT_NETBOX_TOKEN` | required | NetBox API token |
| `RACKLINT_BMC_USERNAME` | see [Credentials](#credentials) | User name for every BMC |
| `RACKLINT_BMC_PASSWORD` | see [Credentials](#credentials) | Password for every BMC |
| `RACKLINT_BMC_PASSWORD_FILE` | unset | A file that holds the BMC password, instead of `RACKLINT_BMC_PASSWORD` |
| `RACKLINT_BMC_SCHEME` | `https` | `https` or `http` |
| `RACKLINT_BMC_VERIFY_TLS` | `true` | Verify BMC TLS certificates |
| `RACKLINT_BMC_CA_BUNDLE` | unset | CA bundle file used to verify BMC certificates |
| `RACKLINT_SWITCH_USERNAME` | see [Credentials](#credentials) | User name for every switch |
| `RACKLINT_SWITCH_PASSWORD` | see [Credentials](#credentials) | Password for every switch |
| `RACKLINT_SWITCH_PASSWORD_FILE` | unset | A file that holds the switch password, instead of `RACKLINT_SWITCH_PASSWORD` |
| `RACKLINT_GNMI_PORT` | `57400` | The gNMI port of the switches whose [platform](#platforms) and config context name none |
| `RACKLINT_SWITCH_VERIFY_TLS` | `true` | Verify switch TLS certificates. Needs `RACKLINT_SWITCH_CA_BUNDLE` |
| `RACKLINT_SWITCH_CA_BUNDLE` | unset | CA bundle file used to verify switch certificates |
| `RACKLINT_CONCURRENCY` | `16` | Devices read at once |
| `RACKLINT_BMC_CONCURRENCY` | `4` | Requests in flight to one BMC |
| `RACKLINT_CREDENTIALS_FILE` | unset | A [credentials file](#the-credentials-file) |
| `RACKLINT_VAULT_ADDR` | unset | Base URL of Vault. Setting it makes Vault the credential source |
| `RACKLINT_VAULT_TOKEN` | unset | A Vault token |
| `RACKLINT_VAULT_TOKEN_FILE` | unset | A file that holds the Vault token, instead of `RACKLINT_VAULT_TOKEN` |
| `RACKLINT_VAULT_ROLE_ID` | unset | The role ID of a Vault AppRole |
| `RACKLINT_VAULT_SECRET_ID_FILE` | unset | A file that holds the AppRole's secret ID |
| `RACKLINT_VAULT_MOUNT` | `secret` | The KV version 2 mount |
| `RACKLINT_VAULT_PREFIX` | `racklint` | The path under the mount where racklint's secrets live |
| `RACKLINT_VAULT_CA_BUNDLE` | unset | CA bundle file used to verify Vault's certificate. Without it, the system trust store |

racklint builds a reader only for the kinds of device in the plan. A run that checks only switches needs no BMC
settings, and a run that checks only servers needs no switch settings. The BMC and switch settings are one of
three ways to give racklint its credentials, and they set a single login for all BMCs and one for all
switches. [Credentials](#credentials) describes the other two.

A token that starts with `nbt_` (NetBox 4.5 and later) is sent as a bearer token. Any other token is sent as a
legacy `Token`.

#### Passwords from files

A password in an environment variable can leak into process listings and CI logs. Instead of
`RACKLINT_BMC_PASSWORD` or `RACKLINT_SWITCH_PASSWORD`, you can set `RACKLINT_BMC_PASSWORD_FILE` or
`RACKLINT_SWITCH_PASSWORD_FILE` to the path of a file that holds the password. A final newline is ignored.
Setting both the password and its file is an error, and so is a file that cannot be read or is empty. The error
names the file, never its content.

racklint reads the file at the start of every run. This suits a secret manager that mounts secrets as files, such
as Docker or Kubernetes secrets or Vault Agent: when the manager rotates a password by rewriting the file,
racklint uses the new password on its next run, and no secret sits in the environment.

### Run a check

```bash
racklint check --rack R01
racklint check --device gpu-01 --device gpu-02
```

Pass exactly one of `--rack` and `--device`. A run checks the servers and switches among the devices and prints
one report for all of them.

| Option | Meaning |
|---|---|
| `--rack NAME` | Check every device in this NetBox rack |
| `--site SLUG` | Site slug. Needed only if the rack name exists in several sites |
| `--device NAME` | Check this device. Repeat for more |
| `--baseline PATH` | Firmware baseline file. Optional. Overrides the firmware rules planned in NetBox |
| `--format table\|json` | Output format. Default `table` |
| `--concurrency N` | Devices to read at once. Overrides `RACKLINT_CONCURRENCY` |
| `--ca-bundle PATH` | CA bundle for BMC certificates. Overrides `RACKLINT_BMC_CA_BUNDLE` |
| `--insecure` | Do not verify BMC certificates. Overrides `RACKLINT_BMC_VERIFY_TLS` |

`--ca-bundle` and `--insecure` apply to BMCs only. Switches have their own settings, because they usually sit in
a different PKI.

Devices are read in groups: one per [platform](#platforms) and, for switches, per gNMI port. The servers are
read first and then the switches, each group with up to `--concurrency` devices at once.

### Firmware rules

The approved firmware versions live in NetBox, in the `firmware` list of the `racklint` config context, next to
the rest of the plan. Assign the context to the device type, so every device of that type gets the rules:

```yaml
racklint:
  firmware:
    - match: "BMC"
      approved: ["1.4.0"]
    - match: "GPU_*"
      approved: ["96.00.89.00.01"]
```

Each `match` is a case-sensitive glob on the `Id` of a firmware item the device reports. Versions must be
quoted strings. A rule must match at least one item, and every item it matches must run an approved version. A
switch reports its OS as the firmware item `OS`, so the rule `match: "OS"` covers a switch's OS version.

A device whose type has no rules fails with `no firmware baseline for device type <slug>`.

`--baseline` is an override. It names a YAML file with the approved versions for each device type, and when you
give it, racklint uses the file's rules for every device and ignores the rules in the config contexts. It
replaces the rules and never merges with them. Use it to check against a list kept in a repository.

```yaml
device_types:
  gpu-node-h100:             # NetBox device-type slug
    - match: "BMC"
      approved: ["1.4.0"]
    - match: "GPU_*"
      approved: ["96.00.89.00.01"]
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Every device passed |
| `1` | At least one device failed, or could not be read |
| `2` | Usage or configuration error: bad arguments, invalid settings or baseline, missing credentials for a kind of device in the plan, NetBox unreachable or returning an error, rack or device not found |

Use the exit code to gate a pipeline.

### Output

The table has one row per device and one line per reason, then a summary. A line that starts with `note:` is
not a reason to fail: it says what a check could not check (see below).

```console
┏━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Node ┃ Kind   ┃ Address    ┃ Status ┃ Reasons                                ┃
┡━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ n01  │ server │ 192.0.2.11 │ PASS   │                                        │
│ n02  │ server │ 192.0.2.12 │ PASS   │                                        │
│ n03  │ server │ 192.0.2.13 │ FAIL   │ gpu: 7 found, 8 planned                │
│ n04  │ server │ 192.0.2.14 │ ERROR  │ no credentials for n04: set bmc-r02    │
│      │        │            │        │ not found in Vault                     │
│ n05  │ server │ 192.0.2.15 │ FAIL   │ memory: 31 DIMMs (1984 GiB), 32        │
│      │        │            │        │ planned (2048 GiB)                     │
│      │        │            │        │ firmware: BMC 1.3.2 not in approved    │
│      │        │            │        │ [1.4.0]                                │
│ n06  │ server │ 192.0.2.16 │ PASS   │                                        │
└──────┴────────┴────────────┴────────┴────────────────────────────────────────┘
3 passed, 2 failed, 1 errored
```

The kind is `server` or `switch`. The address is where racklint read the device: a server's BMC address, or a
switch's management address. A device has status `PASS`, `FAIL` or `ERROR`. `ERROR` means racklint could not
read the device (a connection failure after retries, a timeout, a rejected login, no credentials for the device
or an invalid reply). No checks run for that device, and the reason is the error.

With `--format json` the output is the report as JSON. This is one failing server:

```json
{
  "schema_version": 1,
  "results": [
    {
      "node": "n03",
      "kind": "server",
      "address": "192.0.2.13",
      "status": "fail",
      "findings": [
        {
          "check": "gpu",
          "message": "7 found, 8 planned"
        }
      ],
      "notes": [],
      "error": null
    }
  ]
}
```

`status` is `pass`, `fail` or `error`. `error` is set only when the status is `error`. `notes` lists what a
check could not check, in the same form as a finding, such as `nic: MACs not checked: the BMC reports none`.
A note never fails a device, so a device with notes can pass. The table shows each note after the device's
findings, as `note: <check>: <message>`.

## Credentials

Each device is read with its own credentials. racklint takes the first of these that exists, and uses only that
one:

1. **A factory secret for the device's serial number.** A new BMC often still has the password it left the
   factory with, one per unit. Store it under the device's serial and racklint logs in with it.
2. **The credential set the device's config context names.** In NetBox, put the name in the `racklint` key of a
   config context, which the site, rack, role or device type can hold like any config context, or in the
   device's own local context:

   ```yaml
   racklint:
     credentials: bmc-dc1
   ```

   The name is made of letters, digits, `.`, `_` and `-`, and starts with a letter or digit. NetBox holds the
   name of a set, never a password.
3. **The default set for the device's kind:** `default-server` for a server, `default-switch` for a switch.

A device is logged in to once. If the credentials found are refused, racklint reports the device as an error,
names where they came from, and does not try another set: a BMC or switch that counts failed logins could lock
the account. A refused factory secret adds that it should be removed once the device's password is rotated.
The error for a device whose set does not exist reads `no credentials for n04: set bmc-r02 not found in Vault`.
The other devices are read as usual.

### Where the credentials come from

A run uses exactly one source:

| Settings given | Source |
|---|---|
| `RACKLINT_VAULT_ADDR` | Vault |
| `RACKLINT_CREDENTIALS_FILE` | the credentials file |
| neither | the BMC and switch settings: `RACKLINT_BMC_*` become the set `default-server`, `RACKLINT_SWITCH_*` become `default-switch` |

Giving more than one source is an error, and so is giving the BMC or switch settings with Vault or a
credentials file, because they would not be used. Vault settings without `RACKLINT_VAULT_ADDR` are an error
too. racklint checks this before it reads NetBox, and exits with code 2. A setup that uses only the BMC and
switch settings has no factory secrets and no named sets, so every server and switch is read with the one
login, and a device whose config context names a set is an error, because that set does not exist.

Each set and each serial is looked up once per run, and kept in memory for that run only, as a secret that
prints as `**********`.

### The credentials file

`RACKLINT_CREDENTIALS_FILE` names a YAML file:

```yaml
sets:
  default-server: {username: admin, password_file: /run/secrets/bmc}
  bmc-dc1:        {username: admin, password_env: BMC_DC1_PASSWORD}
  default-switch: {username: admin, password_file: /run/secrets/switch}
factory:                       # by serial
  SN-0004: {username: root, password_file: /run/secrets/factory/SN-0004}
```

Each entry takes its password from `password_file` or `password_env`, exactly one of them. A password written
into the file is an error, so the file holds no secrets and can live in git. Password files are read as
described under [Passwords from files](#passwords-from-files), at the start of the run. An unknown key, an
unset variable, an empty password file and a set name that is not a safe path segment are errors, and none of
the messages shows a password.

### Vault

racklint reads secrets from Vault's KV version 2 engine over its HTTP API, with no extra dependency. Each secret
holds the keys `username` and `password`:

| Secret | Path |
|---|---|
| A set | `<mount>/<prefix>/sets/<name>` |
| A factory secret | `<mount>/<prefix>/factory/<serial>` |

The mount is `secret` and the prefix `racklint` unless `RACKLINT_VAULT_MOUNT` and `RACKLINT_VAULT_PREFIX` say
otherwise. Write one with, for example, `vault kv put secret/racklint/sets/bmc-dc1 username=admin
password=...`. A missing secret is not an error for a factory secret: racklint moves on to the device's set. A
missing set, and any other answer from Vault, is that device's error; the message names the path and the status,
never a token.

racklint logs in to Vault in one of two ways, and exactly one must be given:

- a **token**, in `RACKLINT_VAULT_TOKEN` or in the file `RACKLINT_VAULT_TOKEN_FILE` names; or
- an **AppRole**, with `RACKLINT_VAULT_ROLE_ID` and the file `RACKLINT_VAULT_SECRET_ID_FILE` names. racklint
  logs in once per run.

Files are read at the start of the run. Prefer a token file, filled by Vault Agent, to a token in the
environment, which can leak into process listings and CI logs. Give racklint a policy that can read only
`<mount>/data/<prefix>/*`.

### Redfish sessions

A BMC is logged in to once per read, not on every request. racklint reads the service root without
credentials, as Redfish allows. If the root links a session collection, racklint opens one session, sends its
token with every request, and deletes the session when the read ends, also when the read fails. If a delete
fails, the BMC's session timeout removes the session. A BMC without a session service is read with Basic
authentication. One that refuses the anonymous service root is asked again with Basic authentication. A
refused login (HTTP 401, or 403, which racklint reports as `access denied (HTTP 403)`) is final. Connection
errors, timeouts, 429 and 5xx on a login are retried like any request.

## Python library

The names exported from `racklint` (its `__all__`) are the public API. Anything else may change before 1.0. The
package ships type hints (`py.typed`). The names fall into these groups:

| Group | Names |
|---|---|
| State | `Kind`, `PlanContext`, `ExpectedNode`, `PlannedInterface`, `HardwareSpec`, `GpuSpec`, `MemorySpec`, `ActualNode`, `Processor`, `Gpu`, `Dimm`, `NetworkPort`, `ExpectedSwitch`, `PlannedLink`, `ActualSwitch`, `LldpNeighbour`, `FirmwareItem` |
| Verdict | `Report`, `NodeResult`, `Finding`, `Status` |
| Running checks | `check_plan`, `check_nodes`, `iter_results`, `ActualReader`, `Check`, `Notes`, `server_checks`, `switch_checks`, `SerialCheck`, `GpuCheck`, `GpuRule`, `RedfishGpus`, `OpenBmcAccelerators`, `GPU_RULES`, `MemoryCheck`, `NicCheck`, `FirmwareCheck`, `LinkCheck` |
| Platforms | `ServerPlatform`, `SwitchPlatform`, `OpenConfigSwitch`, `Platforms`, `load_platforms`, `UnknownPlatform` |
| Sources | `NetBoxSource`, `netbox_client`, `RedfishReader`, `BmcAccess`, `GnmiReader`, `GnmiAccess`, `GnmiClient`, `FirmwareBaseline`, `FirmwareRule` |
| gNMI dialects | `GnmiDialect`, `GnmiRequest`, `OPENCONFIG`, `openconfig_components`, `openconfig_neighbours`, `Update` |
| Credentials | `Credentials`, `CredentialSource`, `CredentialStore`, `CredentialLookup`, `StaticStore`, `Identified`, `VaultStore`, `VaultAccess`, `TokenAuth`, `AppRoleAuth`, `vault_client` |
| Errors | `RacklintError`, `BaselineError`, `NetBoxError`, `InventoryError`, `AuthenticationError`, `RedfishError`, `GnmiError`, `GnmiNotFound`, `CredentialError`, `VaultError` |

The library is async and does not read `RACKLINT_*` settings: you pass it explicit objects. `NetBoxSource`
returns both kinds of device, `ExpectedNode` for a server and `ExpectedSwitch` for a switch. You split them,
read each kind with its own reader and run its own checks: `server_checks()` for servers, `switch_checks()` for
switches. A `CredentialSource` gives each device its credentials; `CredentialLookup` is the one that applies
[the lookup order](#credentials) to a `CredentialStore`: a `StaticStore` that you fill, a `VaultStore`, or your
own class. This example reads the plan for rack R01 from NetBox, checks the servers and the switches, and prints
a verdict per device. It takes its connection details from the environment.

```python
import asyncio
import os
from pathlib import Path

from pydantic import SecretStr

from racklint import (
    BmcAccess,
    CredentialLookup,
    Credentials,
    ExpectedNode,
    ExpectedSwitch,
    GnmiAccess,
    GnmiReader,
    NetBoxSource,
    RedfishReader,
    StaticStore,
    check_nodes,
    netbox_client,
    server_checks,
    switch_checks,
)


async def main() -> None:
    async with netbox_client(os.environ["NETBOX_URL"], os.environ["NETBOX_TOKEN"]) as client:
        nodes = await NetBoxSource(client).rack("R01")
    servers = [node for node in nodes if isinstance(node, ExpectedNode)]
    switches = [node for node in nodes if isinstance(node, ExpectedSwitch)]

    credentials = CredentialLookup(
        StaticStore(
            {
                "default-server": Credentials(
                    username=os.environ["BMC_USERNAME"],
                    password=SecretStr(os.environ["BMC_PASSWORD"]),
                    origin="set default-server",
                ),
                "default-switch": Credentials(
                    username=os.environ["SWITCH_USERNAME"],
                    password=SecretStr(os.environ["SWITCH_PASSWORD"]),
                    origin="set default-switch",
                ),
            }
        )
    )
    bmc = BmcAccess()
    switch = GnmiAccess(ca_bundle=Path("switch-ca.pem"))
    server_report = await check_nodes(
        servers, RedfishReader(bmc), server_checks(), credentials, concurrency=16
    )
    switch_report = await check_nodes(
        switches, GnmiReader(switch), switch_checks(), credentials, concurrency=16
    )

    results = (*server_report.results, *switch_report.results)
    for result in results:
        print(result.node, result.kind, result.status, [finding.message for finding in result.findings])
    print("all passed" if server_report.passed and switch_report.passed else "not all passed")


asyncio.run(main())
```

`check_nodes` returns a `Report` with the results in the order of the devices you pass. To act on each result as
soon as it is ready, for live progress, loop over `iter_results` instead. It takes the same arguments and yields
`NodeResult`s in the order they finish:

```python
import asyncio
import os

from pydantic import SecretStr

from racklint import (
    BmcAccess,
    CredentialLookup,
    Credentials,
    ExpectedNode,
    NetBoxSource,
    RedfishReader,
    StaticStore,
    iter_results,
    netbox_client,
    server_checks,
)


async def main() -> None:
    async with netbox_client(os.environ["NETBOX_URL"], os.environ["NETBOX_TOKEN"]) as client:
        nodes = await NetBoxSource(client).devices(["gpu-01", "gpu-02"])
    servers = [node for node in nodes if isinstance(node, ExpectedNode)]

    login = Credentials(
        username=os.environ["BMC_USERNAME"],
        password=SecretStr(os.environ["BMC_PASSWORD"]),
        origin="set default-server",
    )
    credentials = CredentialLookup(StaticStore({"default-server": login}))
    async for result in iter_results(
        servers, RedfishReader(BmcAccess()), server_checks(), credentials, concurrency=16
    ):
        print(result.node, result.status)


asyncio.run(main())
```

To read each device with the reader and the checks of its [platform](#platforms), as the command does,
pass the whole plan to `check_plan`. It groups the devices by platform and, for switches, by gNMI port, and
returns one `Report` with the results in the order of the plan. You give it the access settings of every kind
of device in the plan. In the first example, this replaces the two lists of servers and switches and the two
calls of `check_nodes`:

```python
from pathlib import Path

from racklint import BmcAccess, GnmiAccess, check_plan, load_platforms

report = await check_plan(
    nodes,
    load_platforms(),
    credentials,
    bmc=BmcAccess(),
    gnmi=GnmiAccess(ca_bundle=Path("switch-ca.pem")),
    concurrency=16,
)
```

A device that names a platform which is not installed is that device's `error` result; the others are read.

`NetBoxSource` raises `NetBoxError` and `FirmwareBaseline.load` raises `BaselineError`; both are subclasses of
`RacklintError`. A device that cannot be read, or has no credentials, does not raise: its result gets status
`error`; a refused login is an `AuthenticationError` inside the reader and is reported the same way. Reading a switch
needs the `network` extra, and without it `GnmiReader` raises `RacklintError`; the gNMI names are importable
either way.

`FirmwareBaseline.applied_to(node)` returns the node with the baseline's rules for its device type in place of
its planned rules, as `--baseline` does for every device. A `FirmwareBaseline` can also be built from a dict
with `FirmwareBaseline.model_validate`, so an application can keep the baseline in its own database.

`GpuCheck` finds a server's GPUs with `GpuRule`s: `GpuCheck(rules)` takes any sequence of objects with a
`find(actual, planned)` method that returns the `Gpu`s found in an `ActualNode`, and `server_checks()` uses
`GPU_RULES`. To teach racklint another way a BMC exposes GPUs, add a rule after the built-in ones:

```python
from racklint import GPU_RULES, ActualNode, Gpu, GpuCheck, GpuSpec


class VendorGpus:
    """A BMC that reports its GPUs as processors of type OEM with the planned model."""

    def find(self, actual: ActualNode, planned: GpuSpec) -> tuple[Gpu, ...]:
        return tuple(
            Gpu(id=processor.id, model=processor.model)
            for processor in actual.processors
            if processor.type == "OEM" and processor.model == planned.model
        )


check = GpuCheck([*GPU_RULES, VendorGpus()])
```

A check that can also say what it could not check has a `notes(expected, actual)` method beside `run`: that is
the `Notes` protocol, which `NicCheck` follows. `check_nodes` puts the messages on the result as `notes`, and
they never fail the device. A check without `notes` works as before, so `Check` is unchanged.

The NetBox source is optional. A NetBox plugin runs inside NetBox, so it can build `ExpectedNode`s and
`ExpectedSwitch`es from the Django ORM instead of the REST API, then call `asyncio.run(check_nodes(...))` from a
background job, which is synchronous code.

## What it checks

| Check | Applies to | Fails when | Example reason |
|---|---|---|---|
| Serial | servers, switches | The device's serial matches none of the serials the device reports | `serial: expected SN-R01-03, device reports SN-R01-30` |
| GPU | servers | The GPU count differs, or a GPU's model differs from the planned model | `gpu: 7 found, 8 planned` |
| Memory | servers | The enabled DIMM count or the total capacity differs | `memory: 31 DIMMs (1984 GiB), 32 planned (2048 GiB)` |
| NIC | servers | A planned MAC address is not reported, or fewer ports are reported than interfaces are planned | `nic: eth2 (02:00:00:00:03:03) not found` |
| Firmware | servers, switches | The device type has no rules, a rule matches no firmware item, or a matched item's version is not approved | `firmware: BMC 1.3.2 not in approved [1.4.0]` |
| Link | switches | A planned switch-to-switch link is not seen, or is seen on the wrong far end; a port with no planned cable sees a neighbour; or a planned link cannot be checked | `link: ethernet-1/1 expected spine1 ethernet-1/3, sees spine1 ethernet-1/4` |

A device passes only if every check passes. Firmware items that match no rule are ignored, and so are reported
ports beyond the plan. A check may also add a note about what it could not check, which does not fail the
device.

If NetBox lacks a value a check needs, the device fails with `not planned in NetBox: <item>`. A device with no
address to read it at fails with that reason alone: `BMC address` for a server, `management address` for a
switch.

### What NetBox must hold

For a server:

| Item | Where racklint reads it |
|---|---|
| Serial | The device's `serial` field |
| BMC address | The device's out-of-band IP (`oob_ip`). The prefix length is dropped |
| Device type | The device type's slug |
| NICs | The device's physical interfaces that are not management-only. A MAC address is compared if the interface has a primary MAC; without one, racklint only counts the port |
| GPU and memory | The `racklint` key of the device's config context |
| Firmware rules | The `firmware` list in the `racklint` key of the config context, unless `--baseline` is given |
| Platform | `platform` in the `racklint` key of the config context. Optional: see [Platforms](#platforms) |

Assign the config context to the device type, so every device of that type gets it:

```yaml
racklint:
  gpu:    {count: 8, model: "NVIDIA H100 80GB HBM3"}
  memory: {dimm_count: 32, total_gib: 2048}
  firmware:
    - {match: "BMC", approved: ["1.4.0"]}
```

An invalid `racklint` config context is a configuration error and stops the run with exit code `2`.

On the BMC side, racklint starts at `/redfish/v1` and follows the links the service returns. It hardcodes no
vendor paths. It reads systems, processors, memory, network adapters and the firmware inventory. A network
adapter's NIC ports are its `NetworkDeviceFunctions` or, when it has none, its `Ports`; a port's MAC is the
first of its `Ethernet.AssociatedMACAddresses`, if it reports any. GPUs are found by rules, tried in order:
Processors of type GPU (the DMTF standard), then Processors of type Accelerator with the planned GPU model
(upstream OpenBMC). The first rule that finds any decides. It logs in once per BMC with a Redfish session,
sends the session's `X-Auth-Token` with every request and logs out when the read ends; a BMC without a session
service is read with HTTP Basic authentication. It makes up to three attempts at a request that fails with a
connection error, a timeout, HTTP 5xx or 429, and uses a connect timeout of 5 seconds and a read timeout of 15
seconds. A refused login is never retried, because another try with a wrong or just-rotated password could
lock the account.

## Switches

A switch is a device whose type has `kind: switch` in its config context. The default kind is `server`.
Assign the context to the switch's device type:

```yaml
racklint:
  kind: switch
  firmware:
    - {match: "OS", approved: ["v26.7.2-519-g94fbd638fa8"]}
```

For a switch, racklint reads these from NetBox:

| Item | Where racklint reads it |
|---|---|
| Serial | The device's `serial` field |
| Management address | The device's out-of-band IP (`oob_ip`), or its primary IP (`primary_ip`) if there is no out-of-band IP |
| Firmware rules | The `firmware` list in the config context, unless `--baseline` is given. The OS is the rule `match: "OS"` |
| Planned links | The device's physical interfaces that are not management-only and whose cable has status `planned` or `connected` |
| Far interface's label | The `label` of the interface at the far end of a planned link. Optional: see [The cabling rule](#the-cabling-rule) |
| Platform and gNMI port | `platform` and `gnmi_port` in the `racklint` key of the config context. Optional: see [Platforms](#platforms) |

It reads these from the switch, over gNMI with OpenConfig models:

| Item | OpenConfig source |
|---|---|
| Serial | `state/serial-no` of the `/components/component` of type `CHASSIS` |
| OS version | `state/software-version` of the first component, in the order `OPERATING_SYSTEM`, `CONTROLLER_CARD`, `CHASSIS`, that reports one. It is the firmware item `OS` |
| LLDP neighbours | `system-name`, `port-id`, `port-id-type` and `port-description` of each neighbour on `/lldp/interfaces` |

The `sonic` platform reads the serial and the OS version from SONiC's own databases instead: see
[SONiC](#sonic).

The OS version is compared exactly, as firmware is: the approved string is what the switch reports, build suffix
included.

racklint connects to each switch on its gNMI port, which is `RACKLINT_GNMI_PORT` unless the switch's
[platform](#platforms) or config context names another, with `RACKLINT_SWITCH_USERNAME` and
`RACKLINT_SWITCH_PASSWORD`. To verify the switch's TLS certificate it needs a CA bundle in
`RACKLINT_SWITCH_CA_BUNDLE`, because pygnmi cannot use the system trust store. Set
`RACKLINT_SWITCH_VERIFY_TLS=false` to accept any certificate instead. Requests are retried like Redfish
requests, up to three attempts, so a switch that is still starting does not show as an error, but a refused
login is not retried.

### The cabling rule

`LinkCheck` compares the planned cables of a switch with what the switch sees over LLDP. It checks a planned link
when the far end is a switch. Servers send no LLDP while no OS is running, so a link to a device planned as a
server is left out. The far end is the interface NetBox traces the cable to, through patch panels; where NetBox
has traced no path, it is the cable's own far end when that is an interface. A switch's links into another rack
are checked from this switch's side, and the far switch need not be part of the run.

A link matches when the LLDP neighbour on the local port is the planned far end:

- the neighbour's `system-name` equals the far device's NetBox name, or its short form before the first dot
  (switches often report an FQDN), and
- its `port-id` or `port-description` equals the far interface's NetBox name (some vendors put a MAC address
  in `port-id`), or its NetBox label, if it has one.

The label is for switches that advertise another name for a port than the one NetBox holds. SONiC, for one,
advertises a port's alias over LLDP. Record the alias as the label of the interface in NetBox, and racklint
accepts it. A finding still names the planned interface by its NetBox name.

The findings:

```text
link: ethernet-1/1 expected spine1 ethernet-1/1, sees spine1 ethernet-1/2
link: ethernet-1/3 expected spine1 ethernet-1/3, no LLDP neighbour
link: ethernet-1/5 not planned in NetBox, sees spine1 ethernet-1/4
link: ethernet-1/6 cannot be checked: spine2 has no racklint config context
link: ethernet-1/7 cannot be checked: its cable does not reach an interface in NetBox
```

A planned link is never dropped silently. If the far device has no `racklint` config context, so racklint does
not know its kind, or the cable does not reach an interface in NetBox, the link is reported as one that cannot be
checked. A port with no planned cable that sees any neighbour is a finding: the cable is there, but not in the
plan. Management ports are never considered, because switches see each other on them.

NetBox interface names must match the switch's own interface names, for example `ethernet-1/1`.

## Platforms

A platform is a family of devices: how racklint reads them, and which checks judge them. A device names its
platform with `racklint.platform` in its `racklint` config context. A device that names none uses the default
platform of its kind, so nothing changes for a setup that has never named one.

| Platform | Kind | Read with | Checks |
|---|---|---|---|
| `redfish` (default) | server | Redfish | `server_checks()`; GPUs are processors of type `GPU`, or `Accelerator` of the planned model |
| `openbmc` | server | Redfish | the same, but as upstream OpenBMC reports: GPUs are only the `Accelerator`s of the planned model, and when the BMC reports no MAC on any port, its NIC ports are counted, with a note that the MACs were not checked |
| `openconfig` (default) | switch | gNMI, OpenConfig models | `switch_checks()` |
| `srlinux` | switch | gNMI, OpenConfig models | the same; a named place for SR Linux's differences, of which there are none yet |
| `sonic` | switch | gNMI: OpenConfig LLDP, and SONiC's databases for the serial and the OS version | the same as `openconfig`; see [SONiC](#sonic) |

NetBox merges the config contexts that match a device, and can assign one to a NetBox Platform. So you attach
the name to your own NetBox Platform, however you named it there, and every device of that platform gets it:

```yaml
racklint:
  platform: openbmc
```

A switch platform may have a usual gNMI port of its own. `racklint.gnmi_port` in a config context overrides it
for a site, a platform or one device. A switch is read on the first of these that exists: the device's
`racklint.gnmi_port`, its platform's port, `RACKLINT_GNMI_PORT`. `sonic` names 8080; the other built-in
platforms name no port, so they use `RACKLINT_GNMI_PORT`, `57400` unless set. racklint never reads gNMI
without TLS, whatever the port.

```yaml
racklint:
  gnmi_port: 9339
```

A name that no installed platform has is an error for that device alone, and the other devices are read as
usual. The result is `error`, with the reason:

```text
no racklint platform 'arista-eos': install one, or remove racklint.platform
```

So is a platform of the other kind, such as a switch platform named by a server. Platforms come from racklint
and from any installed package. Two platforms with one name stop the run before anything is read, and so does
a platform whose name is not its entry point's.

### SONiC

The `sonic` platform reads a switch that runs SONiC through its gNMI server, on port 8080 unless the switch's
config context says otherwise. SONiC's `/components` has no component that racklint takes for the chassis and,
on 202411, no OS version, so the platform reads these from SONiC itself:

| Item | Where racklint reads it |
|---|---|
| LLDP neighbours | OpenConfig `/lldp/interfaces`, as for any switch. Neighbours on `eth0`, SONiC's management port, are left out |
| Serial | `STATE_DB` table `CHASSIS_INFO`, key `chassis 1`, field `serial`; if that is missing, empty or `N/A`, `STATE_DB` table `EEPROM_INFO`, key `0x23`, field `Value`. A switch with neither has no serial to compare |
| OS version | The target `OTHERS`, path `osversion/build`, field `build_version`. It is the firmware item `OS` |

The switch needs two settings, because racklint logs in with a username and a password over TLS:

- `GNMI|certs` in CONFIG_DB, with the server certificate, its key and the CA. Without it SONiC does not serve
  gNMI over TLS, and racklint never reads gNMI without TLS.
- `user_auth = password` in `GNMI|gnmi`.

Things to know:

- SONiC checks each gNMI call with an SSH login to itself, so one read of a switch makes about five logins,
  and `sshd` must accept the password. A refused login is never retried.
- SONiC names its ports `Ethernet0` and advertises their vendor alias, such as `fortyGigE0/0`, as the LLDP
  `port-id`. Name the interfaces in NetBox as SONiC does, and record the alias as the label of each interface
  that a SONiC neighbour sees; [The cabling rule](#the-cabling-rule) says how a label is matched.
- When SONiC cannot read its version file, it answers `sonic.NA` and the reason as `error`. racklint then
  reports no OS version, so the firmware rule for `OS` matches no item.
- `sonic` is tested against recordings shaped from SONiC's source code, not against a running switch, because
  the public images do not give one that serves gNMI without setup. If you run SONiC, an anonymised capture of
  what your switches return would help; [CONTRIBUTING.md](CONTRIBUTING.md#recordings) says what to remove.

## Writing a platform

A platform is an object with a `name`, a `kind` and two methods. racklint defines two protocols, so a platform
needs no racklint base class:

```python
class ServerPlatform(Protocol):
    """One family of servers: how to read their BMCs, and which checks judge them."""

    name: str  # read-only
    kind: Literal[Kind.SERVER]

    def reader(self, access: BmcAccess) -> ActualReader[ActualNode]: ...
    def checks(self) -> Sequence[Check[ExpectedNode, ActualNode]]: ...


class SwitchPlatform(Protocol):
    """One family of switches: how to read them, and which checks judge them."""

    name: str  # read-only
    kind: Literal[Kind.SWITCH]
    gnmi_port: int | None  # its usual gNMI port; None for RACKLINT_GNMI_PORT

    def reader(self, access: GnmiAccess) -> ActualReader[ActualSwitch]: ...
    def checks(self) -> Sequence[Check[ExpectedSwitch, ActualSwitch]]: ...
```

- `reader` gets the access settings of its protocol, never racklint's whole settings, and returns an
  `ActualReader`: the object that reads one device. `RedfishReader` and `GnmiReader` are two.
- `checks` returns the checks that judge the device. They can be racklint's, tuned as `openbmc` tunes
  `GpuCheck`, or your own.
- Before 1.0, a change to these protocols that would break a platform raises the minor version and is listed
  in the [changelog](CHANGELOG.md).

A platform in a package of its own is registered with an entry point, whose name is the platform's name. This
one reads the switches of an imaginary Acme OS with the OpenConfig reader and the usual checks, on Acme's gNMI
port:

```python
# racklint_acme.py
from collections.abc import Sequence
from typing import Literal

from racklint import (
    ActualReader,
    ActualSwitch,
    Check,
    ExpectedSwitch,
    GnmiAccess,
    GnmiReader,
    Kind,
    switch_checks,
)


class AcmeOs:
    name = "acme-os"
    kind: Literal[Kind.SWITCH] = Kind.SWITCH
    gnmi_port = 9339

    def reader(self, access: GnmiAccess) -> ActualReader[ActualSwitch]:
        return GnmiReader(access)

    def checks(self) -> Sequence[Check[ExpectedSwitch, ActualSwitch]]:
        return switch_checks()


PLATFORM = AcmeOs()
```

```toml
# pyproject.toml of the package
[project.entry-points."racklint.platforms"]
acme-os = "racklint_acme:PLATFORM"
```

Install the package next to racklint, and a device with `racklint.platform: acme-os` is read with it.

A switch that does not keep everything in OpenConfig models needs a gNMI dialect: the requests that read it,
and how to parse the answers. `GnmiReader(access, dialect=...)` sends the dialect's requests over one
connection, and passes the answers, keyed by request, to the dialect's `parse`. A `GnmiRequest` has a `path`
and, if the switch serves it under a request target, a `target`. If it is `optional`, a switch with no data at
the path gives no updates instead of an error. `OPENCONFIG`, the default dialect, reads `/components` and
`/lldp/interfaces`; `openconfig_components` and `openconfig_neighbours` are its parsing, for a dialect to
reuse. Say Acme OS keeps its serial in a table of its own:

```python
from collections.abc import Mapping

from racklint import (
    ActualSwitch,
    GnmiAccess,
    GnmiReader,
    GnmiRequest,
    OpenConfigSwitch,
    Update,
    openconfig_neighbours,
)

LLDP = GnmiRequest("/lldp/interfaces")
SERIAL = GnmiRequest("system/serial", target="ACME_DB", optional=True)


class AcmeDialect:
    requests = (LLDP, SERIAL)

    def parse(self, replies: Mapping[GnmiRequest, list[Update]]) -> ActualSwitch:
        serial = replies[SERIAL][0]["val"]["serial"] if replies[SERIAL] else None
        return ActualSwitch(
            serials=(serial,) if serial else (),
            neighbours=tuple(openconfig_neighbours(replies[LLDP])),
        )


class AcmeOs(OpenConfigSwitch):
    name = "acme-os"
    gnmi_port = 9339

    def reader(self, access: GnmiAccess) -> GnmiReader:
        return GnmiReader(access, dialect=AcmeDialect())
```

`OpenConfigSwitch` is the platform of any OpenConfig switch, with `switch_checks()`; extending it keeps the
checks. The `sonic` platform, in [`src/racklint/platforms/sonic.py`](src/racklint/platforms/sonic.py), is
written this way, and imports only names that `racklint` exports.

`racklint.testing`, imported on its own, is the conformance kit: it reads recorded responses through a
platform, so a platform is tested without hardware. A server recording is a directory of Redfish resources in
the layout of DMTF's Redfish-Mockup-Server, loaded with `load_mockup`. A switch recording is one gNMI
GetResponse per request. The kit builds the platform's reader as a run does, serves the recording to it, and
returns what the reader reports for the test to compare:

```python
import json
from pathlib import Path

from racklint import ActualSwitch, FirmwareItem
from racklint.gnmi import COMPONENTS, LLDP
from racklint.testing import read_recorded_switch

from racklint_acme import PLATFORM

RECORDINGS = Path(__file__).parent / "recordings"


async def test_acme_os_reads_its_recording() -> None:
    responses = {
        COMPONENTS: json.loads((RECORDINGS / "components.json").read_text()),
        LLDP: json.loads((RECORDINGS / "lldp-interfaces.json").read_text()),
    }

    switch = await read_recorded_switch(PLATFORM, responses)

    assert switch == ActualSwitch(serials=("SN-0001",), firmware=(FirmwareItem(id="OS", version="1.0"),))
```

For a server, `read_recorded_server(platform, load_mockup(directory))` does the same. racklint's own platforms
are tested like this, from [`tests/recordings/`](tests/recordings). To add a platform to racklint itself, or
to publish recordings, read [CONTRIBUTING.md](CONTRIBUTING.md): a recording must hold no data from a real
network.

## Try it without hardware

[`examples/no-hardware/`](examples/no-hardware) is a Docker Compose stack with a real NetBox and six mock BMCs,
two of them built wrong on purpose and one whose credential set is not in the stack's Vault. racklint reads every
credential from that Vault. One command checks the rack, and the summary line is
`3 passed, 2 failed, 1 errored`. With `--profile fabric` it also starts three SR Linux leaves and a spine with
containerlab, and the same command checks nine devices: `4 passed, 4 failed, 1 errored`. See its README for the
steps and what is planted.

## Limitations

- GPUs are found in Redfish `Processors` only. A BMC that lists GPUs only under `PCIeDevices` shows zero GPUs;
  the finding then names the processors the BMC did list, and a library user can add a `GpuRule`.
- Credentials come from one source per run. Vault support is KV version 2 with a token or an AppRole login; for
  another secret manager, write a `CredentialStore`. The BMC and switch settings give one login to every BMC
  and one to every switch.
- Switches are checked for serial, OS version and switch-to-switch cabling. Optics, the hardware model, the
  cabling of BMC management ports and the cabling of servers' data ports are not checked.
- OpenConfig coverage differs by vendor and version. racklint reads the models above and is tested against
  Nokia SR Linux, and the `sonic` platform against recordings only. A gNMI dialect is the place to extend.
- Read-only by design. racklint reports differences and never fixes them in NetBox or on a device.

## Related work

| Project | What it does |
|---|---|
| [bb-Ricardo/netbox-sync](https://github.com/bb-Ricardo/netbox-sync) | Syncs VMware and Redfish sources into NetBox |
| [Solvik/netbox-agent](https://github.com/Solvik/netbox-agent) | Agent on each server that pushes hardware into NetBox |
| [bb-Ricardo/check_redfish](https://github.com/bb-Ricardo/check_redfish) | Monitoring plugin for component health over Redfish |
| OpenStack Ironic, Metal3, MAAS, Warewulf | Full provisioning platforms with BMC inspection |

racklint is smaller than any of these and does one thing: it compares the hardware with NetBox's plan and
reports the differences.

## License

[MIT](LICENSE)
