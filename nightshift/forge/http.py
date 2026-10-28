"""Origin-confined Gitea requests using a private urllib opener."""

import json
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class GiteaHTTPError(Exception):
    """A transport failure with safe diagnostics and an optional HTTP status."""

    def __init__(self, status: int | None, message: str, transient: bool):
        self.status, self.message, self.transient = status, message, transient
        super().__init__(message)


class _RefuseRedirects(HTTPRedirectHandler):
    handler_order = 900  # Before HTTPErrorProcessor (1000) dispatches redirects.

    def http_response(self, request, response):
        if 300 <= response.code < 400:
            raise HTTPError(request.full_url, response.code, "Redirect refused",
                            response.headers, response)
        return response

    https_response = http_response


def _unsafe(text: str) -> bool:
    return any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 or c == "\\"
               for c in text)


def _unsafe_path(path: str) -> bool:
    return any(_unsafe(value) or ".." in value.split("/")
               for value in (path, unquote(path)))


def _origin(parts):
    return parts.scheme.lower(), parts.hostname.lower(), parts.port or (
        443 if parts.scheme.lower() == "https" else 80)


def _http_failure(status: int) -> GiteaHTTPError:
    return GiteaHTTPError(status, f"HTTP request failed (status {status})",
                          status == 429 or 500 <= status < 600)


class GiteaTransport:
    """Send requests only to a configured origin and optional path prefix.

    An injected opener must support add_handler and open, honor urllib response
    handlers, and never redirect independently. It is a trusted dependency.
    """

    def __init__(self, base_url: str, token: str, *, opener=None, guard=None,
                 timeout: float = 30):
        try:
            parts = urlsplit(base_url)
            if (_unsafe(base_url) or "?" in base_url or "#" in base_url
                    or not parts.hostname or parts.username is not None
                    or parts.password is not None or parts.netloc.endswith(":")
                    or parts.port == 0 or _unsafe_path(parts.path)
                    or parts.scheme not in ("https", "http")
                    or (parts.scheme == "http" and parts.hostname.lower()
                        not in ("localhost", "127.0.0.1", "::1"))):
                raise ValueError
            self._origin = _origin(parts)
        except ValueError:
            raise ValueError("Invalid Gitea base URL") from None
        if not token or "\r" in token or "\n" in token:
            raise ValueError("Invalid Gitea token")
        if not timeout > 0:
            raise ValueError("Invalid Gitea timeout")
        self._base = parts._replace(path=parts.path.rstrip("/")).geturl()
        self._token, self._timeout, self._guard = token, timeout, guard
        self._opener = build_opener() if opener is None else opener
        self._opener.add_handler(_RefuseRedirects())

    def __repr__(self) -> str:
        return "GiteaTransport()"

    def request(self, method: str, path: str, *, query=None, json_body=None):
        # HTTP methods use ASCII token syntax, including extension methods.
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", method):
            raise ValueError("Invalid Gitea HTTP method")
        if (not path.startswith("/") or path.startswith("//")
                or "?" in path or "#" in path or _unsafe_path(path)):
            raise ValueError("Invalid Gitea API path")
        try:
            url = self._base + path
            if query is not None:
                url += "?" + urlencode(query, doseq=True)
            if _origin(urlsplit(url)) != self._origin:
                raise ValueError("Gitea request origin mismatch")
            headers = {"Authorization": "token " + self._token,
                       "Accept": "application/json"}
            data = None
            if json_body is not None:
                data = json.dumps(json_body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            request = Request(url, data=data, method=method.upper(), headers=headers)
            with self._opener.open(request, timeout=self._timeout) as response:
                status = response.code
                if not 200 <= status < 300:
                    raise _http_failure(status)
                body = response.read()
                try:
                    result = json.loads(body.decode("utf-8")) if body else None
                except (UnicodeError, ValueError):
                    raise GiteaHTTPError(status, "Invalid JSON response", False) from None
                return status, response.headers, result
        except HTTPError as error:
            error.close()
            raise _http_failure(error.code) from None
        except UnicodeError:
            raise GiteaHTTPError(None, "Unsupported request encoding", False) from None
        except (URLError, OSError, HTTPException):
            raise GiteaHTTPError(None, "Connection failed", True) from None

    def get_json(self, path: str, query=None):
        """Return the parsed JSON from a GET, or None for an empty response."""
        _, _, result = self.request("GET", path, query=query)
        return result

    def get_page(self, path: str, query, page: int, limit: int):
        """Return one array and its headers, leaving completeness to the caller."""
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in (page, limit)):
            raise ValueError("Page and limit must be positive integers")
        page_query = dict(query)
        page_query.update(page=page, limit=limit)
        status, headers, items = self.request("GET", path, query=page_query)
        if not isinstance(items, list):
            raise GiteaHTTPError(status, "Expected JSON array response", False)
        return items, headers
