from unittest.mock import MagicMock, patch

import pytest

from cap_931.web_research import ResearchError, extract_page, research_website


def test_extracts_main_content():
    html = "<html><head><title>Sample Corp</title></head><body><nav>Menu</nav><main><h1>Our strategy</h1><p>Building data platforms.</p></main><script>secret()</script></body></html>"
    title, text = extract_page(html)
    assert title == "Sample Corp"
    assert "Our strategy" in text
    assert "Building data platforms." in text
    assert "Menu" not in text
    assert "secret()" not in text


def test_missing_title():
    title, text = extract_page("<html><body><p>Hello sales</p></body></html>")
    assert title == "Untitled page"
    assert text == "Hello sales"


def test_blocked_html_is_rejected_before_becoming_evidence():
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = 200
    response.headers = {"Content-Type": "text/html"}
    response.encoding = "utf-8"
    response.iter_content.return_value = [
        (
            b"<html><title>Attention Required! | Cloudflare</title>"
            b"<body>Complete the security check to continue.</body></html>"
        )
    ]
    session = MagicMock()
    session.get.return_value = response

    with (
        patch("cap_931.web_research.requests.Session", return_value=session),
        patch("cap_931.web_research.validate_public_url", side_effect=lambda url: url),
        pytest.raises(ResearchError, match="access was blocked"),
    ):
        research_website("https://blocked.example")

    session.close.assert_called_once()
