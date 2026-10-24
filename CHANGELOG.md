# Changelog

All notable changes to racklint are listed here, newest first.

- Every pull request adds its user-facing changes under `## Unreleased`.
- The release pull request renames that heading to `## <version> - <YYYY-MM-DD>` and sets the same version in
  `pyproject.toml`.
- Pushing the tag `v<version>` publishes the package to PyPI and creates the GitHub release from that section.

## 0.1.1 - 2026-10-24

racklint checks servers and switches against what NetBox plans for them, before they go into service, and
changes nothing.

### Servers, over Redfish

- Serial, GPUs, memory, NICs and firmware, compared with the device and its config context in NetBox. Firmware
  rules live in the config context; `--baseline` overrides them.
- GPUs are found by rules, tried in order: Processors of type GPU, then Processors of type Accelerator with
  the planned GPU model (upstream OpenBMC). A zero count lists the processors the BMC reported.
- NIC ports come from an adapter's `NetworkDeviceFunctions`, else its `Ports`.
- One Redfish session per BMC, with HTTP Basic as the fallback. A refused login is never retried.

### Switches, over gNMI

- With the `network` extra: serial, OS version, and cabling. Each planned switch-to-switch link, traced
  through patch panels, is compared with the switch's LLDP neighbours, by port name, description or NetBox
  label.
- Never read without TLS.

### Platforms

- `redfish` (the default for servers), `openbmc`, `openconfig` (the default for switches), `srlinux` and
  `sonic`, chosen with `racklint.platform` in a device's config context. Packages add more through the
  `racklint.platforms` entry point group.
- `racklint.testing`, a conformance kit, reads recorded responses through a platform, with no hardware.
- `sonic` is tested only against recordings shaped from sonic-gnmi's source. Anonymised captures from real
  switches are welcome.

### Credentials

- Per device: a factory secret by serial, then the set the config context names, then a default set. From
  Vault (KV version 2, token or AppRole), a credentials file, or the settings, with passwords read from files.

### Output

- A table or JSON (`schema_version` 1). A note says what a check could not check, and never fails a device.
- Exit codes: `0` when every device passed, `1` when one failed or could not be read, `2` for a usage or
  configuration error. `racklint --version` prints the version.

### Try it without hardware

- `examples/no-hardware`: NetBox, Vault and six mock BMCs, with planted faults. With `--profile fabric`, also
  three SR Linux leaves and a spine.
