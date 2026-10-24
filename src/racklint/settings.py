# racklint/src/racklint/settings.py
"""The CLI's settings, from RACKLINT_* environment variables or a .env file in the working directory.

The library does not use them: it takes explicit objects such as BmcAccess.
"""

from pathlib import Path
from typing import Literal

from pydantic import FilePath, HttpUrl, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RACKLINT_", env_file=".env", extra="ignore", frozen=True)

    netbox_url: HttpUrl
    netbox_token: SecretStr
    bmc_username: str | None = None
    bmc_password: SecretStr | None = None
    # A file holding the password, instead of bmc_password. Read at the start of every run, so a secret
    # manager can rotate the password by rewriting the file.
    bmc_password_file: Path | None = None
    bmc_scheme: Literal["https", "http"] = "https"
    bmc_verify_tls: bool = True
    bmc_ca_bundle: FilePath | None = None
    concurrency: PositiveInt = 16
    bmc_concurrency: PositiveInt = 4
    switch_username: str | None = None
    switch_password: SecretStr | None = None
    switch_password_file: Path | None = None
    gnmi_port: PositiveInt = 57400
    switch_verify_tls: bool = True
    # A plain path, checked only when the plan contains switches, so a server-only run never needs it.
    switch_ca_bundle: Path | None = None
    # One credential source per run: Vault, a credentials file, or the BMC and switch settings above.
    credentials_file: Path | None = None
    vault_addr: HttpUrl | None = None
    vault_token: SecretStr | None = None
    vault_token_file: Path | None = None
    vault_role_id: str | None = None
    vault_secret_id_file: Path | None = None
    vault_mount: str = "secret"
    vault_prefix: str = "racklint"
    vault_ca_bundle: FilePath | None = None
