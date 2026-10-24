# racklint/tests/test_http.py
import httpx
import pytest

from racklint.http import describe_error

REQUEST = httpx.Request("GET", "https://user:s3cret@bmc.test/redfish/v1")


class OddError(httpx.HTTPError):
    """An httpx error type that describe_error has no words for."""


@pytest.mark.parametrize(
    ("error", "words"),
    [
        pytest.param(httpx.ConnectTimeout(""), "connection timed out", id="connect-timeout"),
        pytest.param(httpx.ReadTimeout(""), "read timed out", id="read-timeout"),
        pytest.param(httpx.WriteTimeout(""), "write timed out", id="write-timeout"),
        pytest.param(httpx.PoolTimeout(""), "timed out waiting for a free connection", id="pool-timeout"),
        pytest.param(httpx.ConnectError("refused"), "could not connect", id="connect-error"),
        pytest.param(httpx.ProxyError("denied"), "proxy refused the connection", id="proxy-error"),
        pytest.param(httpx.ReadError("reset"), "connection closed while reading", id="read-error"),
        pytest.param(httpx.WriteError("broken pipe"), "connection closed while writing", id="write-error"),
        pytest.param(httpx.RemoteProtocolError("no response"), "connection closed", id="remote-protocol"),
        pytest.param(httpx.TooManyRedirects("20"), "too many redirects", id="redirects"),
        pytest.param(OddError("something odd"), "something odd", id="other-type-with-text"),
        pytest.param(OddError(""), "OddError", id="other-type-without-text"),
    ],
)
def test_describes_an_error_in_plain_words(error: httpx.HTTPError, words: str) -> None:
    assert describe_error(error) == words


def test_a_status_error_gives_the_status_and_not_the_url() -> None:
    error = httpx.HTTPStatusError("boom", request=REQUEST, response=httpx.Response(403, request=REQUEST))

    assert describe_error(error) == "HTTP 403"
