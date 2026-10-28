"""Validation utilities for CAP 931 sales research."""

import ipaddress
import socket
from urllib.parse import urlsplit


class ValidationError(ValueError):
    """Raised for invalid or disallowed user input."""


def validate_public_url(url: str) -> str:
    """Validate URL syntax and reject non-public destinations.

    This is a defense-in-depth check, NOT complete SSRF protection. DNS can
    change between validation and connection. Production needs network egress
    restrictions or a transport that pins validated addresses.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValidationError("A website URL is required.")
    url = url.strip()
    if len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise ValidationError("The URL is too long or contains invalid characters.")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValidationError("The URL is malformed.") from exc
    if parts.scheme not in {"https", "http"} or not hostname:
        raise ValidationError("Use a complete http:// or https:// URL.")
    if parts.username or parts.password or parts.fragment:
        raise ValidationError("Credentials and fragments are not permitted in URLs.")
    if port not in (None, 80, 443):
        raise ValidationError("Only standard web ports 80 and 443 are allowed.")
    if hostname.lower() == "localhost" or hostname.lower().endswith((".localhost", ".local", ".internal")):
        raise ValidationError("Local or internal addresses are not allowed.")
    try:
        addresses = socket.getaddrinfo(hostname, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValidationError("Could not resolve the website hostname.") from exc
    if not addresses:
        raise ValidationError("The hostname has no usable IP addresses.")
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0].split("%", 1)[0])
        if not address.is_global:
            raise ValidationError("The URL resolves to a non-public network address.")
    return url


def parse_competitor_urls(raw: str) -> list[str]:
    """Validate newline-separated competitor URLs, removing duplicates."""
    urls = []
    for candidate in raw.splitlines():
        if candidate.strip():
            url = validate_public_url(candidate)
            if url not in urls:
                urls.append(url)
    if len(urls) > 5:
        raise ValidationError("Enter no more than five competitor URLs.")
    return urls
