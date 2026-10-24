# racklint/tests/test_redfish_client.py
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import respx

from racklint.errors import AuthenticationError, InventoryError
from racklint.redfish import RedfishAuthError, RedfishClient, RedfishError, Resource
from tests.factories import PASSWORD, credentials

BMC = "https://bmc.test"
ROOT = f"{BMC}/redfish/v1"
SESSIONS = "/redfish/v1/SessionService/Sessions"
SESSION = f"{SESSIONS}/7"
TOKEN = "session-token-value"


@asynccontextmanager
async def client() -> AsyncIterator[RedfishClient]:
    async with httpx.AsyncClient(base_url=BMC) as http:
        yield RedfishClient(http, max_requests=1, backoff=0)


async def get(path: str = "/redfish/v1") -> Resource:
    async with client() as redfish:
        return await redfish.get(path)


def created_session() -> httpx.Response:
    """What a BMC answers to a login: the token and the new session's location in the headers."""
    return httpx.Response(
        201, headers={"X-Auth-Token": TOKEN, "Location": SESSION}, json={"@odata.id": SESSION, "Id": "7"}
    )


async def test_returns_the_resource(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(ROOT).respond(json={"Id": "RootService"})

    assert await get() == {"Id": "RootService"}


async def test_retries_transient_statuses(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(ROOT).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, json={"Id": "RootService"}),
        ]
    )

    assert await get() == {"Id": "RootService"}
    assert route.call_count == 3


async def test_gives_up_after_three_attempts(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(ROOT).respond(503)

    with pytest.raises(RedfishError, match="returned HTTP 503"):
        await get()
    assert route.call_count == 3


async def test_retries_connection_errors_then_reports_them(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(ROOT).mock(side_effect=httpx.ConnectError("connection refused"))

    with pytest.raises(RedfishError, match=r"^GET /redfish/v1 failed: could not connect$"):
        await get()
    assert route.call_count == 3


async def test_a_timeout_reads_in_plain_words(respx_mock: respx.MockRouter) -> None:
    respx_mock.get(ROOT).mock(side_effect=httpx.ConnectTimeout(""))

    with pytest.raises(RedfishError) as raised:
        await get()
    assert str(raised.value) == "GET /redfish/v1 failed: connection timed out"


async def test_does_not_retry_client_errors(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(ROOT).respond(404)

    with pytest.raises(RedfishError, match="returned HTTP 404"):
        await get()
    assert route.call_count == 1


REFUSALS = [
    pytest.param(401, "authentication failed", id="401"),
    pytest.param(403, r"access denied \(HTTP 403\)", id="403"),
]


@pytest.mark.parametrize(("status", "message"), REFUSALS)
async def test_reports_rejected_credentials_without_retrying(
    respx_mock: respx.MockRouter, status: int, message: str
) -> None:
    route = respx_mock.get(ROOT).respond(status)

    with pytest.raises(RedfishAuthError, match=f"^{message}$"):
        await get()
    assert route.call_count == 1


@pytest.mark.parametrize("body", ["<html>", "[1, 2]"])
async def test_rejects_a_body_that_is_not_a_json_object(respx_mock: respx.MockRouter, body: str) -> None:
    respx_mock.get(ROOT).respond(200, text=body)

    with pytest.raises(RedfishError, match="not a JSON object"):
        await get()


def test_redfish_errors_are_inventory_errors() -> None:
    assert issubclass(RedfishError, InventoryError)


def test_a_refused_login_is_an_authentication_error() -> None:
    assert issubclass(RedfishAuthError, RedfishError)
    assert issubclass(RedfishAuthError, AuthenticationError)


async def test_login_posts_the_credentials_and_returns_the_session(respx_mock: respx.MockRouter) -> None:
    login = respx_mock.post(f"{BMC}{SESSIONS}").mock(return_value=created_session())

    async with client() as redfish:
        location = await redfish.login(SESSIONS, credentials(username="admin"))

    assert location == SESSION
    assert login.calls.last.request.read() == (
        b'{"UserName":"admin","Password":"' + PASSWORD.encode() + b'"}'
    )


async def test_after_login_every_request_carries_the_token_and_no_password(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(f"{BMC}{SESSIONS}").mock(return_value=created_session())
    resource = respx_mock.get(ROOT).respond(json={"Id": "RootService"})

    async with client() as redfish:
        await redfish.login(SESSIONS, credentials())
        await redfish.get("/redfish/v1")

    headers = resource.calls.last.request.headers
    assert headers["X-Auth-Token"] == TOKEN
    assert "Authorization" not in headers


async def test_login_uses_the_path_of_a_location_that_is_a_full_url(respx_mock: respx.MockRouter) -> None:
    other = "https://other.example"
    respx_mock.post(f"{BMC}{SESSIONS}").respond(
        201, headers={"X-Auth-Token": TOKEN, "Location": other + SESSION}
    )

    async with client() as redfish:
        assert await redfish.login(SESSIONS, credentials()) == SESSION


async def test_login_takes_the_session_from_the_body_when_there_is_no_location_header(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(f"{BMC}{SESSIONS}").respond(
        201, headers={"X-Auth-Token": TOKEN}, json={"@odata.id": SESSION}
    )

    async with client() as redfish:
        assert await redfish.login(SESSIONS, credentials()) == SESSION


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(201, headers={"Location": SESSION}), id="no-token"),
        pytest.param(httpx.Response(201, headers={"X-Auth-Token": TOKEN}, text="<html>"), id="no-location"),
    ],
)
async def test_a_login_answer_without_a_token_or_a_location_is_an_error(
    respx_mock: respx.MockRouter, answer: httpx.Response
) -> None:
    respx_mock.post(f"{BMC}{SESSIONS}").mock(return_value=answer)

    async with client() as redfish:
        with pytest.raises(RedfishError, match="returned no session token or location") as raised:
            await redfish.login(SESSIONS, credentials())

    assert TOKEN not in str(raised.value)


@pytest.mark.parametrize(("status", "message"), REFUSALS)
async def test_a_refused_login_is_final_after_one_post(
    respx_mock: respx.MockRouter, status: int, message: str
) -> None:
    login = respx_mock.post(f"{BMC}{SESSIONS}").respond(status)

    async with client() as redfish:
        with pytest.raises(RedfishAuthError, match=f"^{message}$") as raised:
            await redfish.login(SESSIONS, credentials())

    assert login.call_count == 1
    assert PASSWORD not in str(raised.value)


async def test_a_login_is_retried_after_transient_failures(respx_mock: respx.MockRouter) -> None:
    login = respx_mock.post(f"{BMC}{SESSIONS}").mock(
        side_effect=[httpx.ConnectError("connection refused"), httpx.Response(503), created_session()]
    )

    async with client() as redfish:
        assert await redfish.login(SESSIONS, credentials()) == SESSION
    assert login.call_count == 3


async def test_a_login_that_never_gets_through_is_an_error_after_three_tries(
    respx_mock: respx.MockRouter,
) -> None:
    login = respx_mock.post(f"{BMC}{SESSIONS}").respond(503)

    async with client() as redfish:
        with pytest.raises(RedfishError, match=f"POST {SESSIONS} returned HTTP 503"):
            await redfish.login(SESSIONS, credentials())
    assert login.call_count == 3


async def test_logout_deletes_the_session(respx_mock: respx.MockRouter) -> None:
    logout = respx_mock.delete(f"{BMC}{SESSION}").respond(204)

    async with client() as redfish:
        await redfish.logout(SESSION)

    assert logout.call_count == 1


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(500), id="http-error"),
        pytest.param(httpx.ConnectError("gone"), id="unreachable"),
    ],
)
async def test_a_failed_logout_is_ignored(
    respx_mock: respx.MockRouter, answer: httpx.Response | Exception
) -> None:
    respx_mock.delete(f"{BMC}{SESSION}").mock(side_effect=[answer])

    async with client() as redfish:
        await redfish.logout(SESSION)


async def test_basic_authentication_is_sent_on_every_request(respx_mock: respx.MockRouter) -> None:
    resource = respx_mock.get(ROOT).respond(json={"Id": "RootService"})

    async with client() as redfish:
        redfish.use_basic_auth(credentials(username="admin"))
        await redfish.get("/redfish/v1")

    expected = "Basic " + base64.b64encode(b"admin:" + PASSWORD.encode()).decode()
    assert resource.calls.last.request.headers["Authorization"] == expected


async def test_a_session_replaces_basic_authentication(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(f"{BMC}{SESSIONS}").mock(return_value=created_session())
    resource = respx_mock.get(ROOT).respond(json={"Id": "RootService"})

    async with client() as redfish:
        redfish.use_basic_auth(credentials())
        await redfish.login(SESSIONS, credentials())
        await redfish.get("/redfish/v1")

    headers = resource.calls.last.request.headers
    assert headers["X-Auth-Token"] == TOKEN
    assert "Authorization" not in headers
