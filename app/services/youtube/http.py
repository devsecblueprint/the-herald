"""
A very small HTTP seam.

Everything that touches the network goes through ``HttpClient`` so the test
suite can run with no network at all. The one distinction that matters is
between a request that never reached the server (safe to retry) and one
whose response was lost (ambiguous), because Discord delivery hangs on it.
"""

import json as jsonlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol

import requests

DEFAULT_TIMEOUT = 10


class HttpError(Exception):
    """A request failed before the server could act on it. Retry is safe."""


class HttpResponseIncomplete(HttpError):
    """The request was sent but the response was lost. The outcome is unknown."""


@dataclass
class HttpResponse:
    """A minimal, inspectable HTTP response."""

    status_code: int
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    url: str = ""

    def json(self) -> Any:
        """
        Parse the body as JSON.

        Raises:
            ValueError: If the body is not valid JSON.
        """
        return jsonlib.loads(self.text)

    @property
    def is_redirect(self) -> bool:
        """True for a 3xx response."""
        return 300 <= self.status_code < 400

    @property
    def ok(self) -> bool:
        """True for a 2xx response."""
        return 200 <= self.status_code < 300


class HttpClient(Protocol):
    """The surface the YouTube feature needs from an HTTP library."""

    def get(
        self,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """Issue a GET request."""

    def head(
        self,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        allow_redirects: bool = False,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """Issue a HEAD request."""

    def post(
        self,
        url: str,
        *,
        json: Optional[Any] = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """Issue a POST request."""


class RequestsHttpClient:
    """``HttpClient`` backed by ``requests``, with a shared session."""

    def __init__(self, session: Optional[requests.Session] = None, timeout: float = DEFAULT_TIMEOUT):
        self.session = session or requests.Session()
        self.timeout = timeout

    def get(self, url, *, params=None, headers=None, timeout=None) -> HttpResponse:
        """Issue a GET request."""
        return self._request("GET", url, params=params, headers=headers, timeout=timeout)

    def head(self, url, *, headers=None, allow_redirects=False, timeout=None) -> HttpResponse:
        """Issue a HEAD request, not following redirects by default."""
        return self._request(
            "HEAD", url, headers=headers, timeout=timeout, allow_redirects=allow_redirects
        )

    def post(self, url, *, json=None, headers=None, timeout=None) -> HttpResponse:
        """Issue a POST request with a JSON body."""
        return self._request("POST", url, json=json, headers=headers, timeout=timeout)

    def _request(self, method, url, **kwargs) -> HttpResponse:
        """Run the request and normalise both the response and the failures."""
        timeout = kwargs.pop("timeout", None) or self.timeout
        try:
            response = self.session.request(method, url, timeout=timeout, **kwargs)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ChunkedEncodingError) as exc:
            # The request was on the wire; we simply never saw the answer.
            raise HttpResponseIncomplete(f"{method} {url}: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise HttpError(f"{method} {url}: {exc}") from exc

        try:
            text = response.text
        except Exception as exc:  # pragma: no cover - defensive
            raise HttpResponseIncomplete(f"{method} {url}: unreadable body: {exc}") from exc

        return HttpResponse(
            status_code=response.status_code,
            text=text,
            headers=dict(response.headers),
            url=response.url,
        )
