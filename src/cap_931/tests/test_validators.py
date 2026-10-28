from unittest.mock import patch

import pytest

from cap_931.validators import (
    ValidationError,
    parse_competitor_urls,
    validate_public_url,
)


def fake_public_dns(*args, **kwargs):
    return [(2, 1, 6, "", ("93.184.215.14", 443))]


def fake_private_dns(*args, **kwargs):
    return [(2, 1, 6, "", ("127.0.0.1", 80))]


def test_valid_public_url():
    with patch("cap_931.validators.socket.getaddrinfo", side_effect=fake_public_dns):
        assert validate_public_url("https://example.com") == "https://example.com"


@pytest.mark.parametrize("url", ["", "not-a-url", "file:///etc/passwd", "http://localhost", "https://user:pass@example.com", "https://example.com:8080"])
def test_invalid_urls(url):
    with pytest.raises(ValidationError):
        validate_public_url(url)


def test_rejects_private_dns():
    with (
        patch("cap_931.validators.socket.getaddrinfo", side_effect=fake_private_dns),
        pytest.raises(ValidationError),
    ):
        validate_public_url("http://example.com")


def test_competitor_deduplication():
    with patch("cap_931.validators.socket.getaddrinfo", side_effect=fake_public_dns):
        assert parse_competitor_urls("https://a.com\nhttps://a.com\nhttps://b.com") == ["https://a.com", "https://b.com"]
