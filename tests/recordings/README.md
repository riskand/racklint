# Recordings

What devices returned, kept for the conformance kit, `racklint.testing`. A test reads a recording through a
platform without hardware, and compares what the platform reports with what it should report. See
`tests/test_testing.py`.

| Platform | Recording | Layout |
|---|---|---|
| `redfish` | [`tests/fixtures/redfish_node.json`](../fixtures/redfish_node.json) | one JSON object: each resource by path |
| `openbmc` | [`openbmc/`](openbmc) | DMTF Redfish-Mockup-Server: `<path>/index.json` per resource |
| `srlinux` | [`srlinux/`](srlinux) | one GetResponse per request: `components.json`, `lldp-interfaces.json` |
| `sonic` | [`sonic/`](sonic) | one GetResponse per request, named for it: see below |

## Where they come from

All four were written for racklint, are under racklint's licence (MIT) and are not captures of a real device.

- **`redfish`** is the node every Redfish unit test builds on (`tests.factories.bmc_tree()`). It is not copied
  here, so the unit tests and the kit read one tree.
- **`openbmc`** is a trimmed tree in the shape of upstream OpenBMC's bmcweb: two `CPU` processors and eight
  `Accelerator` processors, as in [`docs/spikes/gpu-discovery.md`](../../docs/spikes/gpu-discovery.md),
  finding 2, and a network adapter with two `Ports` and no `NetworkDeviceFunctions`, none with a MAC address,
  as bmcweb serves them: an `Id`, a `Name`, a `Status` and a link to the port's metrics. It holds the
  resources racklint reads, and a session service; a port's `Metrics` link, which racklint does not follow,
  leads nowhere. bmcweb is Apache-2.0; none of its text is in the recording, only the shape of its Processor
  resources (`redfish-core/lib/processor.hpp`) and of its Port resources.
- **`srlinux`** is in the shape SR Linux replies to `/components` and `/lldp/interfaces` over gNMI, which
  `tests/test_gnmi.py` already uses: `openconfig-platform` components and `openconfig-lldp` neighbours.
- **`sonic`** has a GetResponse for each request of the platform, in the shape SONiC's gNMI server gives, as
  [the SONiC spike](../../docs/spikes/sonic.md) found it. The shapes come from the source of that server and
  of the daemons that fill its databases, listed [below](#sources-of-the-sonic-recordings). No text of that
  source is in the recordings, only its shapes; the values are invented. Every Get answers a Notification with
  the path asked as its update's path (S1, S2), and pygnmi decodes the `json_ietf_val` into the update's
  `val`.
  - `osversion-build.json`: `osversion/build` under the target `OTHERS` answers an object with `build_version`
    and `error` (S3, `non_db_client.go`). `osversion-build-error.json` is the answer when the switch cannot read
    its version file: `build_version` is `sonic.NA` and `error` says why.
  - `chassis-info.json`: `CHASSIS_INFO/chassis 1` under `STATE_DB`. A path that names a key answers the fields
    of that Redis hash, all strings (S3, `db_client.go`): `serial`, `model`, `revision` and
    `switch_host_serial` (S14, `chassis_db_init`). `chassis-info-na.json` is the answer of a platform that
    cannot tell them: each is `N/A` (S14).
  - `eeprom-info.json`: `EEPROM_INFO/0x23` under `STATE_DB`, the hash of the system EEPROM's TLV 0x23, whose
    fields are `Name`, `Len` and `Value` (S15, `EepromRedisVisitor`; S14, `syseepromd`). That TLV is the
    serial number.
  - `lldp-interfaces.json`: `/lldp/interfaces` is OpenConfig, served by translib (S5, `lldp_app.go`): an
    interface for each key of the table `LLDP_ENTRY_TABLE`, named as SONiC names its ports, with one neighbour
    whose `id` is that name and whose `state` holds the remote system's `id`, `system-name`,
    `system-description`, `chassis-id`, `port-id`, `port-description` and `management-address`. The table
    includes `eth0`, the management port, because `lldp_syncd` accepts it (S16). A SONiC neighbour's `port-id`
    is its port alias, such as `fortyGigE0/0` (S12, `lldpmgrd`; S13, the Dell S6000's `port_config.ini`). The
    enumerated fields `port-id-type` and `chassis-id-type` are left out: the source sets them from numbers
    but does not show how they are rendered, and racklint reads neither.
  - A request a switch has no data for has no file. The test leaves it out of the recording, and the kit
    answers it as the switch does.

## Rules

A recording holds no real serial number, MAC address, IP address, hostname or credential. These use the
documentation addresses `192.0.2.0/24` and `198.51.100.0/24`, invented serial numbers, and locally
administered MAC addresses (the second hexadecimal digit of the first octet is 2, 6, A or E).

## Sources of the `sonic` recordings

All Apache-2.0 (the `LICENSE` of each repository), read on the `master` branch on 22 October 2026. The labels
are those of [the spike's source table](../../docs/spikes/sonic.md#sources).

- S1, sonic-gnmi, `doc/gNMI_usage_examples.md`:
  https://raw.githubusercontent.com/sonic-net/sonic-gnmi/master/doc/gNMI_usage_examples.md
- S2, sonic-gnmi, `gnmi_server/server.go`:
  https://raw.githubusercontent.com/sonic-net/sonic-gnmi/master/gnmi_server/server.go
- S3, sonic-gnmi, `sonic_data_client/db_client.go`:
  https://raw.githubusercontent.com/sonic-net/sonic-gnmi/master/sonic_data_client/db_client.go
- S3, sonic-gnmi, `sonic_data_client/non_db_client.go`:
  https://raw.githubusercontent.com/sonic-net/sonic-gnmi/master/sonic_data_client/non_db_client.go
- S5, sonic-mgmt-common, `translib/lldp_app.go`:
  https://raw.githubusercontent.com/sonic-net/sonic-mgmt-common/master/translib/lldp_app.go
- S12, sonic-buildimage, `dockers/docker-lldp/lldpmgrd`:
  https://raw.githubusercontent.com/sonic-net/sonic-buildimage/master/dockers/docker-lldp/lldpmgrd
- S13, sonic-buildimage, the Dell S6000's `port_config.ini`:
  https://raw.githubusercontent.com/sonic-net/sonic-buildimage/master/device/dell/x86_64-dell_s6000_s1220-r0/Force10-S6000/port_config.ini
- S14, sonic-platform-daemons, `sonic-chassisd/scripts/chassis_db_init`:
  https://raw.githubusercontent.com/sonic-net/sonic-platform-daemons/master/sonic-chassisd/scripts/chassis_db_init
- S14, sonic-platform-daemons, `sonic-syseepromd/scripts/syseepromd`:
  https://raw.githubusercontent.com/sonic-net/sonic-platform-daemons/master/sonic-syseepromd/scripts/syseepromd
- S15, sonic-platform-common, `sonic_platform_base/sonic_eeprom/eeprom_tlvinfo.py`:
  https://raw.githubusercontent.com/sonic-net/sonic-platform-common/master/sonic_platform_base/sonic_eeprom/eeprom_tlvinfo.py
- S16, sonic-dbsyncd, `src/lldp_syncd/daemon.py`:
  https://raw.githubusercontent.com/sonic-net/sonic-dbsyncd/master/src/lldp_syncd/daemon.py
