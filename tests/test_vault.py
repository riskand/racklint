# racklint/tests/test_vault.py
import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from racklint.credentials import CredentialError, CredentialLookup
from racklint.vault import AppRoleAuth, TokenAuth, VaultAccess, VaultError, VaultStore, vault_client
from tests.factories import CLIENT_TOKEN, PASSWORD, approle_login, expected_node, kv_secret

VAULT = "https://vault.test:8200"
TOKEN = "hvs.root-token"
SECRET_ID = "secret-id-value"
SERVER_SET = f"{VAULT}/v1/secret/data/racklint/sets/default-server"
LOGIN = f"{VAULT}/v1/auth/approle/login"
TOKEN_ACCESS = VaultAccess(address=VAULT, auth=TokenAuth(SecretStr(TOKEN)))
APPROLE_ACCESS = VaultAccess(address=VAULT, auth=AppRoleAuth("role-1", SecretStr(SECRET_ID)))


def denied() -> httpx.Response:
    return httpx.Response(403, json={"errors": ["permission denied"]})


def server_secret() -> dict[str, Any]:
    return kv_secret(username="admin", password=PASSWORD)


@pytest.fixture
async def store() -> AsyncIterator[VaultStore]:
    async with vault_client(TOKEN_ACCESS) as http:
        yield VaultStore(http, TOKEN_ACCESS)


async def test_a_set_is_read_with_the_token(respx_mock: respx.MockRouter, store: VaultStore) -> None:
    route = respx_mock.get(SERVER_SET).respond(json=server_secret())

    found = await store.named("default-server")

    assert found is not None
    assert (found.username, found.origin, found.factory) == ("admin", "set default-server", False)
    assert found.password.get_secret_value() == PASSWORD
    assert route.calls.last.request.headers["X-Vault-Token"] == TOKEN


async def test_a_factory_secret_is_read_from_the_factory_folder(
    respx_mock: respx.MockRouter, store: VaultStore
) -> None:
    respx_mock.get(f"{VAULT}/v1/secret/data/racklint/factory/SN-0004").respond(
        json=kv_secret(username="root", password="factory-secret")
    )

    found = await store.factory("SN-0004")

    assert found is not None
    assert (found.username, found.origin, found.factory) == (
        "root",
        "factory secret for serial SN-0004",
        True,
    )
    assert found.password.get_secret_value() == "factory-secret"


async def test_the_mount_and_the_prefix_are_configurable() -> None:
    access = VaultAccess(address=VAULT, auth=TokenAuth(SecretStr(TOKEN)), mount="kv", prefix="dc1/racklint")
    with respx.mock() as router:
        route = router.get(f"{VAULT}/v1/kv/data/dc1/racklint/sets/bmc-dc1").respond(json=server_secret())
        async with vault_client(access) as http:
            await VaultStore(http, access).named("bmc-dc1")

    assert route.call_count == 1


async def test_a_missing_secret_is_none(respx_mock: respx.MockRouter, store: VaultStore) -> None:
    respx_mock.get(SERVER_SET).respond(404, json={"errors": []})

    assert await store.named("default-server") is None


@pytest.mark.parametrize("status", [403, 500, 307])
async def test_any_other_status_is_an_error_naming_the_path_and_the_status(
    respx_mock: respx.MockRouter, store: VaultStore, status: int
) -> None:
    respx_mock.get(SERVER_SET).respond(status)

    with pytest.raises(VaultError) as raised:
        await store.named("default-server")

    assert str(raised.value) == f"reading racklint/sets/default-server from Vault returned HTTP {status}"
    assert TOKEN not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(kv_secret(username="admin"), id="no-password"),
        pytest.param(kv_secret(password=PASSWORD), id="no-username"),
        pytest.param(kv_secret(username="admin", password=12345), id="password-not-a-string"),
        pytest.param({"data": None}, id="no-secret-data"),
        pytest.param([1, 2], id="not-an-object"),
    ],
)
async def test_a_secret_without_a_username_and_password_is_an_error(
    respx_mock: respx.MockRouter, store: VaultStore, body: Any
) -> None:
    respx_mock.get(SERVER_SET).respond(json=body)

    with pytest.raises(VaultError) as raised:
        await store.named("default-server")

    assert str(raised.value) == "racklint/sets/default-server in Vault has no username and password"
    assert raised.value.__cause__ is None
    assert "12345" not in str(raised.value)


async def test_a_body_that_is_not_json_is_an_error(respx_mock: respx.MockRouter, store: VaultStore) -> None:
    respx_mock.get(SERVER_SET).respond(200, text="<html>")

    with pytest.raises(VaultError, match="has no username and password"):
        await store.named("default-server")


async def test_approle_logs_in_once_for_many_reads_and_reads_with_the_client_token(
    respx_mock: respx.MockRouter,
) -> None:
    login = respx_mock.post(LOGIN).respond(json=approle_login())
    read = respx_mock.get(SERVER_SET).respond(json=server_secret())

    async with vault_client(APPROLE_ACCESS) as http:
        store = VaultStore(http, APPROLE_ACCESS)
        await asyncio.gather(*(store.named("default-server") for _ in range(5)))
        await store.named("default-server")

    assert login.call_count == 1
    assert json.loads(login.calls.last.request.content) == {"role_id": "role-1", "secret_id": SECRET_ID}
    assert "X-Vault-Token" not in login.calls.last.request.headers
    assert read.call_count == 6
    assert {call.request.headers["X-Vault-Token"] for call in read.calls} == {CLIENT_TOKEN}


async def test_a_token_login_makes_no_login_request(respx_mock: respx.MockRouter, store: VaultStore) -> None:
    login = respx_mock.post(LOGIN).respond(json=approle_login())
    respx_mock.get(SERVER_SET).respond(json=server_secret())

    await store.named("default-server")

    assert login.call_count == 0


async def test_a_failed_login_is_an_error_that_is_not_repeated(respx_mock: respx.MockRouter) -> None:
    login = respx_mock.post(LOGIN).mock(return_value=denied())

    async with vault_client(APPROLE_ACCESS) as http:
        store = VaultStore(http, APPROLE_ACCESS)
        for name in ("default-server", "bmc-dc1"):
            with pytest.raises(VaultError) as raised:
                await store.named(name)

    assert str(raised.value) == "AppRole login to Vault returned HTTP 403"
    assert SECRET_ID not in str(raised.value)
    assert login.call_count == 1


async def test_a_login_without_a_client_token_is_an_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(LOGIN).respond(json={"auth": None})

    async with vault_client(APPROLE_ACCESS) as http:
        with pytest.raises(VaultError, match="returned no client token"):
            await VaultStore(http, APPROLE_ACCESS).named("default-server")


@pytest.mark.parametrize("serial", ["SN/0004", "../SN", ".SN", "SN 4", ""])
async def test_a_serial_that_is_not_a_path_segment_has_no_factory_secret_and_no_request_is_made(
    respx_mock: respx.MockRouter, store: VaultStore, serial: str
) -> None:
    anything = respx_mock.route().respond(404)

    assert await store.factory(serial) is None
    assert anything.call_count == 0


async def test_an_unreachable_vault_is_an_error(respx_mock: respx.MockRouter, store: VaultStore) -> None:
    respx_mock.get(SERVER_SET).mock(side_effect=httpx.ConnectError("connection refused"))

    with pytest.raises(
        VaultError,
        match=re.escape("Vault at https://vault.test:8200 could not be reached: could not connect"),
    ):
        await store.named("default-server")


async def test_a_vault_failure_is_that_devices_error_with_its_name(
    respx_mock: respx.MockRouter, store: VaultStore
) -> None:
    respx_mock.get(f"{VAULT}/v1/secret/data/racklint/factory/SN-04").respond(404)
    respx_mock.get(SERVER_SET).mock(return_value=denied())

    with pytest.raises(CredentialError) as raised:
        await CredentialLookup(store).credentials_for(expected_node(name="n04", serial="SN-04"))

    assert str(raised.value) == (
        "no credentials for n04: reading racklint/sets/default-server from Vault returned HTTP 403"
    )


def test_a_vault_error_is_a_credential_error() -> None:
    assert issubclass(VaultError, CredentialError)
