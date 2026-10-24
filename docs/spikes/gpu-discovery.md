# Spike: how Redfish services expose GPUs

Date: 2026-10-22. Time-boxed research for racklint's GPU rules. Public sources only. Nothing here was
tested against a live BMC.

## Question

racklint counts GPUs as members of each ComputerSystem's `Processors` with `ProcessorType == "GPU"`
(`src/racklint/redfish.py`, `read_inventory` and `_gpu`). A BMC that exposes GPUs another way gives
`gpu: 0 found, N planned`, which looks the same as missing GPUs.

Candidate design to confirm or refute:

1. When racklint finds zero GPUs, say where it looked.
2. Fall back to `Chassis/*/PCIeDevices/*/PCIeFunctions/*`, counting devices whose function class code
   marks a GPU or accelerator, only when Processors yields no GPU. Never sum the two sources.

Confidence levels: **high** = read in source code or a normative schema; **medium** = a primary document
that describes, but does not show, a payload; **low** = a WebSearch snippet (vendor docs could not be
fetched).

## Sources

Every file marked "saved" is kept verbatim in the spike's payload folder, listed in its `SOURCES.txt`.

| Ref | Source | Licence | Saved |
|---|---|---|---|
| S1 | DMTF Redfish JSON schemas (`PCIeFunction.v1_7_0`, `PCIeDevice.v1_23_0`, `Processor.v1_24_0`, `ComputerSystem.v1_29_0`, `Chassis.v1_29_0`): https://raw.githubusercontent.com/DMTF/Redfish-Publications/main/json-schema/ | BSD-3-Clause (https://raw.githubusercontent.com/DMTF/Redfish-Publications/main/LICENSE.md) | yes |
| S2 | DMTF `public-rackmount1` mockup, the only mockup in a reachable DMTF repo: https://raw.githubusercontent.com/DMTF/Redfish-Mockup-Server/main/public-rackmount1/ | BSD-3-Clause (https://raw.githubusercontent.com/DMTF/Redfish-Mockup-Server/main/LICENSE.md) | yes |
| S3 | Mockup Server README (points to DSP2043 on dmtf.org): https://raw.githubusercontent.com/DMTF/Redfish-Mockup-Server/main/README.md | BSD-3-Clause | no |
| S4 | bmcweb `processor.hpp`: https://raw.githubusercontent.com/openbmc/bmcweb/master/redfish-core/lib/processor.hpp | Apache-2.0 (https://raw.githubusercontent.com/openbmc/bmcweb/master/LICENSE) | yes |
| S5 | bmcweb `pcie.hpp`: https://raw.githubusercontent.com/openbmc/bmcweb/master/redfish-core/lib/pcie.hpp | Apache-2.0 | yes |
| S6 | bmcweb `pcie_util.hpp`: https://raw.githubusercontent.com/openbmc/bmcweb/master/redfish-core/include/utils/pcie_util.hpp, `systems.hpp` and `chassis.hpp` in https://raw.githubusercontent.com/openbmc/bmcweb/master/redfish-core/lib/ | Apache-2.0 | yes |
| S7 | phosphor-dbus-interfaces `Accelerator`, `PCIeDevice`, `PCIeFunction` YAML: https://raw.githubusercontent.com/openbmc/phosphor-dbus-interfaces/master/yaml/xyz/openbmc_project/Inventory/Item/ | Apache-2.0 (https://raw.githubusercontent.com/openbmc/phosphor-dbus-interfaces/master/LICENSE) | yes |
| S8 | peci-pcie (OpenBMC PCIe inventory producer): https://raw.githubusercontent.com/openbmc/peci-pcie/master/src/peci_pcie.cpp, `include/peci_pcie.hpp`, `include/pciDeviceClass.hpp` | Apache-2.0 (https://raw.githubusercontent.com/openbmc/peci-pcie/master/LICENSE) | yes |
| S9 | Search result: bmcweb commit "Support Processors and its collection for ProcessorType Accelerator": https://gerrit.openbmc.org/plugins/gitiles/openbmc/bmcweb/+/32bee76c18d4731657a7d75034ea0450e53c21f0%5E%21 | Apache-2.0 | no (blocked) |
| S10 | sushy `system.py`, `pcie_device.py`, `constants.py`, `processor.py`, `chassis/chassis.py`: https://raw.githubusercontent.com/openstack/sushy/master/sushy/resources/ | Apache-2.0 (https://raw.githubusercontent.com/openstack/sushy/master/LICENSE) | yes |
| S11 | sushy test samples `pcie_device.json`, `pcie_function.json`: https://raw.githubusercontent.com/openstack/sushy/master/sushy/tests/unit/json_samples/ | Apache-2.0 | yes |
| S12 | ironic Redfish inspect: https://raw.githubusercontent.com/openstack/ironic/master/ironic/drivers/modules/redfish/inspect.py | Apache-2.0 (https://raw.githubusercontent.com/openstack/ironic/master/LICENSE) | yes |
| S13 | ironic iLO inspect (stable/2025.1; gone from master): https://raw.githubusercontent.com/openstack/ironic/stable/2025.1/ironic/drivers/modules/ilo/inspect.py | Apache-2.0 | yes |
| S14 | Search result: proliantutils commit adding `pci_gpu_devices`: https://git.openstack.org/openstack/deb-python-proliantutils/commit/ca5a0c973e6a7d0bf8fb0a79d973aa773d224d07 | Apache-2.0 (not verified; opendev.org blocked) | no |
| S15 | pci.ids: https://raw.githubusercontent.com/pciutils/pciids/master/pci.ids (version 2026.10.22) | GPL-2.0-or-later or BSD-3-Clause (file header) | yes |
| S16 | Linux `pci_ids.h`: https://raw.githubusercontent.com/gregkh/linux/master/include/linux/pci_ids.h | GPL-2.0 | yes |
| S17 | Linux `amdgpu_drv.c`: https://raw.githubusercontent.com/gregkh/linux/master/drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c | MIT-style (file header) | yes |
| S18 | NVIDIA open kernel modules `nv-pci-table.c`, `nv-pci.c`, `linux_nvswitch.c`: https://raw.githubusercontent.com/NVIDIA/open-gpu-kernel-modules/main/kernel-open/nvidia/ | MIT (SPDX headers) | yes |
| S20 | Search result: Lenovo XCC "GET – GPU properties": https://pubs.lenovo.com/xcc-restapi/gpu_properties_get and https://pubs.lenovo.com/xcc2-restapi/gpu_properties_get | vendor docs | no |
| S21 | Search result: Lenovo XCC PCIe devices/functions: https://pubs.lenovo.com/xcc-restapi/server_pcie_devices_get, https://pubs.lenovo.com/xcc-restapi/function_server_pcie_devices_get, https://pubs.lenovo.com/xcc3-restapi/function_server_pcie_devices_get | vendor docs | no |
| S22 | Search result: ironic commit "Add GPU reporting to idrac-wsman inspect interface": https://git.openstack.org/openstack/ironic/commit/101fc29686a4bf327521f7a4025a6c301db89e84 | Apache-2.0 | no |
| S23 | Search result: AMD MI250 system acceptance: https://instinct.docs.amd.com/projects/system-acceptance/en/latest/gpus/mi250.html | vendor docs | no |
| S24 | Search result: AMD MI300X system acceptance: https://instinct.docs.amd.com/projects/system-acceptance/en/latest/gpus/mi300x.html | vendor docs | no |
| S25 | Search result: Intel Gaudi platform guide: https://docs.habana.ai/en/latest/Installation_Guide/Platform_Upgrade_and_Unboxing.html | vendor docs | no |
| S26 | Search result: Supermicro FAQ 43400 (NVSwitch 22a3 in lspci): https://www.supermicro.com/support/faqs/faq.cfm?faq=43400 | vendor docs | no |
| S27 | Search result: lspci of ASPEED Graphics: https://openbenchmarking.org/system/1708210-TR-PHP21395923/Intel%20Xeon%20E3-1231%20v3/lspci | third party | no |
| S28 | Search result: openUBMC PCIeFunctions spec: https://www.openubmc.cn/docs/zh/development/specifications/redfish/details/Chassis/PCIeFunctions | vendor docs | no |

## Findings

### 1. DMTF public mockups

- The DSP2043 mockup bundle is published only as zip files on dmtf.org, which is blocked here [S3].
  Redfish-Publications holds schemas and registries, not mockups [S1 README:
  https://raw.githubusercontent.com/DMTF/Redfish-Publications/main/README.md]. Confidence: high.
- `public-rackmount1`, the one mockup in a reachable DMTF repo, has no GPU and no PCIeDevices. Its
  Processors are `CPU1`, `CPU2` (`ProcessorType: "CPU"`) and `FPGA1` (`ProcessorType: "FPGA"`) [S2].
  Confidence: high.
- Which DSP2043 mockups contain GPUs is **unknown**.
- The schema itself gives two standard places for PCIe devices [S1]:
  `ComputerSystem.PCIeDevices`, an array of links (added v1_2_0, not deprecated), and
  `Chassis.PCIeDevices`, a link to a collection (added v1_10_0). Confidence: high.
- The schema links the two sides: `Processor.Links.PCIeDevice` and `Processor.Links.PCIeFunctions`
  (v1_4_0), and `PCIeDevice.Links.Processors` (v1_12_0) [S1]. Confidence: high.
- `PCIeFunction.ClassCode` must be "the PCI Class Code, Subclass, and Programming Interface ... in the
  order listed", pattern `^0[xX]([0-9A-Fa-f]{2}){3}$`. A 3D controller is therefore `0x030200`.
  `DeviceClass` is an enum that includes `DisplayController` and `ProcessingAccelerators`. `VendorId` and
  `DeviceId` are `0xNNNN`, most significant byte first [S1]. Confidence: high.
- `ProcessorType` values are `CPU`, `GPU`, `FPGA`, `DSP`, `Accelerator`, `Core`, `Thread`, `Partition`,
  `OEM` [S1]. Confidence: high.

### 2. OpenBMC bmcweb

- bmcweb maps every D-Bus `xyz.openbmc_project.Inventory.Item.Accelerator` object to a Processor with
  `ProcessorType: "Accelerator"`, never `"GPU"` (`getAcceleratorDataByService`, processor.hpp line 579)
  [S4]. The D-Bus interface does carry `Type` with values `ASIC`, `FPGA`, `GPU`, `Unknown` [S7], but
  bmcweb does not read it [S4]. The original commit is titled "Support Processors and its collection for
  ProcessorType Accelerator" [S9]. Confidence: high (upstream master only).
- So **an upstream bmcweb BMC with GPUs gives racklint `0 found`** today.
- bmcweb serves PCIe devices at `/redfish/v1/Systems/{system}/PCIeDevices`, and functions at
  `.../PCIeDevices/{id}/PCIeFunctions/{0..7}`. The ComputerSystem's `PCIeDevices` property is filled from
  the same collection (`getPCIeDeviceList`, pcie_util.hpp; systems.hpp line 3195). `chassis.hpp` has no
  `PCIeDevices` [S5, S6]. Confidence: high.
- bmcweb copies `ClassCode` and `DeviceClass` strings from D-Bus unchanged. The code says: "TODO:
  FunctionType and DeviceClass are Redfish enums. The D-Bus property strings should be mapped correctly"
  (pcie.hpp line 784) [S5]. It reads the `FunctionNClassCode`/`FunctionNDeviceClass` properties of the
  PCIeDevice interface, which phosphor-dbus-interfaces now marks deprecated in favour of a separate
  PCIeFunction interface [S7]. Confidence: high.
- peci-pcie master writes only the new `Item.PCIeFunction` interface, with numeric `BaseClassCode` and
  `SubClassCode` and a `DeviceClass` of the form
  `xyz.openbmc_project.Inventory.Item.PCIeFunction.DeviceClasses.DisplayController` [S8]. bmcweb master
  does not read that interface [S5]. Inference, not tested: on this pairing, bmcweb PCIeFunctions may
  carry no class at all. Confidence: medium.
- bmcweb fills `PCIeDevice.Links.Processors` from a D-Bus association (pcie.hpp around line 192). No
  `Links.PCIeDevice` is written on Processor [S4, S5]. Confidence: high.
- NVIDIA's own bmcweb fork (NVBMC, used on HGX) could not be read. Its GPU `ProcessorType` is
  **unknown** from primary source.

### 3. OpenStack sushy, ironic, proliantutils

- sushy knows `ProcessorType.GPU` and `DeviceClass.DISPLAY_CONTROLLER` / `PROCESSING_ACCELERATORS`
  [S10]. It has **no GPU classifier**. `CLASSCODE_FOR_GPU_DEVICES` is not in sushy master [S10].
- `System.pcie_devices` follows the System's `PCIeDevices` link, or an embedded list of links. sushy's
  Chassis class has no PCIeDevices accessor [S10]:

  ```python
  pcie_path = utils.get_sub_resource_path_by(self, "PCIeDevices")
  ...
  # Check if PCIeDevices is embedded in System JSON
  if (hasattr(self, 'json') and self.json
          and 'PCIeDevices' in self.json):
  ```
  Confidence: high.
- ironic's Redfish inspect walks `system.pcie_devices`, then each device's `pcie_functions`, and records
  every function with `info['class'] = function.device_class.value` plus vendor and device IDs. It does
  not pick out GPUs (`_get_pcie_devices`, `_extract_function_info`) [S12]. Confidence: high.
- `CLASSCODE_FOR_GPU_DEVICES` is in HPE's proliantutils, used by ironic's iLO driver for the
  `pci_gpu_devices` capability [S13, S14]. The search result quotes
  `CLASSCODE_FOR_GPU_DEVICES = [3]` and `SUBCLASSCODE_FOR_GPU_DEVICES = [0, 1, 2, 128]`, matched on
  separate integer `ClassCode` and `SubclassCode` fields [S14]. Those are not standard PCIeFunction
  properties [S1], so this reads an HPE resource. Which HPE resource, and whether it is current, is
  unknown (opendev.org blocked). Confidence: low.
- That rule counts subclass 0x00 (VGA), which is also the ASPEED BMC VGA class (Q5).
- The sushy sample `pcie_function.json` is Dell-shaped (`Oem.Dell`, `System.Embedded.1`) and shows
  `"ClassCode": "0x000002"` with `"DeviceClass": "NetworkController"` for an Ethernet NIC [S11]. The
  schema order would give `0x020000` [S1]; Lenovo's docs show `0x020000` for a NIC [S21]. Whether this
  is a real iDRAC capture is unknown. Confidence: high that the sample says this; low that iDRAC does.
- The same sample's functions live at `Chassis/System.Embedded.1/PCIeDevices/...`, and the device links
  back to the Chassis [S11]. Confidence: high (sample).

### 4. Vendors (lower confidence)

- **Lenovo XCC/XCC2**: GPUs are `Systems/1/Processors/GPU{N}` with `ProcessorType "GPU"`, and `Links`
  to the GPU's `PCIeDevice` and `PCIeFunctions`; the example is NVIDIA, VendorId `0x10de` [S20]. PCIe
  devices and functions are also listed under `Chassis/1/PCIeDevices` with standard-order class codes
  (`0x0C0400`, `0x010400`, `0x020000`) [S21]. **The same GPU appears in both places.** Confidence: low.
- **Dell iDRAC9**: an older ironic WS-Man fixture names a V100 `Video.Slot.7-1` [S22]. Where iDRAC's
  Redfish service lists GPUs (Processors, PCIeDevices or both) is unknown.
- **Supermicro, and NVIDIA HGX baseboards**: unknown.
- **HPE iLO 5/6**: no source found for where GPUs appear. Only the proliantutils rule above. Unknown.
- **Double-count risk**: Lenovo (both lists, linked) [S20, S21].

### 5. PCI class codes

- pci.ids names classes: `03 00` VGA compatible controller, `03 02` 3D controller, `03 80` Display
  controller, `12 00` Processing accelerators, `04 03` Audio device, `06 80` (bridge, other) [S15]. Linux
  defines `PCI_CLASS_DISPLAY_VGA 0x0300`, `PCI_CLASS_DISPLAY_3D 0x0302`, `PCI_CLASS_DISPLAY_OTHER 0x0380`,
  `PCI_CLASS_ACCELERATOR_PROCESSING 0x1200`, `PCI_CLASS_BRIDGE_OTHER 0x0680` [S16]. Confidence: high.
- pci.ids gives names per vendor and device, not a class per device. It proves, for example,
  `10de:2330 GH100 [H100 SXM5 80GB]`, `1002:74a1 Instinct MI300X`, `1da3:1020 Gaudi2`,
  `1a03:2000 ASPEED Graphics Family`, `10de:22a3 GH100 [H100 NVSwitch]` [S15]. It cannot prove which
  class any of them reports.
- **NVIDIA**: the driver binds NVIDIA devices of class 0x0300 or 0x0302 only (`nv_pci_table`) [S18].
  So an NVIDIA GPU reports one of those two. Which one a given datacenter SKU reports is not proven by a
  primary source found here. Confidence: high for "0x0300 or 0x0302".
- **AMD**: amdgpu's catch-all entries bind AMD devices of class 0x0300, 0x0380 and 0x1200 [S17]. AMD's
  MI250 page shows each MI250 as "Display controller" (0x0380) [S23]. MI300X class: unknown; AMD's page
  shows only the name [S24]. Confidence: high for the driver table, low for MI250.
- **Intel Gaudi2**: Intel's guide shows `lspci` output with "Processing accelerators" (0x1200) and vendor
  `1da3` [S25]. Confidence: low.
- **ASPEED BMC VGA**: `1a03:2000`, class 0x0300 VGA, behind an `1a03:1150` AST1150 bridge [S15, S27].
  This is the onboard video on most servers. Confidence: low for the class (one lspci dump), high for the
  IDs.
- openUBMC documents a `ClassCode` of `0x120000` with `DeviceClass` `ProcessingAccelerators` [S28].
  Confidence: low.

### 6. Multi-function devices, switches

- **NVSwitch**: NVIDIA's nvswitch driver binds NVIDIA devices of class 0x0680 (bridge, other) [S18].
  Supermicro shows 4 NVSwitch devices (`22a3`) in `lspci` on an 8×H100 HGX system [S26]. A class match
  on 0x03/0x12 skips them. A match on VendorId `0x10de` alone would count them. Confidence: high (driver),
  low (Supermicro count).
- **GPU audio function**: pci.ids lists HD Audio functions for NVIDIA GPU chips, e.g. `10de:22ba AD102
  High Definition Audio Controller` [S15]. Audio is class 0x0403 [S16], so a class match skips it.
  Whether datacenter SKUs (H100, L40S) expose an audio function is unknown.
- **One card, two GPUs**: each MI250 OAM shows as two PCI devices, one per GCD [S23]. A PCIe count gives
  16 for 8 MI250 cards. Confidence: low.
- **Virtual functions**: `PCIeFunction.FunctionType` can be `Virtual` [S1, S10]. A VF of a GPU keeps the
  GPU's class. Counting functions, not devices, would count VFs if a BMC lists them. Whether any BMC
  does is unknown.
- **bmcweb functions**: up to 8 functions per device, numbered 0 to 7 [S5].

### 7. What refutes or complicates the candidate design

- Upstream bmcweb puts PCIeDevices under **Systems**, not Chassis [S5, S6]. sushy and ironic also read
  only the System's `PCIeDevices` [S10, S12]. The schema allows both places [S1]. A Chassis-only fallback
  misses bmcweb entirely.
- Upstream bmcweb reports GPUs as `ProcessorType "Accelerator"` [S4]. The most common open BMC stack
  therefore fails the current rule before any fallback is needed.
- `ClassCode` is not reliable: bmcweb passes D-Bus strings through unmapped [S5]; the Dell-shaped sushy
  sample has a byte order the schema does not allow [S11]; bmcweb may emit no class with current
  peci-pcie [S5, S8].
- Class 0x0300 also matches the ASPEED BMC VGA [S15, S27], and NVIDIA GPUs can be 0x0300 [S18]. A rule
  on subclass alone cannot separate them.
- Some accelerators are one card but two PCI devices (MI250) [S23]. What NetBox's planned count means
  (cards or devices) decides whether a PCIe count can match it.

## Summary

| Source / vendor | Where GPUs appear | Class code seen | Confidence |
|---|---|---|---|
| DMTF schema | `Processor` (`ProcessorType GPU` or `Accelerator`); PCIe under `ComputerSystem.PCIeDevices` or `Chassis.PCIeDevices`; links both ways | `ClassCode` 0xCCSSPP; `DeviceClass` enum | high [S1] |
| DMTF public-rackmount1 | no GPU; FPGA in Processors; no PCIeDevices | none | high [S2] |
| Other DSP2043 mockups | unknown | unknown | — |
| bmcweb upstream | `Systems/{s}/Processors` with `ProcessorType Accelerator`; `Systems/{s}/PCIeDevices` | D-Bus string passed through, maybe absent | high [S4, S5] |
| sushy / ironic | read `Systems/{s}/PCIeDevices`; no GPU rule | `DeviceClass` recorded | high [S10, S12] |
| proliantutils (HPE) | HPE PCI device list, class 3, subclass 0/1/2/0x80 | integers | low [S14] |
| Lenovo XCC/XCC2 | `Processors/GPU{N}` (GPU) **and** `Chassis/1/PCIeDevices`, linked | 0xCCSSPP | low [S20, S21] |
| Dell iDRAC9 | unknown; a Dell-shaped sample puts PCIe under `Chassis/System.Embedded.1` | sample shows `0x000002` for a NIC | low [S11] |
| Supermicro, NVIDIA HGX | unknown | unknown | — |
| HPE iLO 5/6 | unknown | unknown | — |
| NVIDIA GPU (PCI) | — | 0x0300 or 0x0302 | high [S18] |
| AMD Instinct (PCI) | — | 0x0300, 0x0380 or 0x1200; MI250 0x0380 | high / low [S17, S23] |
| Intel Gaudi2 (PCI) | — | 0x1200 | low [S25] |
| ASPEED BMC VGA (PCI) | — | 0x0300, `1a03:2000` | low [S27] |
| NVIDIA NVSwitch (PCI) | — | 0x0680, `10de:22a3` | high [S18] |

## What this means for the candidate design

**Rule 1, say where racklint looked: confirmed.** Upstream bmcweb gives zero `GPU` processors on GPU
hosts [S4], which looks like `0 found` today. The message should name the
Systems searched, the `ProcessorType` values seen (so `Accelerator` is visible), and the PCIe
collections read.

**Rule 2, PCIe fallback: confirmed in principle, refuted in detail.**

- *Chassis only*: refuted. Read the `PCIeDevices` of each ComputerSystem and of each Chassis, and
  de-duplicate by `@odata.id` [S1, S5, S11].
- *Match on ClassCode*: weakened. Parse the base class from `ClassCode` only when it fits the schema
  pattern, and also accept `DeviceClass` `DisplayController` or `ProcessingAccelerators` [S1, S5, S11].
  Base classes 0x03 and 0x12 cover NVIDIA, AMD and Gaudi [S17, S18, S25].
- *Exclude onboard VGA*: needed, and must not rely on subclass, because NVIDIA GPUs may be 0x0300
  [S18]. Excluding ASPEED VendorId `0x1a03` is supported by the evidence [S15, S27]; other BMC VGA
  vendors were not checked.
- *Count devices, not functions*: supported. It avoids VFs and extra functions [S1, S5]. It still gives
  two per MI250 card [S23].
- *NVSwitch*: a class rule skips it; a vendor-only rule does not [S18, S26].
- *Never sum*: confirmed. Lenovo lists each GPU in both places [S20, S21]. Where
  `Processor.Links.PCIeDevice` exists, it shows which PCIe device is the same GPU [S1].

**Not in the candidate design**: `ProcessorType "Accelerator"`. It is what upstream bmcweb emits for
GPUs [S4], and what the schema offers for non-GPU accelerators too [S1]. Whether to count it, report
it, or ignore it is a decision the evidence does not make.

## Unknowns

- Which DSP2043 mockups contain GPUs, and how they model them (bundle only on dmtf.org).
- The `ProcessorType` NVIDIA HGX/NVBMC firmware reports, from a primary source.
- Where Dell iDRAC9 lists GPUs (Processors, PCIeDevices or both), and its real `ClassCode` byte order.
- How Supermicro and NVIDIA HGX baseboards expose GPUs, including what an unavailable baseboard returns.
- Where HPE iLO 5/6 exposes GPUs, and which resource proliantutils reads.
- Supermicro's layout on non-HGX (PCIe GPU) systems.
- The PCI class of H100/H200/B200, MI300X and L40S, from a primary source.
- Whether datacenter GPUs expose an audio function, and whether any BMC lists SR-IOV VFs.
- Whether bmcweb master with peci-pcie master emits any PCIeFunction class.
- Whether onboard VGA is always ASPEED; other BMC VGA vendors were not checked.
