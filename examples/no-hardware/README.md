# racklint example: no hardware needed

## What this shows

racklint checking rack R01 in a real NetBox against six mock BMCs, with no servers. NetBox holds the plan: six
GPU nodes, their serials, BMC addresses and MACs, and the hardware and firmware their device type should
have. Each mock BMC serves a Redfish tree for one node. racklint reads every credential from a Vault dev
server, not from its settings. Two of the nodes are built wrong on purpose, and a third names a credential set
that Vault does not have; racklint reports all three.

## Run it

From this directory, with Docker Compose:

```bash
docker compose up -d
docker compose run --rm racklint check --rack R01
```

The first `docker compose up` may print a warning that it cannot pull `racklint:example` (`pull access denied … repository does not exist`); this is harmless, because there is no published image and Compose then builds it from this repository, and later runs reuse the built image.

The first start takes a few minutes while NetBox migrates its database. The output:

```console
┏━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Node ┃ Kind   ┃ Address    ┃ Status ┃ Reasons                                ┃
┡━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ n01  │ server │ 192.0.2.11 │ PASS   │                                        │
│ n02  │ server │ 192.0.2.12 │ PASS   │ note: nic: MACs not checked: the BMC   │
│      │        │            │        │ reports none                           │
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

`n02` passes, and the table notes what racklint could not check on it: see
[What is planted](#what-is-planted).

The approved firmware is in NetBox, in the device type's config context. `seed.py` writes it there from
`baseline.yaml`, so `--baseline baseline.yaml` gives the same result.

The exit code is `1` because two nodes failed and one could not be read; it is `0` when every node passes.

## With switches

The `fabric` profile adds three SR Linux leaves in rack R01 and a spine in rack R02, run by
[containerlab](https://containerlab.dev), so one command checks the servers and the switches:

```bash
docker compose --profile fabric up -d
docker compose --profile fabric run --rm racklint check --rack R01
```

The output, nine rows:

```console
┏━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Node  ┃ Kind   ┃ Address       ┃ Status ┃ Reasons                            ┃
┡━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ leaf1 │ switch │ 198.51.100.11 │ PASS   │                                    │
│ leaf2 │ switch │ 198.51.100.12 │ FAIL   │ link: ethernet-1/1 expected spine1 │
│       │        │               │        │ ethernet-1/3, sees spine1          │
│       │        │               │        │ ethernet-1/4                       │
│       │        │               │        │ link: ethernet-1/2 expected spine1 │
│       │        │               │        │ ethernet-1/4, sees spine1          │
│       │        │               │        │ ethernet-1/3                       │
│ leaf3 │ switch │ 198.51.100.13 │ FAIL   │ firmware: OS                       │
│       │        │               │        │ v26.3.3-392-g480f5fa2d04 not in    │
│       │        │               │        │ approved                           │
│       │        │               │        │ [v26.7.2-519-g94fbd638fa8]         │
│ n01   │ server │ 192.0.2.11    │ PASS   │                                    │
│ n02   │ server │ 192.0.2.12    │ PASS   │ note: nic: MACs not checked: the   │
│       │        │               │        │ BMC reports none                   │
│ n03   │ server │ 192.0.2.13    │ FAIL   │ gpu: 7 found, 8 planned            │
│ n04   │ server │ 192.0.2.14    │ ERROR  │ no credentials for n04: set        │
│       │        │               │        │ bmc-r02 not found in Vault         │
│ n05   │ server │ 192.0.2.15    │ FAIL   │ memory: 31 DIMMs (1984 GiB), 32    │
│       │        │               │        │ planned (2048 GiB)                 │
│       │        │               │        │ firmware: BMC 1.3.2 not in         │
│       │        │               │        │ approved [1.4.0]                   │
│ n06   │ server │ 192.0.2.16    │ PASS   │                                    │
└───────┴────────┴───────────────┴────────┴────────────────────────────────────┘
4 passed, 4 failed, 1 errored
```

Planted in `fabric/fabric.clab.yml`, against the plan in `fabric/switches.toml`:

- `leaf2`'s two uplinks land on the spine swapped: two `link:` findings.
- `leaf3` runs SR Linux 26.3.3, which the baseline does not approve: a `firmware: OS …` finding.

`leaf1` passes. Every SR Linux container reports the serial `Sim Serial No.`, so no serial fault is planted.
The switches' config context names the `srlinux` platform, so racklint reads them with it.

The `clab` service stays up once the lab is deployed, so each later check reads the same switches and does not
deploy them again. The `fabric-ready` service holds `racklint` back until every switch answers gNMI and sees
an LLDP neighbour on each port the plan cables. You need a Linux host with Docker that can run privileged
containers, about 6 GB of free memory for the four switches, and room for the SR Linux images, about 3.2 GB
each. containerlab's containers are not Compose's, so destroy the lab before taking the stack down:

```bash
docker compose run --rm --no-deps --entrypoint containerlab clab destroy -t fabric.clab.yml --cleanup
docker compose --profile fabric down -v
```

## What is planted

The planted faults are the `[actual]` tables: what the mock BMC reports instead of what NetBox plans.

`nodes/n03.toml`:

```toml
# Planted fault: one GPU is missing.
[actual]
gpu_count = 7
```

`nodes/n05.toml`:

```toml
# Planted faults: the BMC runs unapproved firmware, and one DIMM is missing.
[actual]
dimm_count = 31
firmware = { BMC = "1.3.2" }
```

`nodes/n04.toml`:

```toml
# Planted fault: the credential set its config context names is not in Vault.
credential_set = "bmc-r02"
```

`seed.py` puts the name in the device's own config context, `{"racklint": {"credentials": "bmc-r02"}}`, and
writes no set of that name to Vault. racklint cannot read n04, and says why:
`error: no credentials for n04: set bmc-r02 not found in Vault`. The other nodes are read as usual.

`n02` has no fault, but its mock BMC reports itself as upstream OpenBMC does. It reports its GPUs with
`ProcessorType` `Accelerator` (`gpu_processor_type = "Accelerator"` in `nodes/n02.toml`), and its NIC ports as
the `Ports` of each network adapter, without MAC addresses (`nic_ports_without_macs = true` in the same file).
Its config context names the `openbmc` platform (`platform = "openbmc"` in the same file), which counts
exactly such GPUs when their model is the planned one, and counts the NIC ports instead of comparing MACs. n02
passes, with the note `nic: MACs not checked: the BMC reports none`. `seed.py` puts the name in the device's
own config context, as it does for n04's credential set. The other servers name no platform, so they are read
by the default, `redfish`.

`n06` has no fault. Its MACs are not recorded in NetBox (`macs_recorded = false` in `nodes/n06.toml`), so
racklint has nothing to compare them with and checks its ports by count only. It passes.

## Credentials

Vault holds every credential, in the KV version 2 engine that its dev mode mounts at `secret/`. `seed.py` and
`seed_fabric.py` write them:

| Secret | Used by |
|---|---|
| `secret/racklint/sets/default-server` | every server whose config context names no set |
| `secret/racklint/factory/SN-R01-02` | n02: a factory secret for its serial wins over any set |
| `secret/racklint/sets/default-switch` | every switch (the `fabric` profile) |

The mock BMCs accept any login, so which secret a node was read with does not show in the output; the unit tests
cover every refused login. The example's BMCs also have no session service, so racklint reads them on its
Basic authentication fallback. Sessions, the default for a BMC that has them, are covered by unit tests only.

The Vault is a dev server: in memory, unsealed, with the root token `racklint-example`. racklint is given that
token in `RACKLINT_VAULT_TOKEN`; a real setup would give it a token that can only read `secret/racklint`, or an
AppRole. Look at a secret with:

```bash
docker compose exec -e VAULT_TOKEN=racklint-example vault vault kv get secret/racklint/sets/default-server
```

## Look around

- NetBox is at <http://127.0.0.1:8000>. Log in as `admin` with the password `racklint-example`.
- A mock BMC answers from inside the stack:

  ```bash
  docker compose run --rm --entrypoint python racklint -c "import httpx; print(httpx.get('http://192.0.2.11/redfish/v1').json()['Id'])"
  ```

  It prints `RootService`.
- The same report as JSON, for scripts:

  ```bash
  docker compose run --rm racklint check --rack R01 --format json
  ```

## How it works

| Service | Role |
|---|---|
| `postgres`, `valkey`, `netbox` | NetBox from the `netboxcommunity/netbox` image, version pinned |
| `vault` | a Vault dev server, `hashicorp/vault`, version pinned, holding the credentials |
| `seed` | one-shot; runs `seed.py` once NetBox and Vault are healthy, provisions an API token into the `token` volume and writes the servers' credentials to Vault |
| `render` | one-shot; runs `mockups.py`, writing one mockup tree per node into the `mockups` volume |
| `bmc-n01` … `bmc-n06` | `dmtf/redfish-mockup-server`, one per node, static IP on the `oob` network |
| `seed-fabric`, `clab`, `fabric-ready` | the `fabric` profile; `seed-fabric` plans the switches, their cables and their config context and writes their credentials to Vault, `clab` deploys `fabric/fabric.clab.yml`, then `fabric-ready` (`wait_fabric.py`) waits until every switch answers gNMI and sees an LLDP neighbour on each planned port |
| `racklint` | the example's image, on the default, `oob` and `fabric` networks, used with `docker compose run` |

`seed`, `seed-fabric`, `fabric-ready`, `render` and `racklint` all run one image, built from this repository's
source. The nodes are described once, in `nodes/`: `defaults.toml` holds the hardware of the device type, and
`nNN.toml` a node's serial, BMC IP, MACs and optional `credential_set`, `platform` and `[actual]` table.
`seed.py` writes that plan into NetBox, and `mockups.py` builds each node's Redfish tree from the same files.
Running `seed.py` again changes nothing racklint reads: a secret written again is a new version of the same
secret.

## Troubleshooting

If `docker compose ps` shows `netbox` restarting, and `docker compose logs netbox` shows `Address family not
supported by protocol`, the Docker host has IPv6 disabled and the NetBox image listens on `::`. Enable IPv6 in
Docker, or run the example on a host that has it.

## Clean up

```bash
docker compose down -v
```
