"""Explicit, bounded web search and page reading with IP-pinned connections.

This module performs no work until ``search`` or ``read`` is called. It never
uses environment proxies, follows redirects without revalidation, or executes
page content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import http.client
import ipaddress
import json
import logging
import os
from queue import Queue
import re
import socket
import ssl
from threading import BoundedSemaphore, Event, Lock, Thread
from time import monotonic
from typing import Callable, Protocol
import unicodedata
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

logger = logging.getLogger(__name__)


class WebAccessError(RuntimeError):
    """A safe, sanitized web-access failure."""

    def __init__(self, message: str, bytes_received: int = 0):
        self.bytes_received = bytes_received
        super().__init__(message)


class URLValidationError(WebAccessError):
    pass


class WebTimeoutError(WebAccessError):
    pass


class WebHTTPStatusError(WebAccessError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Web request returned HTTP status {status}")


class SearchError(WebAccessError):
    pass


class SearchCredentialsError(SearchError):
    pass


class SearchRateLimitError(SearchError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: str


@dataclass(frozen=True)
class WebPage:
    final_url: str
    title: str
    retrieved_at: datetime
    content_type: str
    status: int
    text: str
    content_hash: str
    source_bytes: int = 0


class SearchProvider(Protocol):
    def search(self, query: str, max_results: int | None = None,
               timeout_seconds: float | None = None, cancel: Event | None = None) -> list[SearchResult]: ...


def _plain_text(value: str) -> str:
    return "".join(char for char in value if unicodedata.category(char) not in {"Cc", "Cf"})


def canonicalize_url(url: str) -> str:
    """Validate URL syntax and canonicalize its authority without resolving DNS."""
    if not isinstance(url, str) or not url or len(url) > 4096:
        raise URLValidationError("URL is empty or exceeds the allowed length")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url) or "\\" in url:
        raise URLValidationError("URL contains unsupported characters")
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise URLValidationError("Only HTTP and HTTPS URLs are supported")
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise URLValidationError("URLs containing credentials are not allowed")
        raw_host = parsed.hostname
        if not raw_host or "%" in raw_host:
            raise URLValidationError("URL hostname is missing or malformed")
        port = parsed.port
    except ValueError as exc:
        if isinstance(exc, URLValidationError):
            raise
        raise URLValidationError("URL authority or port is malformed") from exc
    host = raw_host.rstrip(".").lower()
    if not host:
        raise URLValidationError("URL hostname is malformed")
    try:
        address = ipaddress.ip_address(host)
        address = _public_ip(str(address))
        host = address.compressed
        is_ip = True
    except ValueError:
        is_ip = False
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise URLValidationError("URL hostname is malformed") from exc
        if len(host) > 253 or any(
            not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise URLValidationError("URL hostname is malformed")
        if "." not in host:
            raise URLValidationError("Single-label hostnames are not allowed")
        if any(host == suffix or host.endswith("." + suffix) for suffix in (
            "localhost", "local", "internal", "home", "lan", "test", "invalid", "example", "onion"
        )):
            raise URLValidationError("Local or non-public hostnames are not allowed")
    expected_port = 443 if scheme == "https" else 80
    if port is not None and port != expected_port:
        raise URLValidationError("URL port is not supported")
    if not parsed.netloc or parsed.path.startswith("//"):
        raise URLValidationError("URL is malformed")
    authority_host = f"[{host}]" if is_ip and ":" in host else host
    authority = authority_host if port is None or port == expected_port else f"{authority_host}:{port}"
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&?/:@!$'()*+,;%-._~")
    return urlunsplit((scheme, authority, path, query, ""))


def _public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise URLValidationError("DNS returned an invalid IP address") from exc
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if not address.is_global or address.is_multicast or address.is_unspecified:
        raise URLValidationError("URL resolves to a non-public IP address")
    return address


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, port: int, address: str, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout)
        self._address = str(_public_ip(address))

    def connect(self) -> None:
        address = ipaddress.ip_address(self._address)
        family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sockaddr = ((self._address, self.port, 0, 0) if address.version == 6
                        else (self._address, self.port))
            sock.connect(sockaddr)
            peer = _public_ip(sock.getpeername()[0])
            if peer != address:
                raise WebAccessError("Connected peer did not match the validated destination")
            self.sock = sock
        except Exception:
            sock.close()
            raise


class _PinnedHTTPSConnection(_PinnedHTTPConnection):
    def __init__(self, hostname: str, port: int, address: str, timeout: float):
        super().__init__(hostname, port, address, timeout)
        self._ssl_context = ssl.create_default_context()

    def connect(self) -> None:
        super().connect()
        assert self.sock is not None
        plain = self.sock
        try:
            # The socket is connected to the validated numeric IP; TLS still
            # verifies the certificate against the original URL hostname.
            self.sock = self._ssl_context.wrap_socket(plain, server_hostname=self.host)
        except Exception:
            plain.close()
            raise


@dataclass(frozen=True)
class _Response:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes


ConnectionFactory = Callable[[str, str, int, float], http.client.HTTPConnection]
Resolver = Callable[[str, int], list[str]]


class SecureHTTPTransport:
    """HTTP/1.1 requests with DNS validation and a numeric-IP-pinned socket."""

    def __init__(self, connect_timeout_seconds: float = 5, read_timeout_seconds: float = 5,
                 total_timeout_seconds: float = 15, max_redirects: int = 3,
                 resolver: Resolver | None = None, connection_factory: ConnectionFactory | None = None,
                 clock: Callable[[], float] = monotonic):
        for name, value in (("connect timeout", connect_timeout_seconds),
                            ("read timeout", read_timeout_seconds),
                            ("total timeout", total_timeout_seconds)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 120:
                raise ValueError(f"{name} must be greater than zero and at most 120 seconds")
        if isinstance(max_redirects, bool) or not isinstance(max_redirects, int) or not 0 <= max_redirects <= 5:
            raise ValueError("max_redirects must be between zero and five")
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.read_timeout_seconds = float(read_timeout_seconds)
        self.total_timeout_seconds = float(total_timeout_seconds)
        self.max_redirects = max_redirects
        self._resolver = resolver
        self._connection_factory = connection_factory or self._connection
        self._clock = clock
        self._resolver_lock = Lock()
        self._resolver_timed_out = False

    @staticmethod
    def _connection(url: str, address: str, port: int, timeout: float) -> http.client.HTTPConnection:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        cls = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
        return cls(hostname, port, address, timeout)

    def _resolve(self, host: str, port: int, timeout: float) -> list[str]:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            return [str(literal)]
        with self._resolver_lock:
            if self._resolver_timed_out:
                raise WebTimeoutError("DNS resolution previously timed out; this transport is disabled")
            resolver = self._resolver or self._system_resolve
            result: Queue[tuple[bool, object]] = Queue(maxsize=1)

            def run() -> None:
                try:
                    result.put((True, resolver(host, port)))
                except BaseException as exc:
                    result.put((False, exc))

            worker = Thread(target=run, name="sylphy-dns-resolution", daemon=True)
            worker.start()
            worker.join(timeout=max(0.001, timeout))
            if worker.is_alive():
                # A platform resolver call cannot be forcibly cancelled. Fail
                # closed and poison this transport so repeated calls cannot
                # accumulate stuck resolver threads.
                self._resolver_timed_out = True
                raise WebTimeoutError("DNS resolution timed out")
            succeeded, value = result.get_nowait()
            if not succeeded:
                if isinstance(value, TimeoutError):
                    raise WebTimeoutError("DNS resolution timed out") from None
                raise WebAccessError("DNS resolution failed") from None
            addresses = list(value)  # type: ignore[arg-type]
            if not addresses:
                raise WebAccessError("DNS returned no addresses")
            # Reject mixed answers: the peer selection must only use addresses
            # that have all passed the public-address policy.
            return [str(_public_ip(item)) for item in dict.fromkeys(addresses)]

    @staticmethod
    def _system_resolve(host: str, port: int) -> list[str]:
        rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        addresses: list[str] = []
        for family, _, _, _, sockaddr in rows:
            if family not in {socket.AF_INET, socket.AF_INET6}:
                raise URLValidationError("DNS returned an unsupported address family")
            if family == socket.AF_INET6 and len(sockaddr) > 3 and sockaddr[3] != 0:
                raise URLValidationError("Scoped IPv6 destinations are not allowed")
            addresses.append(sockaddr[0])
        return addresses

    def request(self, url: str, headers: dict[str, str], max_response_bytes: int,
                accepted_content_types: set[str], max_redirects: int | None = None,
                cancel: Event | None = None, total_timeout_seconds: float | None = None) -> _Response:
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        redirect_limit = self.max_redirects if max_redirects is None else max_redirects
        if isinstance(redirect_limit, bool) or not isinstance(redirect_limit, int) or not 0 <= redirect_limit <= self.max_redirects:
            raise ValueError("redirect limit exceeds configured maximum")
        total_timeout = self.total_timeout_seconds if total_timeout_seconds is None else total_timeout_seconds
        if (isinstance(total_timeout, bool) or not isinstance(total_timeout, (int, float))
                or not 0 < total_timeout <= self.total_timeout_seconds):
            raise ValueError("request total timeout exceeds configured maximum")
        started = self._clock()
        deadline = started + total_timeout
        current = canonicalize_url(url)
        for redirect_count in range(redirect_limit + 1):
            if cancel and cancel.is_set():
                raise InterruptedError("Web request cancelled")
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise WebTimeoutError("Web request exceeded its total time limit")
            parsed = urlsplit(current)
            host = parsed.hostname or ""
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = self._resolve(host, port, min(self.connect_timeout_seconds, remaining))
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise WebTimeoutError("Web request exceeded its total time limit")
            # All answers are checked, including unused alternatives. Pinning
            # below ensures a later DNS answer change cannot alter this hop.
            for address in addresses:
                _public_ip(address)
            peer_address = addresses[0]
            timeout = min(self.connect_timeout_seconds, max(0.001, deadline - self._clock()))
            connection = self._connection_factory(current, peer_address, port, timeout)
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            request_headers = {
                "Host": f"[{host}]" if ":" in host else host,
                "User-Agent": "SYLPHY-WebReader/1.0",
                "Accept-Encoding": "identity",
                "Connection": "close",
                **headers,
            }
            try:
                if cancel and cancel.is_set():
                    raise InterruptedError("Web request cancelled")
                connection.request("GET", path, headers=request_headers)
                if connection.sock is not None:
                    connection.sock.settimeout(min(self.read_timeout_seconds, max(0.001, deadline - self._clock())))
                response = connection.getresponse()
                if cancel and cancel.is_set():
                    raise InterruptedError("Web request cancelled")
                response_headers = {key.lower(): value.strip() for key, value in response.getheaders()}
                if response.status in {301, 302, 303, 307, 308}:
                    location = response_headers.get("location")
                    if not location:
                        raise WebAccessError("Redirect response has no destination")
                    if redirect_count >= redirect_limit:
                        raise WebAccessError("Web request exceeded the redirect limit")
                    current = canonicalize_url(urljoin(current, location))
                    continue
                if response.status < 200 or response.status >= 300:
                    raise WebHTTPStatusError(response.status)
                content_type = response_headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type not in accepted_content_types:
                    raise WebAccessError("Web response has an unsupported content type")
                encoding = response_headers.get("content-encoding", "identity").lower()
                if encoding not in {"", "identity"}:
                    raise WebAccessError("Compressed web responses are not supported")
                declared_length = response_headers.get("content-length")
                if declared_length is not None:
                    if not declared_length.isdigit():
                        raise WebAccessError("Web response has a malformed content length")
                    if int(declared_length) > max_response_bytes:
                        raise WebAccessError("Web response exceeds the configured byte limit")
                chunks: list[bytes] = []
                size = 0
                while True:
                    if cancel and cancel.is_set():
                        raise InterruptedError("Web request cancelled")
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise WebTimeoutError("Web request exceeded its total time limit")
                    if connection.sock is not None:
                        connection.sock.settimeout(min(self.read_timeout_seconds, remaining))
                    chunk = response.read(min(65536, max_response_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_response_bytes:
                        raise WebAccessError("Web response exceeds the configured byte limit", size)
                    chunks.append(chunk)
                return _Response(current, response.status, response_headers, b"".join(chunks))
            except (WebAccessError, InterruptedError):
                raise
            except (TimeoutError, socket.timeout):
                raise WebTimeoutError("Web request timed out") from None
            except Exception as exc:
                logger.debug("Web request failed (%s)", type(exc).__name__)
                raise WebAccessError("Web request failed") from None
            finally:
                connection.close()
        raise WebAccessError("Web request exceeded the redirect limit")


class BraveSearchProvider:
    """Explicit Brave Search API client. It receives only the caller's query."""

    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
    PROVIDER = "brave"
    MAX_QUERY_CHARS = 512
    MAX_RESULTS = 10
    MAX_RESPONSE_BYTES = 512_000

    def __init__(self, api_key: str | None = None, timeout_seconds: float = 10,
                 max_results: int = 5, max_response_bytes: int = MAX_RESPONSE_BYTES,
                 transport: SecureHTTPTransport | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("BRAVE_SEARCH_API_KEY")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 60:
            raise ValueError("Search timeout must be greater than zero and at most 60 seconds")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= self.MAX_RESULTS:
            raise ValueError("Search max_results must be between one and ten")
        if (isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int)
                or not 1 <= max_response_bytes <= self.MAX_RESPONSE_BYTES):
            raise ValueError("Search max_response_bytes must be between one and 512000")
        self.timeout_seconds = float(timeout_seconds)
        self.max_results = max_results
        self.max_response_bytes = max_response_bytes
        self.transport = transport or SecureHTTPTransport(
            connect_timeout_seconds=min(5, timeout_seconds), read_timeout_seconds=min(5, timeout_seconds),
            total_timeout_seconds=timeout_seconds, max_redirects=0,
        )

    def search(self, query: str, max_results: int | None = None,
               timeout_seconds: float | None = None, cancel: Event | None = None) -> list[SearchResult]:
        if not isinstance(query, str) or not query.strip() or len(query) > self.MAX_QUERY_CHARS:
            raise SearchError("Search query must contain between one and 512 characters")
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in query):
            raise SearchError("Search query contains unsupported control characters")
        if not self.api_key or not self.api_key.strip():
            raise SearchCredentialsError("Brave Search is enabled but BRAVE_SEARCH_API_KEY is missing")
        limit = self.max_results if max_results is None else max_results
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.max_results:
            raise SearchError("Requested result count exceeds the configured maximum")
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= self.timeout_seconds:
            raise SearchError("Requested timeout exceeds the configured maximum")
        url = self.ENDPOINT + "?" + urlencode({"q": query.strip(), "count": limit})
        try:
            response = self.transport.request(
                url, {"Accept": "application/json", "X-Subscription-Token": self.api_key.strip()},
                self.max_response_bytes, {"application/json"}, max_redirects=0, cancel=cancel,
                total_timeout_seconds=float(timeout),
            )
        except WebHTTPStatusError as exc:
            if exc.status == 429:
                raise SearchRateLimitError("Brave Search rate limit was reached") from None
            if exc.status in {401, 403}:
                raise SearchCredentialsError("Brave Search rejected the configured credentials") from None
            raise SearchError(f"Brave Search returned HTTP status {exc.status}") from None
        except InterruptedError:
            raise
        except WebAccessError as exc:
            if isinstance(exc, WebTimeoutError):
                raise SearchError("Brave Search request timed out") from None
            raise SearchError("Brave Search request failed") from None
        try:
            data = json.loads(response.body.decode("utf-8"))
            results = data["web"]["results"]
            if not isinstance(results, list):
                raise ValueError
            output: list[SearchResult] = []
            for item in results[:limit]:
                if not isinstance(item, dict) or not all(isinstance(item.get(field), str)
                                                         for field in ("title", "url", "description")):
                    raise ValueError
                title = " ".join(_plain_text(item["title"]).split())[:500]
                snippet = " ".join(_plain_text(item["description"]).split())[:1500]
                result_url = canonicalize_url(item["url"])
                output.append(SearchResult(title, result_url, snippet, self.PROVIDER))
            return output
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, URLValidationError):
            raise SearchError("Brave Search returned a malformed response") from None


class _ReadableHTML(HTMLParser):
    _IGNORED = {"script", "style", "nav", "footer", "header", "aside", "form", "iframe",
                "noscript", "svg", "canvas", "button", "template", "meta", "link", "object",
                "video", "audio"}
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
             "param", "source", "track", "wbr"}
    _BLOCKS = {"article", "main", "section", "div", "p", "li", "ul", "ol", "h1", "h2", "h3",
               "h4", "h5", "h6", "blockquote", "tr", "td", "th", "br", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._title_depth = 0
        self._suppressed_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attributes = {key.lower(): (value or "") for key, value in attrs}
        markers = (attributes.get("class", "") + " " + attributes.get("id", "")).lower()
        marker_tokens = set(re.split(r"[^a-z0-9]+", markers))
        noncontent_markers = {"ad", "ads", "advert", "advertisement", "advertising", "sponsor",
                              "sponsored", "tracking", "tracker", "cookie-banner", "sidebar", "navbar"}
        hidden = ("hidden" in attributes or attributes.get("aria-hidden", "").lower() == "true"
                  or attributes.get("role", "").lower() in {"navigation", "banner", "contentinfo", "complementary"}
                  or bool(marker_tokens & noncontent_markers)
                  or "display:none" in attributes.get("style", "").replace(" ", "").lower()
                  or "visibility:hidden" in attributes.get("style", "").replace(" ", "").lower())
        if self._suppressed_depth:
            if tag not in self._VOID:
                self._suppressed_depth += 1
            return
        if tag in self._IGNORED or hidden:
            if tag not in self._VOID:
                self._suppressed_depth = 1
            return
        if tag == "title":
            self._title_depth += 1
        if tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._suppressed_depth:
            if tag not in self._VOID:
                self._suppressed_depth = max(0, self._suppressed_depth - 1)
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._suppressed_depth or not data.strip():
            return
        if self._title_depth:
            self.title_parts.append(data)
        else:
            self.parts.append(data)


class WebReader:
    """Fetch and extract bounded HTML text. Results remain untrusted data."""

    CONTENT_TYPES = {"text/html", "application/xhtml+xml"}

    def __init__(self, transport: SecureHTTPTransport | None = None, connect_timeout_seconds: float = 5,
                 read_timeout_seconds: float = 5, total_timeout_seconds: float = 15,
                 max_redirects: int = 3, max_response_bytes: int = 1_048_576,
                 max_extracted_chars: int = 20_000, max_concurrent_requests: int = 1):
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int) or not 1 <= max_response_bytes <= 10_000_000:
            raise ValueError("max_response_bytes must be between one and 10000000")
        if isinstance(max_extracted_chars, bool) or not isinstance(max_extracted_chars, int) or not 1 <= max_extracted_chars <= 1_000_000:
            raise ValueError("max_extracted_chars must be between one and 1000000")
        if isinstance(max_concurrent_requests, bool) or not isinstance(max_concurrent_requests, int) or not 1 <= max_concurrent_requests <= 4:
            raise ValueError("max_concurrent_requests must be between one and four")
        self.transport = transport or SecureHTTPTransport(connect_timeout_seconds, read_timeout_seconds,
                                                          total_timeout_seconds, max_redirects)
        self.max_redirects = max_redirects
        self.total_timeout_seconds = getattr(transport, "total_timeout_seconds", total_timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self.max_extracted_chars = max_extracted_chars
        self._requests = BoundedSemaphore(max_concurrent_requests)

    def read(self, url: str, cancel: Event | None = None, *,
             max_response_bytes: int | None = None,
             max_extracted_chars: int | None = None,
             timeout_seconds: float | None = None) -> WebPage:
        byte_limit = self.max_response_bytes if max_response_bytes is None else max_response_bytes
        text_limit = self.max_extracted_chars if max_extracted_chars is None else max_extracted_chars
        if (isinstance(byte_limit, bool) or not isinstance(byte_limit, int)
                or not 1 <= byte_limit <= self.max_response_bytes):
            raise ValueError("Per-request byte limit exceeds the configured Web Reader limit")
        if (isinstance(text_limit, bool) or not isinstance(text_limit, int)
                or not 1 <= text_limit <= self.max_extracted_chars):
            raise ValueError("Per-request text limit exceeds the configured Web Reader limit")
        request_timeout = (self.total_timeout_seconds if timeout_seconds is None
                           else timeout_seconds)
        if (isinstance(request_timeout, bool) or not isinstance(request_timeout, (int, float))
                or not 0 < request_timeout <= self.total_timeout_seconds):
            raise ValueError("Per-request timeout exceeds the configured Web Reader limit")
        if not self._requests.acquire(blocking=False):
            raise WebAccessError("Maximum concurrent web-reader requests are active")
        try:
            response = self.transport.request(
                url, {"Accept": "text/html, application/xhtml+xml"}, byte_limit,
                self.CONTENT_TYPES, self.max_redirects, cancel, float(request_timeout),
            )
        finally:
            self._requests.release()
        charset = "utf-8"
        content_type_header = response.headers.get("content-type", "")
        for parameter in content_type_header.split(";")[1:]:
            key, sep, value = parameter.partition("=")
            if sep and key.strip().lower() == "charset":
                charset = value.strip().strip('"\'')[:64] or "utf-8"
                break
        try:
            html = response.body.decode(charset, errors="replace")
            parser = _ReadableHTML()
            parser.feed(html)
            parser.close()
            text = re.sub(r"\s+([.,!?;:%\)\]\}])", r"\1",
                          _plain_text(" ".join(" ".join(parser.parts).split())))[:text_limit]
            title = " ".join(_plain_text(" ".join(parser.title_parts)).split())[:500]
        except (LookupError, ValueError, AssertionError):
            raise WebAccessError("Web page HTML could not be parsed") from None
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return WebPage(response.url, title, datetime.now(timezone.utc),
                       content_type_header, response.status, text, content_hash, len(response.body))
