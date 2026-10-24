# racklint/src/racklint/vault.py
"""Read credentials from HashiCorp Vault's KV version 2 engine, over its HTTP API.

Only what racklint needs: a token or AppRole login, and reading secrets that hold `username` and `password`.
Secrets live at <mount>/<prefix>/sets/<name> and <mount>/<prefix>/factory/<serial>.
"""

import asyncio
import re
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from racklint.credentials import CredentialError, Credentials
from racklint.http import describe_error
from racklint.models import PATH_SEGMENT

TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class VaultError(CredentialError):
    """Vault could not be read."""


@dataclass(frozen=True)
class TokenAuth:
    token: SecretStr


@dataclass(frozen=True)
class AppRoleAuth:
    role_id: str
    secret_id: SecretStr


@dataclass(frozen=True)
class VaultAccess:
    """How to reach Vault and where racklint's secrets are."""

    address: str
    auth: TokenAuth | AppRoleAuth
    mount: str = "secret"
    prefix: str = "racklint"
    verify: ssl.SSLContext | bool = True


@asynccontextmanager
async def vault_client(access: VaultAccess) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=access.address, verify=access.verify, timeout=TIMEOUT) as http:
        yield http


class VaultStore:
    """A CredentialStore on Vault. Logs in once, on first use."""

    where = "Vault"

    def __init__(self, http: httpx.AsyncClient, access: VaultAccess) -> None:
        self._http = http
        self._access = access
        self._token: asyncio.Task[str] | None = None

    async def factory(self, serial: str) -> Credentials | None:
        # A serial that cannot be a path segment cannot have a factory secret either.
        if not re.fullmatch(PATH_SEGMENT, serial):
            return None
        return await self._read("factory", serial, f"factory secret for serial {serial}", factory=True)

    async def named(self, name: str) -> Credentials | None:
        return await self._read("sets", name, f"set {name}")

    async def _read(self, folder: str, key: str, origin: str, factory: bool = False) -> Credentials | None:
        path = f"{self._access.prefix}/{folder}/{key}"
        response = await self._request(
            "GET", f"/v1/{self._access.mount}/data/{path}", token=await self._login()
        )
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if not response.is_success:
            raise VaultError(f"reading {path} from Vault returned HTTP {response.status_code}")
        try:
            secret = response.json()["data"]["data"]
            return Credentials(
                username=secret["username"], password=secret["password"], origin=origin, factory=factory
            )
        except (ValueError, KeyError, TypeError):
            # Not chained: a validation error quotes the value it rejected, which could be the password.
            raise VaultError(f"{path} in Vault has no username and password") from None

    async def _login(self) -> str:
        """The token to read with: the given one, or one AppRole login per run."""
        if self._token is None:
            self._token = asyncio.ensure_future(self._authenticate())
        return await self._token

    async def _authenticate(self) -> str:
        auth = self._access.auth
        if isinstance(auth, TokenAuth):
            return auth.token.get_secret_value()
        response = await self._request(
            "POST",
            "/v1/auth/approle/login",
            json={"role_id": auth.role_id, "secret_id": auth.secret_id.get_secret_value()},
        )
        if not response.is_success:
            raise VaultError(f"AppRole login to Vault returned HTTP {response.status_code}")
        try:
            return str(response.json()["auth"]["client_token"])
        except (ValueError, KeyError, TypeError) as error:
            raise VaultError("AppRole login to Vault returned no client token") from error

    async def _request(
        self, method: str, url: str, token: str | None = None, json: dict[str, str] | None = None
    ) -> httpx.Response:
        headers = {"X-Vault-Token": token} if token else {}
        try:
            return await self._http.request(method, url, headers=headers, json=json)
        except httpx.HTTPError as error:
            raise VaultError(
                f"Vault at {self._access.address} could not be reached: {describe_error(error)}"
            ) from error
