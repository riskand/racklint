# Spike: reading SONiC switches over gNMI

Date: 2026-10-22. Time-boxed research for racklint's platforms, with SONiC as the first new one. Public
sources only. Nothing here was tested against a live switch or a running SONiC image.

## Question

racklint reads a switch with two gNMI Get requests, `/components` and `/lldp/interfaces`, without a target or
origin, in `json_ietf` encoding, over TLS with a CA bundle and username/password sent as gRPC metadata. From
the replies it takes the serial number of the component whose type is `CHASSIS`, the `software-version` of the
first `OPERATING_SYSTEM`, `CONTROLLER_CARD` or `CHASSIS` component, and the LLDP neighbours of each interface
(`src/racklint/gnmi.py`). `LinkCheck` matches a neighbour when its system name (short form) equals the planned
peer device and the planned peer interface equals the neighbour's `port-id` or `port-description`
(`src/racklint/checks.py`). This was only tested against Nokia SR Linux.

What does community SONiC serve, how, and where does it keep the same three facts?

Confidence levels: **high** = read in source code on the branch named; **medium** = derived by combining
several source files, or documentation without code; **low** = a WebSearch snippet only.

Branches: "master" means the default branch fetched on 2026-10-22. Where a release branch (`202411`,
`202505`) differs, it is named. SONiC master moves fast; several findings below differ between master and
the 2024/2025 release branches.

## Sources

Every file marked "saved" is kept verbatim in the spike's payload folder, listed in its `SOURCES.txt`
(file, raw URL, licence). Licences were read from each repository's `LICENSE` on raw.githubusercontent.com.

| Ref | Source | Licence | Saved |
|---|---|---|---|
| S1 | sonic-gnmi docs: `README.md`, `doc/grpc_telemetry.md`, `doc/gNMI_usage_examples.md` in https://raw.githubusercontent.com/sonic-net/sonic-gnmi/master/ | Apache-2.0 (https://raw.githubusercontent.com/sonic-net/sonic-gnmi/master/LICENSE) | yes |
| S2 | sonic-gnmi server: `gnmi_server/server.go`, `gnmi_server/basicAuth.go`, `gnmi_server/pamAuth.go`, `telemetry/telemetry.go`, `Makefile` (master) | Apache-2.0 | yes |
| S3 | sonic-gnmi data clients: `sonic_data_client/db_client.go`, `sonic_data_client/non_db_client.go`, `sonic_data_client/transl_data_client.go`, `transl_utils/transl_utils.go`, `proto/sonic.proto` (master) | Apache-2.0 | yes |
| S4 | sonic-gnmi `telemetry/telemetry.go` on branch `202411`: https://raw.githubusercontent.com/sonic-net/sonic-gnmi/202411/telemetry/telemetry.go | Apache-2.0 | yes |
| S5 | sonic-mgmt-common translib (master): `translib/lldp_app.go`, `translib/pfm_app.go`, `translib/transformer/xfmr_platform.go`, `translib/path/path_validator.go`, `models/yang/annotations/openconfig-platform-annot.yang`, `models/yang/Makefile` in https://raw.githubusercontent.com/sonic-net/sonic-mgmt-common/master/ | Apache-2.0 (https://raw.githubusercontent.com/sonic-net/sonic-mgmt-common/master/LICENSE) | yes |
| S6 | sonic-mgmt-common on branch `202411`: `translib/pfm_app.go`, `translib/lldp_app.go` | Apache-2.0 | yes |
| S7 | sonic-buildimage build rules (master): `rules/config`, `rules/docker-gnmi.mk`, `rules/docker-telemetry.mk`, `rules/docker-sonic-mgmt-framework.mk`, `files/build_templates/init_cfg.json.j2` in https://raw.githubusercontent.com/sonic-net/sonic-buildimage/master/ | Apache-2.0 (https://raw.githubusercontent.com/sonic-net/sonic-buildimage/master/LICENSE) | yes |
| S8 | sonic-buildimage gNMI container (master): `dockers/docker-sonic-gnmi/gnmi-native.sh`, `telemetry_vars.j2`, `supervisord.conf` | Apache-2.0 | yes |
| S9 | sonic-buildimage `rules/config` and `dockers/docker-sonic-gnmi/gnmi-native.sh` on branches `202411` and `202505` | Apache-2.0 | yes |
| S10 | sonic-buildimage virtual switch: `platform/vs/docker-sonic-vs.mk`, `platform/vs/docker-sonic-vs/{Dockerfile.j2,supervisord.conf.j2,start.sh}`, `platform/vs/kvm-image.mk`, `platform/vs/sonic-platform-modules-vs/sonic_platform/chassis.py`, `device/virtual/x86_64-kvm_x86_64-r0/{pmon_daemon_control.json,default_sku}` (master) | Apache-2.0 | yes |
| S11 | sonic-buildimage `dockers/docker-platform-monitor/docker-pmon.supervisord.conf.j2` (master) | Apache-2.0 | yes |
| S12 | sonic-buildimage LLDP container: `dockers/docker-lldp/lldpmgrd`, `dockers/docker-lldp/lldpd.conf.j2` (master) | Apache-2.0 | yes |
| S13 | sonic-buildimage `device/dell/x86_64-dell_s6000_s1220-r0/Force10-S6000/port_config.ini` (master) | Apache-2.0 | yes |
| S14 | sonic-platform-daemons: `sonic-syseepromd/scripts/syseepromd`, `sonic-chassisd/scripts/chassis_db_init` in https://raw.githubusercontent.com/sonic-net/sonic-platform-daemons/master/ | Apache-2.0 (https://raw.githubusercontent.com/sonic-net/sonic-platform-daemons/master/LICENSE) | yes |
| S15 | sonic-platform-common: `sonic_platform_base/sonic_eeprom/eeprom_tlvinfo.py`, `sonic_platform_base/device_base.py` in https://raw.githubusercontent.com/sonic-net/sonic-platform-common/master/ | Apache-2.0 (https://raw.githubusercontent.com/sonic-net/sonic-platform-common/master/LICENSE) | yes |
| S16 | sonic-dbsyncd `src/lldp_syncd/daemon.py`: https://raw.githubusercontent.com/sonic-net/sonic-dbsyncd/master/src/lldp_syncd/daemon.py | Apache-2.0 (https://raw.githubusercontent.com/sonic-net/sonic-dbsyncd/master/LICENSE) | yes |
| S17 | lldpd `src/daemon/interfaces.c`: https://raw.githubusercontent.com/lldpd/lldpd/master/src/daemon/interfaces.c | ISC (https://raw.githubusercontent.com/lldpd/lldpd/master/LICENSE) | yes |
| S18 | containerlab: `docs/manual/kinds/sonic-vs.md`, `docs/manual/kinds/sonic-vm.md`, `nodes/sonic/sonic.go`, `nodes/sonic_vm/sonic_vm.go` in https://raw.githubusercontent.com/srl-labs/containerlab/main/ | BSD-3-Clause (https://raw.githubusercontent.com/srl-labs/containerlab/main/LICENSE) | yes |
| S19 | pygnmi 0.8.15 as installed in racklint's `.venv` (`pygnmi/client.py`, `pygnmi/create_gnmi_path.py`) | BSD-3-Clause (package metadata) | no (local install) |
| S20 | Search result: community `docker-sonic-vs` images on Docker Hub, e.g. https://hub.docker.com/r/netreplica/docker-sonic-vs/tags | third party | no |
| S21 | Search results: Dell Enterprise SONiC infohub, https://infohub.delltechnologies.com/l/enterprise-sonic-distribution-by-dell-technologies-lifecycle-management/devops-automation-and-telemetry and `.../supported-rpc-operations/`; Dell SONiC spec sheet, https://www.delltechnologies.com/asset/en-us/products/networking/technical-support/dell-networking-spec-sheet-sonic.pdf | vendor docs | no |
| S22 | Search result: Broadcom Enterprise SONiC brief, https://www.doradosoftware.com/hubfs/Enterprise-SONiC-by-Broadcom-Dorado%201.pdf | vendor docs | no |
| S23 | Search result: Cisco SONiC 8000 release notes 202505c.1.1.0, https://www.cisco.com/c/en/us/td/docs/iosxr/cisco8000/sonic/sonic-release-notes-2025c110.html | vendor docs | no |

The sonic-net/SONiC docs repository was not used: no `LICENSE` was found at `master/LICENSE`,
`master/LICENSE.md` or `gh-pages/LICENSE`, so its licence could not be verified.

## Findings

### 1. The gNMI server

- **Component.** sonic-gnmi, which still calls itself "SONiC-telemetry", builds the dial-in server binary
  `telemetry` [S1 `README.md`]. In images it runs in the `gnmi` container (`docker-sonic-gnmi`), started by
  `gnmi-native.sh` under supervisord [S7 `docker-gnmi.mk`, S8 `supervisord.conf`]. The older
  `docker-sonic-telemetry` is still built but not installed by default (`INCLUDE_SYSTEM_TELEMETRY = n`)
  [S7 `rules/config` line 197, `docker-telemetry.mk`]. High.
- **Port.** The binary has no default port (`-port` defaults to -1) [S2 `telemetry.go` line 190]. The
  container uses CONFIG_DB `GNMI|gnmi` `port` and falls back to **8080** when no `GNMI` entry exists [S8
  `gnmi-native.sh` line 91; same on 202411/202505, S9]. A Unix socket `/var/run/gnmi/gnmi.sock` also exists
  [S2 `telemetry.go` line 191]. High.
- **TLS.** TLS uses CONFIG_DB `GNMI|certs` (`server_crt`, `server_key`, `ca_crt`), or else `DEVICE_METADATA`
  `x509` [S8 `telemetry_vars.j2`, `gnmi-native.sh` lines 55-84]. When neither is configured, master starts
  with `--noTLS --bind_address 127.0.0.1` (plaintext, loopback only) [S8 line 86]. 202411 and 202505 start
  with `--noTLS` on every address [S9 line 60]. High.
- **Authentication modes.** The `--client_auth` flag takes `password`, `jwt`, `cert` or `none` [S2
  `telemetry.go` line 231, `server.go` `authenticate` lines 894-952]. Password authentication reads gRPC
  metadata keys `username` and `password` [S2 `basicAuth.go`], which is what racklint sends. It is checked
  by an SSH login to `127.0.0.1:22` with those credentials, not by calling PAM directly [S2 `pamAuth.go`
  lines 87-104]. So the switch's local users and whatever sshd's PAM stack uses (for example TACACS+)
  apply. High.
- **Default mode depends on branch and configuration.**
  - Master: if `GNMI|gnmi` `user_auth` is unset, the container forces `--client_auth cert` ("fail-closed")
    [S8 lines 174-181]. Without TLS or a CA, the binary drops `cert`. On a noTLS TCP listener with no
    other mode left, it refuses to start [S2 `telemetry.go` lines 344-355]. Combining the two files, a
    master image with no `GNMI` configuration would not serve gNMI on TCP at all. Medium (derived, not
    run).
  - 202411: `user_auth` is passed only when set [S9 line 126]. When `--client_auth` is absent, the default
    is password+JWT only if translib write is enabled, else no authentication [S4 lines 183-189].
    `ENABLE_TRANSLIB_WRITE` is commented out in 202411's `rules/config` [S9 line 174]. So a default 202411
    image appears to serve plaintext gNMI on port 8080 without authentication. Medium (derived).
- **Fit with racklint.** Username and password over TLS works when the operator sets `GNMI|certs` and
  `GNMI|gnmi user_auth = password` (comma-separated modes are parsed, e.g. `password,jwt` [S1
  `grpc_telemetry.md` "Application authentication"]). A wrong password returns `PermissionDenied` ("Invalid
  Password"); missing metadata returns `Unauthenticated` [S2 `basicAuth.go`]. Both are in racklint's
  `REFUSED`. High.
- **Every RPC is authenticated separately**, including Capabilities [S2 `server.go` lines 1186, 1424].
  pygnmi's `connect()` sends a Capabilities RPC [S19 `client.py` line 294]. So one racklint read is three
  SSH logins on the switch (Capabilities and two Gets). A wrong password fails on the first, Capabilities.
  High for the code. Lockout behaviour depends on the switch's PAM configuration: unknown.
- **Authorisation.** After authentication, a user's Unix groups are mapped to roles. A read with no
  matching role is allowed ("backwards compatible") [S2 `server.go` lines 840-865]. High.

### 2. What paths it serves

The routing is in `Server.Get` [S2 `server.go` lines 1124-1178], keyed on the request prefix's target, then
the origin:

| Prefix target / origin | Handler | Data |
|---|---|---|
| target `OTHERS` | `NewNonDbClient` | Non-DB virtual paths: `platform/cpu`, `proc/*`, `osversion/build` [S3 `non_db_client.go` lines 73-102] |
| target `SHOW` | `NewShowClient` | `show` command output; paths not determined in this spike |
| target `OPERATIONAL` | `handleOperationalGet` | Not examined |
| target `APPL_DB`, `ASIC_DB`, `COUNTERS_DB`, `CONFIG_DB`, `FLEX_COUNTER_DB`/`PFC_WD_DB`, `STATE_DB`, `CHASSIS_STATE_DB` | `NewDbClientForGet` | Redis tables as `TABLE/KEY/FIELD` [S3 `sonic.proto` `enum Target`, `db_client.go` lines 590-613] |
| any other target, origin `sonic-db` | `NewMixedDbClient` | Native DB through an origin |
| any other target (including none, or `OC-YANG`), any other origin | `NewTranslClient` | YANG models via translib |

- **OpenConfig goes through translib.** A request with no target and no `sonic-db` origin is handed to
  translib [S2 `server.go` lines 1164-1177]. The project's examples use target `OC-YANG` [S1
  `gNMI_usage_examples.md` lines 23-28], but any non-DB target takes the same branch. racklint's requests
  (no target, origin empty for `/components` [S19 `create_gnmi_path.py`]) would reach translib. High.
- **Module prefixes are optional.** sonic-gnmi converts gNMI paths to translib URIs with the
  `AppendModulePrefix` option, which adds missing module prefixes [S3 `transl_utils.go` lines 138-172, S5
  `path_validator.go` lines 152-176, 325-331]. `/components` should become
  `/openconfig-platform:components`. High for the code; not run.
- **translib is always linked.** The `gnmi` container depends on `SONIC_MGMT_COMMON` [S7 `docker-gnmi.mk`],
  and the translib client is compiled in regardless of the write flags. `ENABLE_TRANSLIB_WRITE` only adds
  the `gnmi_translib_write` build tag [S2 `Makefile` lines 46-54]. High.
- **`openconfig-lldp` `/lldp/interfaces`: implemented.** The `lldpApp` registers `/openconfig-lldp:lldp`
  [S5 `lldp_app.go` line 63]. It reads APPL_DB `LLDP_ENTRY_TABLE` (line 82). For
  `/openconfig-lldp:lldp/interfaces` it builds one interface per table key, with one neighbour whose id is
  the local interface name (lines 198-212, 239-245). It fills `state/system-name` from `lldp_rem_sys_name`,
  `state/port-id` from `lldp_rem_port_id`, `state/port-description` from `lldp_rem_port_desc`, plus chassis
  id, port id type, system description and management address (lines 246-300). Only the paths
  `/lldp/interfaces` and `/lldp/interfaces/interface` are handled in `processGet`. The file is identical on
  202411 [S6]. High.
- **`openconfig-platform` `/components`: implemented, but no `CHASSIS` component.**
  - Master: the old `PlatformApp` registration is commented out as deprecated [S5 `pfm_app.go` lines
    41-72]. Components come from the transformer `pfm_components_xfmr`, bound by an annotation on
    `/oc-platform:components/oc-platform:component` [S5 `openconfig-platform-annot.yang`,
    `xfmr_platform.go` lines 241-246]. It serves transceivers (STATE_DB `TRANSCEIVER_*`), integrated circuits
    (CONFIG_DB `NODE_CFG`, type `INTEGRATED_CIRCUIT`), software components from STATE_DB `SW_COMP_INFO`
    (types `OPERATING_SYSTEM`, `BOOT_LOADER`, `SOFTWARE_MODULE`, with `software-version` from that table),
    and one system EEPROM component (lines 17-38, 233-239, 1590-1660).
  - The system EEPROM component is named `System Eeprom` (aliases `eeprom`, `chassis`). Its `serial-no` is
    STATE_DB `EEPROM_INFO` "Serial Number", falling back to "Service Tag". Its `software-version` is the
    EEPROM "Software Version" field, not the OS (lines 281, 850-913). No code sets `state/type` for it: the
    only `Type` assignments are for ICs, transceivers and software components (grep of `xfmr_platform.go`:
    lines 739, 1080, 1139, 1651-1704). High.
  - 202411: `PlatformApp` is active and returns only `System Eeprom`, also without a type, with the same
    EEPROM fields [S6 `pfm_app.go` lines 41-57]. There is no OS component on 202411. High.
  - Who writes STATE_DB `SW_COMP_INFO` on master could not be found (one targeted WebSearch returned
    nothing). Unknown.

### 3. Is it in community images?

- **Hardware images.** `rules/config` on master sets `INCLUDE_SYSTEM_GNMI = y`, `INCLUDE_MGMT_FRAMEWORK = y`,
  `INCLUDE_LLDP ?= y`, `INCLUDE_SYSTEM_TELEMETRY = n`, `ENABLE_TRANSLIB_WRITE = y`,
  `ENABLE_NATIVE_WRITE = y` [S7 lines 187-257]. 202411 and 202505 set the same include flags, with
  `ENABLE_TRANSLIB_WRITE` commented out [S9]. The `gnmi` feature is created `enabled` when
  `include_system_gnmi == "y"` [S7 `init_cfg.json.j2` line 105]. High.
- **Separate mgmt-framework container.** `docker-sonic-mgmt-framework` (REST and CLI) is a separate
  container, installed when `INCLUDE_MGMT_FRAMEWORK = y` [S7 `docker-sonic-mgmt-framework.mk`]. gNMI does
  not need it, because sonic-gnmi links translib itself (finding 2). High.
- **`sonic-vs.img` (KVM).** It installs `SONIC_INSTALL_DOCKER_IMAGES` [S10 `kvm-image.mk` line 12], the same
  list the gnmi and mgmt-framework rules add to. So it should carry the `gnmi` container. Medium (build
  rules read, image not inspected).
- **`docker-sonic-vs` (single container).** It includes only the LLDP, FRR, teamd, NAT, sFlow, orchagent,
  SNMP and database dockers [S10 `docker-sonic-vs.mk` lines 18-25]. Its Dockerfile, supervisord template
  and `start.sh` start LLDP but mention no gnmi or telemetry [S10]. No gNMI server and no pmon. High.

### 4. Native data for racklint's three items

| Item | Where SONiC keeps it | gNMI read | Confidence |
|---|---|---|---|
| Chassis serial | STATE_DB `EEPROM_INFO\|0x23` field `Value` (ONIE TLV 0x23 "Serial Number"), written by pmon `syseepromd` through `eeprom_tlvinfo.EepromRedisVisitor` [S14 `syseepromd` line 91, S15 `eeprom_tlvinfo.py` lines 41, 735-786] | target `STATE_DB`, path `EEPROM_INFO/0x23` | high (code); medium (that a given platform's EEPROM class uses this writer) |
| Chassis serial (alternative) | STATE_DB `CHASSIS_INFO\|chassis 1` field `serial`, and STATE_DB `DEVICE_METADATA\|localhost` field `chassis_serial_number`, written once by pmon `chassis_db_init` from the platform API's `get_serial()`; `N/A` when not implemented [S14 `chassis_db_init` lines 30-44, 75-100] | target `STATE_DB`, path `CHASSIS_INFO/chassis 1` or `DEVICE_METADATA/localhost/chassis_serial_number` | high |
| OS version | `/etc/sonic/sonic_version.yml` `build_version`, served as `{"build_version": "SONiC.<version>", "error": ""}` [S3 `non_db_client.go` lines 26, 42-46, 101-102, 302-336] | target `OTHERS`, path `osversion/build` | high |
| OS version (OpenConfig, master only) | STATE_DB `SW_COMP_INFO` `software-version`, exposed as an `OPERATING_SYSTEM` component [S5 `xfmr_platform.go`] | `/components` via translib | high (code); writer unknown |
| LLDP neighbours | APPL_DB `LLDP_ENTRY_TABLE:<ifname>` with `lldp_rem_sys_name`, `lldp_rem_port_id`, `lldp_rem_port_id_subtype`, `lldp_rem_port_desc`, `lldp_rem_chassis_id` and others, written by `lldp_syncd` [S16 `daemon.py` lines 65, 239-250, 400-431] | target `APPL_DB`, path `LLDP_ENTRY_TABLE` (all keys) or `LLDP_ENTRY_TABLE/Ethernet0` | high |

- **How DB paths work.** gNMI elements are joined with the database's own key separator (`|` in STATE_DB,
  `:` in APPL_DB), so the client always writes `/` [S3 `db_client.go` lines 804-845]. A table-only path
  returns every key as a JSON object keyed by the part after the separator [S3 `db_client.go` lines
  1010-1075]. On Get, a path that names a key must exist, otherwise the reply is `NotFound`. A table-only
  path skips that check [S3 `db_client.go` lines 925-950, S2 `server.go` lines 1152-1162]. High.
- **pygnmi can send a target.** `gNMIclient.get(prefix, path, target=...)` puts `target` in the request
  prefix [S19 `client.py` lines 405-480, `create_gnmi_path.py` lines 11-34]. High.
- `LLDP_ENTRY_TABLE` also holds `eth0`, the management port: lldp_syncd accepts
  `^(Ethernet(\d+)|Ethernet-BP(\d+)|eth0)$` [S16 line 22]. High.

### 5. Interface names

- **Local names.** SONiC ports are named `Ethernet0`, `Ethernet4`, … with a vendor alias in
  `port_config.ini`, e.g. `Ethernet0 … fortyGigE0/0` on the Dell S6000 [S13]. The virtual switch defaults to
  HwSKU `Force10-S6000` [S10 `default_sku`]. The VS copy of that `port_config.ini` was not found at the
  expected raw path, so VS aliases are unverified. LLDP keys and the OpenConfig LLDP interface names are the
  SONiC port names (`Ethernet0`) [S16, S5 `lldp_app.go`]. High.
- **What SONiC advertises as port-id: the alias.** lldpd starts with `configure lldp portidsubtype ifname`
  ("Use ifname globally to avoid MAC-as-Port-ID") [S12 `lldpd.conf.j2` lines 30-31]. lldpmgrd then runs
  `lldpcli configure ports <name> lldp portidsubtype local <alias>` for each port. It uses the CONFIG_DB
  `PORT` `alias`, or the port name if there is no alias [S12 `lldpmgrd` lines 147-156]. High.
- **Port description.** It is the CONFIG_DB `PORT` `description` when set [S12 `lldpmgrd` lines 153-160].
  Otherwise lldpd uses the kernel interface alias if one is set, else the interface name [S17
  `interfaces.c` lines 631-647]. Whether SONiC sets a kernel ifalias on `EthernetN` was not determined.
  Medium.
- **System name.** It is `DEVICE_METADATA|localhost` `hostname` [S12 `lldpd.conf.j2` line 29]. High.
- **What this means for `LinkCheck`.** On a SONiC switch looking at a SONiC peer, `port-id` is the peer's
  alias (e.g. `fortyGigE0/0`), not `Ethernet0`. `port-description` is the peer's port description, or
  possibly its `EthernetN` name. If NetBox records SONiC port names (`Ethernet0`), a match through
  `port-id` fails. A match through `port-description` succeeds only when the peer port has no description
  and no kernel ifalias. If NetBox records aliases, `port-id` matches, but the local side (racklint's
  `interface`, keyed `Ethernet0`) no longer matches NetBox's local interface name. Either way, a name/alias
  map per SONiC device (CONFIG_DB `PORT` `alias`, readable as target `CONFIG_DB` path `PORT`) is needed
  for reliable matching. Medium (derived).

### 6. Lab

- **Kinds.** containerlab has `sonic-vs` (the `docker-sonic-vs` container) and `sonic-vm` (`sonic-vs.img`
  wrapped by srl-labs/vrnetlab) [S18 `nodes/sonic/sonic.go` line 33, `nodes/sonic_vm/sonic_vm.go` line 31,
  both docs pages]. High.
- **Images.** containerlab names no registry image. It points to sonic.software ("unofficial … may be
  down") and the SONiC Azure build pipeline artifacts `target/docker-sonic-vs.gz` and
  `target/sonic-vs.img.gz`, which must be downloaded and loaded by hand [S18 docs]. Community copies exist
  on Docker Hub (e.g. `netreplica/docker-sonic-vs`, several years old) [S20]. Low for the Docker Hub images.
- **sonic-vm.** Default login `admin:admin` [S18 `sonic-vm.md`, `sonic_vm.go` line 32]. `startup-config` is
  loaded as `/etc/sonic/config_db.json` [S18 `sonic-vm.md`]. So `GNMI|certs` and `GNMI|gnmi` could be set
  in the lab topology. High.
- **sonic-vs.** containerlab resets the entrypoint and later runs `supervisord`, then
  `supervisorctl start bgpd` [S18 `sonic.go` lines 61-62, 156-177]. LLDP runs inside it [S10 `start.sh`
  line 181]. There is no gNMI server [finding 3], so `sonic-vs` cannot test racklint's gNMI read. High.
- **Lab limits.**
  - The VS platform skips `syseepromd` [S10 `pmon_daemon_control.json`], so the VM has no `EEPROM_INFO`
    and translib's `System Eeprom` has no serial.
  - The VS `Chassis` class implements no `get_serial()`, and `DeviceBase.get_serial()` raises
    `NotImplementedError` [S10 `chassis.py`, S15 `device_base.py` lines 49-56]. So `chassis_db_init` would
    write `serial = N/A` [S14 lines 44, 51-68].
  - The `Chassis` constructor raises `FileNotFoundError` when `/etc/sonic/vs_chassis_metadata.json` is
    missing, which would make `chassis_db_init` exit before writing anything [S10 `chassis.py`, S14
    `main()`]. Medium.
  - In short, a lab can test the OS version and LLDP, not a real serial.
- **gNMI on sonic-vm by default.** On master, the gnmi server would not start without `GNMI` configuration;
  on 202411 it would serve plaintext without authentication (finding 1). The lab needs `GNMI|certs` and
  `user_auth = password` in its startup config. Medium.

### 7. Distributions (low confidence throughout)

- Dell Enterprise SONiC documents a management framework with CLI, REST and gNMI, describes OpenConfig, and
  says it supports only dial-in gNMI [S21]. Its spec sheet lists "gNMI … REST API - OC YANG" [S21]. Port and
  model list were not found in snippets.
- Broadcom's Enterprise SONiC 4.0.0 brief claims "gNMI/REST/Openconfig" and says the Campus package has no
  telemetry or gNMI [S22]. gNMI availability depends on the package.
- Cisco's SONiC for 8000 release notes (202505c.1.1.0) list gNMI OpenConfig XPath support including LLDP
  [S23].
- Whether any distribution exposes the chassis serial as an OpenConfig `CHASSIS` component, or the OS as an
  `OPERATING_SYSTEM` component, is unknown. The community translib code is Broadcom-copyrighted
  ([S5] `models/yang/Makefile` header), which suggests enterprise builds extend the same code. That is an
  inference, not evidence.

### 8. Other complications

- **Port.** SONiC defaults to 8080, racklint to 57400, and `RACKLINT_GNMI_PORT` is global [S8, README]. A
  mixed SR Linux and SONiC fabric needs a port per platform.
- **No plaintext in racklint.** pygnmi is always given TLS settings, and a default SONiC either has no TCP
  listener (master) or plaintext only (202411). Operators must provision server certificates.
- **SSH-backed logins.** Password checks are SSH logins: three per read, slower than a local check, and
  recorded in auth logs. If sshd is down or restricted to keys, password gNMI fails [S2 `pamAuth.go`].
- **`NotFound` for missing native keys.** racklint retries every `GnmiError`, so a missing `EEPROM_INFO`
  key (as on VS) would be retried before failing. Table-level paths avoid this [S3 `db_client.go`].
- **Value shape.** Native values come back as `json_ietf_val` strings of Redis hashes, with every field a
  string, e.g. the `COUNTERS_DB` examples [S1 `grpc_telemetry.md`]. The EEPROM table is keyed by TLV code
  (`0x23`) with `Name`/`Len`/`Value` fields, not by field name [S15].
- **Multi-ASIC.** A DB target may carry a namespace, `APPL_DB/asic0` [S3 `db_client.go` lines 590-605].
  LLDP on multi-ASIC devices runs per ASIC [S7 `init_cfg.json.j2` lines 127-129]. Not examined further.
- **`SHOW` target.** sonic-gnmi has a `SHOW` target backed by `show_client` [S2 `server.go` lines 30,
  1149-1151]. Its paths were not found (raw paths guessed under `show_client/` returned 404). It may offer
  `show version`-style data.
- **No MAC port-ids between SONiC switches.** lldpd's MAC fallback only applies when the subtype is not
  `local` [S17 lines 649-659], and SONiC sets `local` [S12]. Other vendors' peers may still send MACs.

## Summary

| Question | Answer | Confidence |
|---|---|---|
| gNMI server | sonic-gnmi `telemetry` binary in the `gnmi` container | high |
| Default port | 8080 (CONFIG_DB `GNMI\|gnmi port` overrides) | high |
| TLS | Only when `GNMI\|certs` or `DEVICE_METADATA x509` is set; else plaintext (master: loopback only) | high |
| Username/password as metadata | Supported (`--client_auth password`), verified by SSH to localhost, per RPC | high |
| Default auth | master: `cert` forced, so likely no TCP server without config; 202411: none | medium |
| OpenConfig route | No target (or `OC-YANG`), any origin except `sonic-db`, goes to translib, module prefix added | high |
| `/lldp/interfaces` | Implemented from APPL_DB `LLDP_ENTRY_TABLE`; system-name, port-id, port-description filled | high |
| `/components` serial | `System Eeprom` component has `serial-no`, but no `type`, so racklint's `CHASSIS` filter finds nothing | high |
| `/components` OS version | master: `OPERATING_SYSTEM` from `SW_COMP_INFO` (writer unknown); 202411: none | high / unknown |
| In community images | gnmi + translib: yes (`INCLUDE_SYSTEM_GNMI = y`); `sonic-vs.img`: yes; `docker-sonic-vs`: no | high / medium / high |
| Native serial | STATE_DB `EEPROM_INFO/0x23`, `CHASSIS_INFO/chassis 1` `serial`, `DEVICE_METADATA/localhost` | high |
| Native OS version | `OTHERS` `osversion/build` | high |
| Native LLDP | `APPL_DB` `LLDP_ENTRY_TABLE` | high |
| LLDP port-id | The peer's `PORT` alias (subtype local), not `EthernetN` | high |
| Lab | containerlab `sonic-vm` (gNMI possible with config) and `sonic-vs` (no gNMI); images not on a registry | high |
| VS hardware data | No EEPROM; chassis serial `N/A` or absent | medium |

## What this means for a SONiC platform

- **Works as-is with OpenConfig: LLDP only, partly.** `/lldp/interfaces` returns the shape racklint parses
  (interface name, `neighbors/neighbor/state/{system-name,port-id,port-description}`). Not run against a
  switch.
- **Needs a native-DB dialect or different parsing for the serial and the OS version.**
  - The serial exists in OpenConfig only on an untyped `System Eeprom` component, so racklint's `CHASSIS`
    filter misses it.
  - The OS version is absent from OpenConfig on 202411, and on master depends on a table whose writer is
    unknown.
  - The native sources are documented in code and readable over the same gNMI connection: STATE_DB
    `CHASSIS_INFO`/`EEPROM_INFO` for the serial, `OTHERS osversion/build` for the OS. They need `target`
    set in the request prefix, which pygnmi supports.
- **Needs interface-name mapping.** SONiC advertises its alias as port-id. A SONiC platform needs the
  CONFIG_DB `PORT` name-to-alias map, or a NetBox naming convention, before `LinkCheck` can match SONiC
  peers reliably.
- **Needs per-platform transport settings.** These are port 8080 instead of 57400, and the operator
  prerequisites `GNMI|certs` and `user_auth = password`. Documentation should state these prerequisites.

## Unknowns

- Who writes STATE_DB `SW_COMP_INFO`, and on which releases. This decides whether OpenConfig OS version
  works.
- Whether a real master image with no `GNMI` configuration fails to start gNMI on TCP (derived from code,
  not run).
- Whether 202411's translib OpenConfig Get works when authentication is off (`rc.Auth` unset). Not
  examined.
- Whether SONiC sets a kernel ifalias on `EthernetN`, which changes the advertised port description.
- The VS `port_config.ini` aliases (the raw path for `device/virtual/x86_64-kvm_x86_64-r0/Force10-S6000`
  returned 404).
- Whether every hardware platform's EEPROM class writes `EEPROM_INFO` through `EepromRedisVisitor`.
- The paths behind the `SHOW` and `OPERATIONAL` targets.
- PAM lockout behaviour for repeated SSH-backed gNMI logins.
- Enterprise distributions' OpenConfig coverage of `/components` (search snippets only).
- Whether `sonic-vs.img` from the public pipeline actually contains the `gnmi` container (build rules
  read, image not inspected).
