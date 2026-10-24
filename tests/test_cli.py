# racklint/tests/test_cli.py
import json
import os
import ssl
from base64 import b64decode, b64encode
from dataclasses import replace
from importlib.metadata import EntryPoint, version
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner, Result

from racklint import cli
from racklint.cli import app, bmc_access, load_settings, vault_access
from racklint.gnmi import COMPONENTS, LLDP
from racklint.platforms import ENTRY_POINTS, load_platforms
from tests.factories import (
    CLIENT_TOKEN,
    PASSWORD,
    FakeGnmiClient,
    approle_login,
    bmc_tree,
    gnmi_components,
    gnmi_lldp,
    kv_secret,
    netbox_device,
    netbox_far_interfaces,
    netbox_interfaces,
    netbox_switch,
    netbox_switch_ports,
    page,
    remove_resource,
    serve_bmc,
    serve_netbox,
)

NETBOX = "https://netbox.test"
VAULT = "https://vault.test:8200"
VAULT_TOKEN = "hvs.cli-token"
ENV = {
    "RACKLINT_NETBOX_URL": NETBOX,
    "RACKLINT_NETBOX_TOKEN": "nbt_abc.def",
    "RACKLINT_BMC_USERNAME": "admin",
    "RACKLINT_BMC_PASSWORD": "secret",
    "COLUMNS": "200",
}

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No .env file and no RACKLINT_* variables from the developer's shell."""
    monkeypatch.chdir(tmp_path)
    for name in [name for name in os.environ if name.startswith("RACKLINT_")]:
        monkeypatch.delenv(name)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


def check_rack(*options: str) -> Result:
    return runner.invoke(app, ["check", "--rack", "R01", *options])


def serve_rack_with_a_switch(router: respx.MockRouter, *servers: dict[str, Any]) -> None:
    """Rack R01 with leaf1 and `servers`. leaf1's far end spine1 is in another rack; n01 is cabled to it."""
    router.get(f"{NETBOX}/api/dcim/racks/").respond(json=page({"id": 1, "site": {"slug": "dc1"}}))
    router.get(f"{NETBOX}/api/dcim/devices/").mock(
        side_effect=[
            httpx.Response(200, json=page(*servers, netbox_switch())),
            httpx.Response(200, json=page(netbox_switch(21, "spine1"), netbox_device())),
        ]
    )
    router.get(f"{NETBOX}/api/dcim/interfaces/").mock(
        side_effect=[
            httpx.Response(200, json=page(*netbox_interfaces(), *netbox_switch_ports())),
            httpx.Response(200, json=netbox_far_interfaces()),
        ]
    )
    router.get(f"{NETBOX}/api/dcim/cables/").respond(json=page({"id": 101}, {"id": 102}, {"id": 103}))


def answer_as_switches(monkeypatch: pytest.MonkeyPatch, neighbours: list[tuple[str, str, str]]) -> None:
    """Switch settings in the environment, and every switch answering as leaf1 with these LLDP neighbours."""
    monkeypatch.setenv("RACKLINT_SWITCH_USERNAME", "admin")
    monkeypatch.setenv("RACKLINT_SWITCH_PASSWORD", "secret")
    monkeypatch.setenv("RACKLINT_SWITCH_VERIFY_TLS", "false")
    client = FakeGnmiClient({COMPONENTS: gnmi_components(), LLDP: gnmi_lldp(neighbours)})
    settings_access = cli.gnmi_access
    monkeypatch.setattr(
        cli, "gnmi_access", lambda settings: replace(settings_access(settings), client=client)
    )


def test_version_prints_the_installed_version_and_exits_0() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output == f"racklint {version('racklint')}\n"


def test_version_needs_no_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name)

    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.startswith("racklint ")


@pytest.mark.parametrize(
    ("arguments", "summary"),
    [
        pytest.param(["--help"], "Check servers and switches against NetBox", id="racklint"),
        pytest.param(["check", "--help"], "Check each device.", id="check"),
    ],
)
def test_help_still_works(arguments: list[str], summary: str) -> None:
    result = runner.invoke(app, arguments)

    assert result.exit_code == 0
    assert summary in result.output


def test_a_node_built_as_planned_exits_0(respx_mock: respx.MockRouter) -> None:
    serve_netbox(respx_mock, NETBOX)
    serve_bmc(respx_mock, bmc_tree())

    result = check_rack()

    assert result.exit_code == 0, result.output
    assert "1 passed, 0 failed, 0 errored" in result.output


def test_a_missing_gpu_exits_1_with_its_reason(respx_mock: respx.MockRouter) -> None:
    tree = bmc_tree()
    remove_resource(tree, "/redfish/v1/Systems/1/Processors/GPU_1")
    serve_netbox(respx_mock, NETBOX)
    serve_bmc(respx_mock, tree)

    result = check_rack()

    assert result.exit_code == 1, result.output
    assert "gpu: 1 found, 2 planned" in result.output


def test_json_output(respx_mock: respx.MockRouter) -> None:
    tree = bmc_tree()
    tree["/redfish/v1/UpdateService/FirmwareInventory/BMC"]["Version"] = "1.3.2"
    serve_netbox(respx_mock, NETBOX)
    serve_bmc(respx_mock, tree)

    result = check_rack("--format", "json")

    assert result.exit_code == 1
    [node] = json.loads(result.stdout)["results"]
    assert (node["kind"], node["address"]) == ("server", "192.0.2.11")
    assert node["findings"] == [{"check": "firmware", "message": "BMC 1.3.2 not in approved [1.4.0]"}]


@pytest.mark.parametrize(
    "selection",
    [pytest.param([], id="neither"), pytest.param(["--rack", "R01", "--device", "n01"], id="both")],
)
def test_rack_or_device_is_required_but_not_both(selection: list[str]) -> None:
    result = runner.invoke(app, ["check", *selection])

    assert result.exit_code == 2
    assert "pass either --rack or --device" in result.output


def test_missing_settings_name_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RACKLINT_NETBOX_URL")

    result = check_rack()

    assert result.exit_code == 2
    assert "RACKLINT_NETBOX_URL" in result.output


def test_an_invalid_baseline_exits_2(tmp_path: Path) -> None:
    (tmp_path / "baseline.yaml").write_text("device_types: [")

    result = runner.invoke(app, ["check", "--rack", "R01", "--baseline", str(tmp_path / "baseline.yaml")])

    assert result.exit_code == 2
    assert "cannot load firmware baseline" in result.output


def test_a_netbox_error_exits_2(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{NETBOX}/api/dcim/racks/").respond(json={"count": 0, "results": []})

    result = check_rack()

    assert result.exit_code == 2
    assert "rack 'R01' not found" in result.output


def test_options_take_precedence_over_settings() -> None:
    settings = load_settings(concurrency=3, ca_bundle=None, insecure=True)

    assert (settings.concurrency, settings.bmc_verify_tls) == (3, False)
    assert bmc_access(settings).verify is False
    assert bmc_access(load_settings(concurrency=None, ca_bundle=None, insecure=False)).verify is True


def test_a_baseline_file_overrides_the_rules_in_netbox(respx_mock: respx.MockRouter, tmp_path: Path) -> None:
    serve_netbox(respx_mock, NETBOX)
    serve_bmc(respx_mock, bmc_tree())
    baseline = tmp_path / "baseline.yaml"
    baseline.write_text('device_types:\n  gpu-node-h100:\n    - match: "BMC"\n      approved: ["9.9.9"]\n')

    result = check_rack("--baseline", str(baseline))

    assert result.exit_code == 1
    assert "firmware: BMC 1.4.0 not in approved [9.9.9]" in result.output


def test_servers_and_switches_share_one_report(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve_rack_with_a_switch(respx_mock, netbox_device())
    serve_bmc(respx_mock, bmc_tree())
    answer_as_switches(
        monkeypatch, [("ethernet-1/1", "spine1", "ethernet-1/2"), ("ethernet-1/2", "spine1", "ethernet-1/1")]
    )

    result = check_rack("--format", "json")

    assert result.exit_code == 1, result.output
    results = json.loads(result.stdout)["results"]
    assert [(r["node"], r["kind"], r["status"]) for r in results] == [
        ("n01", "server", "pass"),
        ("leaf1", "switch", "fail"),
    ]
    assert [f["check"] for f in results[1]["findings"]] == ["link", "link"]


def test_a_switch_only_run_needs_no_bmc_settings(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve_rack_with_a_switch(respx_mock)
    monkeypatch.delenv("RACKLINT_BMC_USERNAME")
    monkeypatch.delenv("RACKLINT_BMC_PASSWORD")
    answer_as_switches(
        monkeypatch, [("ethernet-1/1", "spine1", "ethernet-1/1"), ("ethernet-1/2", "spine1", "ethernet-1/2")]
    )

    result = check_rack()

    assert result.exit_code == 0, result.output


def test_a_plan_with_servers_needs_bmc_credentials(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve_netbox(respx_mock, NETBOX)
    monkeypatch.delenv("RACKLINT_BMC_PASSWORD")

    result = check_rack()

    assert result.exit_code == 2
    assert "RACKLINT_BMC_USERNAME and RACKLINT_BMC_PASSWORD" in result.output


def test_the_bmc_password_can_come_from_a_file(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    serve_netbox(respx_mock, NETBOX)
    bmc = serve_bmc(respx_mock, bmc_tree())
    (tmp_path / "bmc-password").write_text("rotated\n")
    monkeypatch.delenv("RACKLINT_BMC_PASSWORD")
    monkeypatch.setenv("RACKLINT_BMC_PASSWORD_FILE", str(tmp_path / "bmc-password"))

    result = check_rack()

    assert result.exit_code == 0, result.output
    assert bmc.calls.last.request.headers["authorization"] == "Basic " + b64encode(b"admin:rotated").decode()


def test_a_password_and_a_password_file_together_are_an_error(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    serve_netbox(respx_mock, NETBOX)
    (tmp_path / "bmc-password").write_text("rotated\n")
    monkeypatch.setenv("RACKLINT_BMC_PASSWORD_FILE", str(tmp_path / "bmc-password"))

    result = check_rack()

    assert result.exit_code == 2
    assert "are both set" in result.output


@pytest.mark.parametrize(("content", "message"), [(None, "cannot read password file"), ("\n", "is empty")])
def test_a_password_file_without_a_password_is_an_error(
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str | None,
    message: str,
) -> None:
    serve_netbox(respx_mock, NETBOX)
    if content is not None:
        (tmp_path / "bmc-password").write_text(content)
    monkeypatch.delenv("RACKLINT_BMC_PASSWORD")
    monkeypatch.setenv("RACKLINT_BMC_PASSWORD_FILE", str(tmp_path / "bmc-password"))

    result = check_rack()

    assert result.exit_code == 2
    assert message in result.output


def test_a_plan_with_switches_needs_switch_credentials(respx_mock: respx.MockRouter) -> None:
    serve_rack_with_a_switch(respx_mock, netbox_device())

    result = check_rack()

    assert result.exit_code == 2
    assert "RACKLINT_SWITCH_USERNAME and RACKLINT_SWITCH_PASSWORD" in result.output


# Platforms


def test_a_device_naming_a_platform_that_is_not_installed_is_its_error_and_the_others_are_read(
    respx_mock: respx.MockRouter,
) -> None:
    bmcs = serve_three_servers(respx_mock, {"platform": "arista-eos"})

    result = check_rack("--format", "json")

    assert result.exit_code == 1, result.output
    results = {r["node"]: r for r in json.loads(result.stdout)["results"]}
    assert [results[name]["status"] for name in ("n01", "n02", "n03")] == ["pass", "error", "pass"]
    assert results["n02"]["error"] == (
        "no racklint platform 'arista-eos': install one, or remove racklint.platform"
    )
    assert bmcs["n02"].call_count == 0


def test_a_device_is_judged_by_the_checks_of_the_platform_it_names(respx_mock: respx.MockRouter) -> None:
    """OpenBMC reports GPUs only as accelerators, so a processor of type GPU is not one to it."""
    serve_three_servers(respx_mock, {"platform": "openbmc"})

    result = check_rack("--format", "json")

    results = {r["node"]: r for r in json.loads(result.stdout)["results"]}
    assert [results[name]["status"] for name in ("n01", "n02", "n03")] == ["pass", "fail", "pass"]
    assert results["n02"]["findings"][0]["message"].startswith("0 found, 2 planned")


def test_two_platforms_with_one_name_exit_2_before_anything_is_read(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = [
        EntryPoint("redfish", "racklint.platforms.redfish:REDFISH", ENTRY_POINTS),
        EntryPoint("redfish", "racklint.platforms.redfish:OPENBMC", ENTRY_POINTS),
    ]
    monkeypatch.setattr(cli, "load_platforms", lambda: load_platforms(claims))

    result = check_rack()

    assert result.exit_code == 2
    assert "racklint platform 'redfish' is claimed twice" in result.output
    assert respx_mock.calls.call_count == 0


# Credential sources


def serve_three_servers(
    router: respx.MockRouter, n02_context: dict[str, str] | None = None
) -> dict[str, respx.Route]:
    """n01, n02 and n03 in rack R01, each with its own BMC, which has the device's own serial.

    n02's config context names the set bmc-dc1, unless `n02_context` says what it adds.
    """
    context = netbox_device()["config_context"]["racklint"]
    named = {"racklint": context | (n02_context or {"credentials": "bmc-dc1"})}
    router.get(f"{NETBOX}/api/dcim/racks/").respond(json=page({"id": 1, "site": {"slug": "dc1"}}))
    router.get(f"{NETBOX}/api/dcim/devices/").respond(
        json=page(
            netbox_device(1, "n01"), netbox_device(2, "n02", config_context=named), netbox_device(3, "n03")
        )
    )
    router.get(f"{NETBOX}/api/dcim/interfaces/").respond(
        json=page(*(interface for device_id in (1, 2, 3) for interface in netbox_interfaces(device_id)))
    )
    routes = {}
    for device_id in (1, 2, 3):
        tree = bmc_tree()
        tree["/redfish/v1/Systems/1"]["SerialNumber"] = f"SN-{device_id:02d}"
        routes[f"n{device_id:02d}"] = serve_bmc(router, tree, f"192.0.2.{10 + device_id}")
    return routes


def basic_username(route: respx.Route) -> str:
    """The username in the Basic authentication of the BMC's last request."""
    header = route.calls.last.request.headers["authorization"]
    return b64decode(header.removeprefix("Basic ")).decode().split(":", 1)[0]


def use_vault(monkeypatch: pytest.MonkeyPatch, **settings: str) -> None:
    """Vault with `settings` in place of the BMC settings."""
    monkeypatch.delenv("RACKLINT_BMC_USERNAME")
    monkeypatch.delenv("RACKLINT_BMC_PASSWORD")
    monkeypatch.setenv("RACKLINT_VAULT_ADDR", VAULT)
    for name, value in settings.items():
        monkeypatch.setenv(f"RACKLINT_{name.upper()}", value)


def serve_default_server_set(router: respx.MockRouter) -> respx.Route:
    """Vault holds the default server set only; n01's serial has no factory secret."""
    router.get(f"{VAULT}/v1/secret/data/racklint/factory/SN-01").respond(404)
    return router.get(f"{VAULT}/v1/secret/data/racklint/sets/default-server").respond(
        json=kv_secret(username="vault-user", password=PASSWORD)
    )


def test_a_credentials_file_gives_each_device_its_factory_secret_or_set_or_default(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bmcs = serve_three_servers(respx_mock)
    (tmp_path / "password").write_text(f"{PASSWORD}\n")
    credentials_file = tmp_path / "credentials.yaml"
    credentials_file.write_text(
        f"""
sets:
  default-server: {{username: default-user, password_file: {tmp_path / "password"}}}
  bmc-dc1: {{username: dc1-user, password_file: {tmp_path / "password"}}}
factory:
  SN-01: {{username: factory-user, password_file: {tmp_path / "password"}}}
"""
    )
    monkeypatch.delenv("RACKLINT_BMC_USERNAME")
    monkeypatch.delenv("RACKLINT_BMC_PASSWORD")
    monkeypatch.setenv("RACKLINT_CREDENTIALS_FILE", str(credentials_file))

    result = check_rack()

    assert result.exit_code == 0, result.output
    assert {name: basic_username(route) for name, route in bmcs.items()} == {
        "n01": "factory-user",
        "n02": "dc1-user",
        "n03": "default-user",
    }


def test_a_device_naming_a_set_that_is_not_there_is_an_error_and_the_others_are_read(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bmcs = serve_three_servers(respx_mock)
    (tmp_path / "password").write_text(PASSWORD)
    credentials_file = tmp_path / "credentials.yaml"
    credentials_file.write_text(
        f"sets:\n  default-server: {{username: default-user, password_file: {tmp_path / 'password'}}}\n"
    )
    monkeypatch.setenv("RACKLINT_CREDENTIALS_FILE", str(credentials_file))
    for name in ("RACKLINT_BMC_USERNAME", "RACKLINT_BMC_PASSWORD"):
        monkeypatch.delenv(name)

    result = check_rack("--format", "json")

    assert result.exit_code == 1, result.output
    results = {r["node"]: r for r in json.loads(result.stdout)["results"]}
    assert [results[name]["status"] for name in ("n01", "n02", "n03")] == ["pass", "error", "pass"]
    assert (
        results["n02"]["error"]
        == f"no credentials for n02: set bmc-dc1 not found in credentials file {credentials_file}"
    )
    assert bmcs["n02"].call_count == 0


def test_vault_gives_the_bmc_its_username(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve_netbox(respx_mock, NETBOX)
    bmc = serve_bmc(respx_mock, bmc_tree())
    vault = serve_default_server_set(respx_mock)
    use_vault(monkeypatch, vault_token=VAULT_TOKEN)

    result = check_rack()

    assert result.exit_code == 0, result.output
    assert basic_username(bmc) == "vault-user"
    assert vault.calls.last.request.headers["X-Vault-Token"] == VAULT_TOKEN


def test_a_vault_token_can_come_from_a_file(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    serve_netbox(respx_mock, NETBOX)
    serve_bmc(respx_mock, bmc_tree())
    vault = serve_default_server_set(respx_mock)
    (tmp_path / "token").write_text(f"{VAULT_TOKEN}\n")
    use_vault(monkeypatch, vault_token_file=str(tmp_path / "token"))

    result = check_rack()

    assert result.exit_code == 0, result.output
    assert vault.calls.last.request.headers["X-Vault-Token"] == VAULT_TOKEN


def test_an_approle_logs_in_to_vault_with_its_secret_id_file(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    serve_netbox(respx_mock, NETBOX)
    serve_bmc(respx_mock, bmc_tree())
    login = respx_mock.post(f"{VAULT}/v1/auth/approle/login").respond(json=approle_login())
    vault = serve_default_server_set(respx_mock)
    (tmp_path / "secret-id").write_text("secret-id-value\n")
    use_vault(monkeypatch, vault_role_id="role-1", vault_secret_id_file=str(tmp_path / "secret-id"))

    result = check_rack()

    assert result.exit_code == 0, result.output
    assert json.loads(login.calls.last.request.content) == {
        "role_id": "role-1",
        "secret_id": "secret-id-value",
    }
    assert vault.calls.last.request.headers["X-Vault-Token"] == CLIENT_TOKEN


def test_the_vault_mount_and_prefix_are_settings(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve_netbox(respx_mock, NETBOX)
    serve_bmc(respx_mock, bmc_tree())
    respx_mock.get(f"{VAULT}/v1/kv/data/dc1/factory/SN-01").respond(404)
    vault = respx_mock.get(f"{VAULT}/v1/kv/data/dc1/sets/default-server").respond(
        json=kv_secret(username="vault-user", password=PASSWORD)
    )
    use_vault(monkeypatch, vault_token=VAULT_TOKEN, vault_mount="kv", vault_prefix="dc1")

    result = check_rack()

    assert result.exit_code == 0, result.output
    assert vault.call_count == 1


def test_a_vault_that_refuses_the_token_is_each_devices_error_and_never_shows_the_token(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve_netbox(respx_mock, NETBOX)
    respx_mock.get(f"{VAULT}/v1/secret/data/racklint/factory/SN-01").respond(403)
    use_vault(monkeypatch, vault_token=VAULT_TOKEN)

    result = check_rack("--format", "json")

    assert result.exit_code == 1, result.output
    [node] = json.loads(result.stdout)["results"]
    assert node["status"] == "error"
    assert (
        node["error"] == "no credentials for n01: reading racklint/factory/SN-01 from Vault returned HTTP 403"
    )
    assert VAULT_TOKEN not in result.output


def test_the_vault_ca_bundle_becomes_the_tls_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = tmp_path / "ca.pem"
    bundle.write_text("not read: ssl is replaced below")
    context = object()
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile: context)
    use_vault(monkeypatch, vault_token=VAULT_TOKEN, vault_ca_bundle=str(bundle))

    access = vault_access(load_settings(None, None, False), VAULT)

    assert access.verify is context


@pytest.mark.usefixtures("respx_mock")
def test_vault_and_a_credentials_file_together_are_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_vault(monkeypatch, vault_token=VAULT_TOKEN, credentials_file=str(tmp_path / "credentials.yaml"))

    result = check_rack()

    assert result.exit_code == 2
    assert "RACKLINT_VAULT_ADDR and RACKLINT_CREDENTIALS_FILE are both set" in result.output


@pytest.mark.parametrize(
    "source",
    [
        pytest.param({"vault_addr": VAULT, "vault_token": VAULT_TOKEN}, id="vault"),
        pytest.param({"credentials_file": "credentials.yaml"}, id="credentials-file"),
    ],
)
@pytest.mark.usefixtures("respx_mock")
def test_another_source_with_the_bmc_settings_is_an_error_naming_them(
    monkeypatch: pytest.MonkeyPatch, source: dict[str, str]
) -> None:
    for name, value in source.items():
        monkeypatch.setenv(f"RACKLINT_{name.upper()}", value)

    result = check_rack()

    assert result.exit_code == 2
    assert "RACKLINT_BMC_USERNAME, RACKLINT_BMC_PASSWORD would not be used" in result.output


@pytest.mark.usefixtures("respx_mock")
def test_vault_with_switch_settings_is_an_error_naming_them(monkeypatch: pytest.MonkeyPatch) -> None:
    use_vault(monkeypatch, vault_token=VAULT_TOKEN, switch_password_file="/run/secrets/switch")

    result = check_rack()

    assert result.exit_code == 2
    assert "RACKLINT_SWITCH_PASSWORD_FILE would not be used" in result.output


@pytest.mark.parametrize(
    ("login", "message"),
    [
        pytest.param({}, "Vault needs a login", id="none"),
        pytest.param({"vault_role_id": "role-1"}, "Vault needs a login", id="approle-without-secret-id"),
        pytest.param(
            {"vault_token": VAULT_TOKEN, "vault_role_id": "role-1", "vault_secret_id_file": "/x"},
            "a token or an AppRole login, not both",
            id="both",
        ),
        pytest.param(
            {"vault_token": VAULT_TOKEN, "vault_token_file": "/x"},
            "RACKLINT_VAULT_TOKEN and RACKLINT_VAULT_TOKEN_FILE are both set",
            id="token-and-token-file",
        ),
    ],
)
def test_vault_needs_exactly_one_login(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, login: dict[str, str], message: str
) -> None:
    serve_netbox(respx_mock, NETBOX)
    use_vault(monkeypatch, **login)

    result = check_rack()

    assert result.exit_code == 2
    assert message in result.output


@pytest.mark.usefixtures("respx_mock")
@pytest.mark.parametrize(
    ("setting", "value"),
    [
        pytest.param("vault_token", VAULT_TOKEN, id="token"),
        pytest.param("vault_token_file", "/run/secrets/vault-token", id="token-file"),
        pytest.param("vault_role_id", "role-1", id="role-id"),
        pytest.param("vault_secret_id_file", "/run/secrets/secret-id", id="secret-id-file"),
        pytest.param("vault_ca_bundle", __file__, id="ca-bundle"),
    ],
)
def test_a_vault_setting_without_an_address_is_an_error_before_netbox_is_read(
    monkeypatch: pytest.MonkeyPatch, setting: str, value: str
) -> None:
    variable = f"RACKLINT_{setting.upper()}"
    monkeypatch.setenv(variable, value)

    result = check_rack()

    assert result.exit_code == 2
    assert f"{variable} would not be used without RACKLINT_VAULT_ADDR" in result.output
