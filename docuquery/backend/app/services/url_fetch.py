"""URL import with SSRF protection.

``assert_public_url`` resolves every address the hostname maps to and rejects
private, loopback, link-local, and otherwise reserved ranges (IPv4 + IPv6),
plus URLs carrying credentials or non-http(s) schemes. ``fetch_document``
streams the response with a hard size cap and a timeout, re-validating every
redirect target (redirects must never become an SSRF bypass).

Residual risk note: the target may re-resolve to a private address between the
check and the connection (DNS rebinding). Fully closing that gap requires
connecting to a pinned IP with custom TLS handling, which is out of scope here.
"""

import ipaddress
import socket
from urllib.parse import unquote, urljoin, urlparse

import httpx

MAX_REDIRECTS = 5
ALLOWED_CONTENT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/pdf",
    "application/octet-stream",
}


class UrlImportError(ValueError):
    """Raised for any URL the importer refuses to fetch."""


def _is_forbidden_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable address: fail closed
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def assert_public_url(url: str) -> str:
    """Validate scheme, credentials, and every resolved address. Returns the URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UrlImportError("Only http(s) URLs can be imported.")
    if not parsed.hostname:
        raise UrlImportError("URL has no hostname.")
    if parsed.username or parsed.password:
        raise UrlImportError("URLs with embedded credentials are not allowed.")

    try:
        addr_infos = socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
        )
    except socket.gaierror as exc:
        raise UrlImportError(f"Could not resolve hostname: {parsed.hostname}") from exc

    for info in addr_infos:
        ip = info[4][0]
        if _is_forbidden_ip(ip):
            raise UrlImportError(
                "This URL points at a private or local network address and cannot be imported."
            )
    return url


def fetch_document(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, str]:
    """Fetch a URL with redirects re-validated, a timeout, and a size cap.

    Returns (content, content_type). Raises UrlImportError on refusal or when
    the response is oversized, or httpx.TransportError on network failure.
    """
    assert_public_url(url)

    with httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout_seconds),
        headers={"User-Agent": "DocuQuery/1.0 (+document import)"},
        transport=transport,
    ) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            response = client.get(current)
            if response.is_redirect:
                location = response.headers.get("location", "")
                current = urljoin(current, location)
                assert_public_url(current)  # a redirect must not bypass the SSRF guard
                continue

            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                raise UrlImportError(
                    f"Unsupported content type '{content_type}'. Only documents can be imported."
                )

            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > max_bytes:
                    raise UrlImportError(
                        f"Remote document exceeds the {max_bytes // (1024 * 1024)} MB import limit."
                    )
                chunks.append(chunk)
            return b"".join(chunks), content_type or "application/octet-stream"

    raise UrlImportError(f"Too many redirects (more than {MAX_REDIRECTS}).")


def suggest_name(url: str, stored_ext: str) -> str:
    """Derive a readable original_name from the URL path plus the stored extension."""
    parsed = urlparse(url)
    tail = [seg for seg in reversed(parsed.path.split("/")) if seg]
    name = unquote(tail[0]) if tail else ""
    stem = name.rsplit(".", 1)[0] if "." in name and not name.endswith(".") else name
    if not stem:
        stem = parsed.hostname or "imported-document"
    return f"{stem[:200]}{stored_ext}"
