# racklint/src/racklint/cli.py
"""The racklint command line. It wires NetBox, the readers, the checks and a renderer together."""

import asyncio
import ssl
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import SecretStr, ValidationError
from rich.console import Console

from racklint.baseline import FirmwareBaseline
from racklint.credentials import (
    DEFAULT_SET,
    CredentialLookup,
    Credentials,
    CredentialStore,
    StaticStore,
    read_password,
)
from racklint.errors import RacklintError
from racklint.gnmi import GnmiAccess
from racklint.models import Kind, Report
from racklint.netbox import NetBoxSource, netbox_client
from racklint.platforms import load_platforms
from racklint.redfish import BmcAccess
from racklint.render import JsonRenderer, Renderer, TableRenderer
from racklint.runner import check_plan
from racklint.settings import Settings
from racklint.vault import AppRoleAuth, TokenAuth, VaultAccess, VaultStore, vault_client

EXIT_NOT_PASSED = 1
EXIT_USAGE = 2


class OutputFormat(StrEnum):
    TABLE = "table"
    JSON = "json"


RENDERERS: dict[OutputFormat, Renderer] = {
    OutputFormat.TABLE: TableRenderer(),
    OutputFormat.JSON: JsonRenderer(),
}

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _print_version(show: bool) -> None:
    """Print the installed version and exit. It is eager, so it runs before any command or setting is read."""
    if show:
        typer.echo(f"racklint {version('racklint')}")
        raise typer.Exit()


@app.callback()
def main(
    show_version: Annotated[
        bool,
        typer.Option("--version", callback=_print_version, is_eager=True, help="Show the version and exit."),
    ] = False,
) -> None:
    """Check servers and switches against NetBox before they go into service."""


@app.command()
def check(
    baseline: Annotated[
        Path | None, typer.Option(help="Firmware baseline YAML file. Overrides the rules planned in NetBox.")
    ] = None,
    rack: Annotated[str | None, typer.Option(help="Check every device in this NetBox rack.")] = None,
    site: Annotated[
        str | None, typer.Option(help="Site slug, if the rack name exists in several sites.")
    ] = None,
    device: Annotated[
        list[str] | None, typer.Option(help="Check this NetBox device. Repeat for more.")
    ] = None,
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Output format.")
    ] = OutputFormat.TABLE,
    concurrency: Annotated[int | None, typer.Option(min=1, help="Devices to read at once.")] = None,
    ca_bundle: Annotated[
        Path | None, typer.Option(exists=True, dir_okay=False, help="CA bundle for BMC TLS certificates.")
    ] = None,
    insecure: Annotated[bool, typer.Option("--insecure", help="Do not verify BMC TLS certificates.")] = False,
) -> None:
    """Check each device. Exit 1 if any device fails or cannot be read."""
    if bool(rack) == bool(device):
        _exit_usage("pass either --rack or --device")
    try:
        settings = load_settings(concurrency, ca_bundle, insecure)
    except ValidationError as error:
        _exit_usage(describe_settings_error(error))
    try:
        _one_source(settings)
        firmware_baseline = FirmwareBaseline.load(baseline) if baseline is not None else None
        report = asyncio.run(_check(settings, firmware_baseline, rack, site, device or []))
    except RacklintError as error:
        _exit_usage(str(error))
    RENDERERS[output_format].render(report, Console())
    raise typer.Exit(0 if report.passed else EXIT_NOT_PASSED)


def load_settings(concurrency: int | None, ca_bundle: Path | None, insecure: bool) -> Settings:
    """Settings from the environment, overridden by the command-line options that were given."""
    overrides: dict[str, object] = {}
    if concurrency is not None:
        overrides["concurrency"] = concurrency
    if ca_bundle is not None:
        overrides["bmc_ca_bundle"] = ca_bundle
    if insecure:
        overrides["bmc_verify_tls"] = False
    return Settings().model_copy(update=overrides)


def describe_settings_error(error: ValidationError) -> str:
    problems = (
        f"RACKLINT_{'_'.join(str(part) for part in problem['loc']).upper()}: {problem['msg']}"
        for problem in error.errors()
    )
    return "invalid settings: " + "; ".join(problems)


def bmc_access(settings: Settings) -> BmcAccess:
    verify: ssl.SSLContext | bool = settings.bmc_verify_tls
    if verify and settings.bmc_ca_bundle is not None:
        verify = ssl.create_default_context(cafile=settings.bmc_ca_bundle)
    return BmcAccess(scheme=settings.bmc_scheme, verify=verify, max_requests=settings.bmc_concurrency)


def gnmi_access(settings: Settings) -> GnmiAccess:
    if settings.switch_ca_bundle is not None and not settings.switch_ca_bundle.is_file():
        raise RacklintError(f"RACKLINT_SWITCH_CA_BUNDLE: {settings.switch_ca_bundle} is not a file")
    return GnmiAccess(
        port=settings.gnmi_port, verify_tls=settings.switch_verify_tls, ca_bundle=settings.switch_ca_bundle
    )


# The BMC and switch settings, which name the default sets and so do not go with another credential source.
_SETTINGS_CREDENTIALS = (
    "bmc_username",
    "bmc_password",
    "bmc_password_file",
    "switch_username",
    "switch_password",
    "switch_password_file",
)


# The Vault settings that have no default, and so show that Vault was meant to be used.
_VAULT_SETTINGS = (
    "vault_token",
    "vault_token_file",
    "vault_role_id",
    "vault_secret_id_file",
    "vault_ca_bundle",
)


@asynccontextmanager
async def credential_store(settings: Settings, kinds: set[Kind]) -> AsyncIterator[CredentialStore]:
    """The one store the settings ask for: Vault, a credentials file, or the BMC and switch settings."""
    if settings.vault_addr is not None:
        access = vault_access(settings, str(settings.vault_addr))
        async with vault_client(access) as http:
            yield VaultStore(http, access)
    elif settings.credentials_file is not None:
        yield StaticStore.from_file(settings.credentials_file)
    else:
        yield settings_store(settings, kinds)


def _one_source(settings: Settings) -> None:
    """Refuse settings for more than one credential source, so a run never uses a password nobody expects.

    This runs before anything is read, so a conflict fails fast.
    """
    stray = _given(settings, *_VAULT_SETTINGS)
    if stray and settings.vault_addr is None:
        raise RacklintError(f"{', '.join(stray)} would not be used without RACKLINT_VAULT_ADDR")
    sources = _given(settings, "vault_addr", "credentials_file")
    if len(sources) > 1:
        raise RacklintError(f"{' and '.join(sources)} are both set; a run uses one credential source")
    ignored = _given(settings, *_SETTINGS_CREDENTIALS)
    if sources and ignored:
        raise RacklintError(
            f"{sources[0]} is set, so {', '.join(ignored)} would not be used: unset them, "
            "or unset the credential source"
        )


def _given(settings: Settings, *names: str) -> list[str]:
    """The environment variables of those settings among `names` that are set."""
    return [f"RACKLINT_{name.upper()}" for name in names if getattr(settings, name) is not None]


def vault_access(settings: Settings, address: str) -> VaultAccess:
    verify: ssl.SSLContext | bool = True
    if settings.vault_ca_bundle is not None:
        verify = ssl.create_default_context(cafile=settings.vault_ca_bundle)
    return VaultAccess(
        address=address,
        auth=_vault_auth(settings),
        mount=settings.vault_mount,
        prefix=settings.vault_prefix,
        verify=verify,
    )


def _vault_auth(settings: Settings) -> TokenAuth | AppRoleAuth:
    """The one login the settings give: a token, from a variable or a file, or an AppRole."""
    tokens = _given(settings, "vault_token", "vault_token_file")
    approle = _given(settings, "vault_role_id", "vault_secret_id_file")
    if tokens and approle:
        raise RacklintError(
            f"Vault takes a token or an AppRole login, not both: {', '.join(tokens + approle)}"
        )
    if len(tokens) > 1:
        raise RacklintError(f"{' and '.join(tokens)} are both set")
    if settings.vault_token is not None:
        return TokenAuth(settings.vault_token)
    if settings.vault_token_file is not None:
        return TokenAuth(read_password(settings.vault_token_file))
    if settings.vault_role_id is not None and settings.vault_secret_id_file is not None:
        return AppRoleAuth(settings.vault_role_id, read_password(settings.vault_secret_id_file))
    raise RacklintError(
        "Vault needs a login: RACKLINT_VAULT_TOKEN or RACKLINT_VAULT_TOKEN_FILE, "
        "or RACKLINT_VAULT_ROLE_ID and RACKLINT_VAULT_SECRET_ID_FILE"
    )


def settings_store(settings: Settings, kinds: set[Kind]) -> StaticStore:
    """The BMC and switch settings as a store: they are the default sets, for the kinds in the plan."""
    sets: dict[str, Credentials] = {}
    if Kind.SERVER in kinds:
        sets[DEFAULT_SET[Kind.SERVER]] = _credentials(
            "BMC",
            "servers",
            settings.bmc_username,
            settings.bmc_password,
            settings.bmc_password_file,
            Kind.SERVER,
        )
    if Kind.SWITCH in kinds:
        sets[DEFAULT_SET[Kind.SWITCH]] = _credentials(
            "SWITCH",
            "switches",
            settings.switch_username,
            settings.switch_password,
            settings.switch_password_file,
            Kind.SWITCH,
        )
    return StaticStore(sets)


def _credentials(
    prefix: str,
    devices: str,
    username: str | None,
    password: SecretStr | None,
    password_file: Path | None,
    kind: Kind,
) -> Credentials:
    """The credentials to read `devices` with, as the default set of `kind`.

    The username comes from RACKLINT_<prefix>_USERNAME. The password comes from RACKLINT_<prefix>_PASSWORD, or
    from the file RACKLINT_<prefix>_PASSWORD_FILE names.
    """
    if password is not None and password_file is not None:
        raise RacklintError(f"RACKLINT_{prefix}_PASSWORD and RACKLINT_{prefix}_PASSWORD_FILE are both set")
    if password_file is not None:
        password = read_password(password_file)
    if username is None or password is None:
        raise RacklintError(
            f"RACKLINT_{prefix}_USERNAME and RACKLINT_{prefix}_PASSWORD (or _PASSWORD_FILE) are required "
            f"when the plan contains {devices}"
        )
    return Credentials(username=username, password=password, origin=f"set {DEFAULT_SET[kind]}")


async def _check(
    settings: Settings,
    baseline: FirmwareBaseline | None,
    rack: str | None,
    site: str | None,
    devices: Sequence[str],
) -> Report:
    """Read the plan once, check each device with its platform, and merge one report."""
    platforms = load_platforms()
    async with netbox_client(str(settings.netbox_url), settings.netbox_token.get_secret_value()) as client:
        source = NetBoxSource(client)
        nodes = await source.rack(rack, site) if rack else await source.devices(devices)
    if baseline is not None:
        nodes = [baseline.applied_to(node) for node in nodes]
    kinds = {node.kind for node in nodes}
    async with credential_store(settings, kinds) as store:
        # Access settings only for each kind in the plan, so a run needs only that kind's settings. They are
        # built before anything is read, so a missing setting fails fast.
        return await check_plan(
            nodes,
            platforms,
            CredentialLookup(store),
            bmc=bmc_access(settings) if Kind.SERVER in kinds else None,
            gnmi=gnmi_access(settings) if Kind.SWITCH in kinds else None,
            concurrency=settings.concurrency,
        )


def _exit_usage(message: str) -> NoReturn:
    Console(stderr=True).print(f"racklint: {message}", markup=False, highlight=False)
    raise typer.Exit(EXIT_USAGE)
