# racklint/src/racklint/http.py
"""Plain words for a failed HTTP request, shared by every reader that talks to a service over httpx.

httpx timeouts often carry no text, so `repr(error)` shows a class name and empty quotes. The description is
built from the exception type and never from the request, so it holds no URL, credential or token.
"""

import httpx

# Most specific first: the first type that `error` is an instance of decides the words.
DESCRIPTIONS: tuple[tuple[type[httpx.HTTPError], str], ...] = (
    (httpx.ConnectTimeout, "connection timed out"),
    (httpx.ReadTimeout, "read timed out"),
    (httpx.WriteTimeout, "write timed out"),
    (httpx.PoolTimeout, "timed out waiting for a free connection"),
    (httpx.TimeoutException, "timed out"),
    (httpx.ProxyError, "proxy refused the connection"),
    (httpx.ConnectError, "could not connect"),
    (httpx.ReadError, "connection closed while reading"),
    (httpx.WriteError, "connection closed while writing"),
    (httpx.RemoteProtocolError, "connection closed"),
    (httpx.TooManyRedirects, "too many redirects"),
)


def describe_error(error: httpx.HTTPError) -> str:
    """What went wrong, in plain words; for a type with no words here, its message, else its class name."""
    for kind, words in DESCRIPTIONS:
        if isinstance(error, kind):
            return words
    if isinstance(error, httpx.HTTPStatusError):
        # Its message quotes the request URL, which may hold credentials.
        return f"HTTP {error.response.status_code}"
    return str(error) or type(error).__name__
