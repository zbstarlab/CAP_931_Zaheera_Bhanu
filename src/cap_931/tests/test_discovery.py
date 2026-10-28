import logging
from io import BytesIO
from urllib.robotparser import RobotFileParser

import pytest
import requests
from pypdf import PdfWriter
from reportlab.pdfgen import canvas

from cap_931.web_research import (
    MAX_HTML_BYTES,
    MAX_PDF_BYTES,
    MAX_TOTAL_EXTRACTED_CHARS,
    FetchResult,
    ResearchError,
    _extract_html_details,
    _fetch_bytes,
    _retrieve_source,
    approved_hostname,
    canonicalize_url,
    discover_relevant_links,
    extract_public_pdf,
    is_approved_domain,
    research_companies,
    research_site,
)


class FakeResponse:
    def __init__(self, status=200, headers=None, data=b"", encoding="utf-8"):
        self.status_code = status
        self.headers = headers or {}
        self.data = data
        self.encoding = encoding

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, chunk_size=8192):
        del chunk_size
        return [self.data]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append((url, kwargs))
        return next(self.responses)


def make_text_pdf(*pages: str) -> bytes:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    for text in pages:
        pdf.drawString(72, 720, text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def make_blank_pdf(*, encrypted=False) -> bytes:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("secret")
    writer.write(buffer)
    return buffer.getvalue()


def make_source(source_id, source_type, url, content="evidence"):
    return {
        "source_id": source_id,
        "url": url,
        "title": url.rsplit("/", 1)[-1] or "Home",
        "retrieved_at": "2026-01-01T00:00:00+00:00",
        "source_type": source_type,
        "content_type": "html",
        "publication_date": None,
        "relevance_score": 1,
        "content": content,
    }


def allow_all_robots():
    parser = RobotFileParser()
    parser.parse([])
    return parser


def test_relevant_link_discovery_is_scored_deduplicated_and_same_domain_only():
    html = """
    <a href="/news?utm_source=email">News</a>
    <a href="https://www.example.com/news">Company news duplicate</a>
    <a href="https://investors.example.com/annual-report.pdf">Annual Report</a>
    <a href="/about/leadership">Executive Leadership</a>
    <a href="https://example.org/strategy">External strategy</a>
    <a href="/privacy">Privacy</a>
    """

    links = discover_relevant_links(html, "https://example.com/")
    urls = [item["url"] for item in links]

    assert urls.count("https://example.com/news") == 1
    assert "https://investors.example.com/annual-report.pdf" in urls
    assert "https://example.com/about/leadership" in urls
    assert all("example.org" not in url for url in urls)
    assert all("privacy" not in url for url in urls)
    assert links == sorted(links, key=lambda item: (-item["score"], item["url"]))


def test_domain_boundary_and_url_canonicalization():
    host = approved_hostname("https://www.example.com")

    assert host == "example.com"
    assert is_approved_domain("https://investors.example.com/report", host)
    assert not is_approved_domain("https://example.com.evil.test/report", host)
    assert canonicalize_url(
        "HTTPS://WWW.EXAMPLE.COM:443/news/?utm_source=x&b=2&a=1#top"
    ) == "https://www.example.com/news/?a=1&b=2"
    assert canonicalize_url("https://example.com") == "https://example.com/"
    assert canonicalize_url("https://example.com/about/").endswith("/about/")


def test_redirects_are_manually_followed_and_revalidated(monkeypatch):
    session = FakeSession(
        [
            FakeResponse(302, {"Location": "/news"}),
            FakeResponse(
                200,
                {"Content-Type": "text/html"},
                b"<html><body>News</body></html>",
            ),
        ]
    )
    validated = []
    monkeypatch.setattr(
        "cap_931.web_research.validate_public_url",
        lambda url: validated.append(url) or url,
    )

    fetched = _fetch_bytes(session, "https://example.com", "example.com")

    assert fetched.url == "https://example.com/news"
    assert validated == ["https://example.com/", "https://example.com/news"]
    assert len(session.requested) == 2


def test_relative_redirects_preserve_trailing_slash_semantics(monkeypatch):
    session = FakeSession(
        [
            FakeResponse(301, {"Location": "https://www.example.com/company/"}),
            FakeResponse(302, {"Location": "news/"}),
            FakeResponse(
                200,
                {"Content-Type": "text/html"},
                b"<html><body>Company news</body></html>",
            ),
        ]
    )
    monkeypatch.setattr(
        "cap_931.web_research.validate_public_url", lambda url: url
    )

    fetched = _fetch_bytes(session, "http://example.com", "example.com")

    assert fetched.url == "https://www.example.com/company/news/"
    assert [request[0] for request in session.requested] == [
        "http://example.com/",
        "https://www.example.com/company/",
        "https://www.example.com/company/news/",
    ]


def test_redirect_loop_stops_when_canonical_url_repeats(monkeypatch):
    session = FakeSession(
        [
            FakeResponse(302, {"Location": "/second?utm_source=first"}),
            FakeResponse(302, {"Location": "/first#again"}),
        ]
    )
    monkeypatch.setattr(
        "cap_931.web_research.validate_public_url", lambda url: url
    )

    with pytest.raises(ResearchError, match="Redirect loop detected") as exc_info:
        _fetch_bytes(session, "https://example.com/first", "example.com")

    assert "https://example.com/first" in str(exc_info.value)
    assert len(session.requested) == 2


def test_redirect_limit_remains_three_and_reports_last_url(monkeypatch):
    session = FakeSession(
        [
            FakeResponse(302, {"Location": f"/step-{number}"})
            for number in range(1, 5)
        ]
    )
    monkeypatch.setattr(
        "cap_931.web_research.validate_public_url", lambda url: url
    )

    with pytest.raises(ResearchError, match="3-redirect limit") as exc_info:
        _fetch_bytes(session, "https://example.com/start", "example.com")

    assert "https://example.com/step-3" in str(exc_info.value)
    assert len(session.requested) == 4


def test_redirect_diagnostics_redact_sensitive_query_values(monkeypatch, caplog):
    session = FakeSession(
        [
            FakeResponse(
                302,
                {"Location": "/news?token=secret-value&section=company"},
            ),
            FakeResponse(
                200,
                {"Content-Type": "text/html"},
                b"<html><body>News</body></html>",
            ),
        ]
    )
    monkeypatch.setattr(
        "cap_931.web_research.validate_public_url", lambda url: url
    )

    with caplog.at_level(logging.DEBUG, logger="cap_931.web_research"):
        _fetch_bytes(session, "https://example.com/", "example.com")

    assert "Public-source redirect 1/3" in caplog.text
    assert "secret-value" not in caplog.text
    assert "token=%5Bredacted%5D" in caplog.text


def test_redirect_outside_approved_domain_is_rejected(monkeypatch):
    session = FakeSession(
        [FakeResponse(302, {"Location": "https://evil.test/strategy"})]
    )
    monkeypatch.setattr(
        "cap_931.web_research.validate_public_url", lambda url: url
    )

    with pytest.raises(ResearchError, match="left the approved company domain"):
        _fetch_bytes(session, "https://example.com", "example.com")

    assert len(session.requested) == 1


def test_source_metadata_and_publication_date(monkeypatch):
    html = b"""
    <html><head><title>Company News</title>
    <meta property="article:published_time" content="2026-02-03T10:00:00Z">
    </head><body><main>Public initiative announced.</main>
    <a href="/about/leadership">Leadership</a></body></html>
    """
    monkeypatch.setattr(
        "cap_931.web_research._fetch_bytes",
        lambda *args, **kwargs: FetchResult(
            "https://example.com/news", 200, "text/html", "utf-8", html
        ),
    )

    source, links = _retrieve_source(
        object(),
        "https://example.com/news",
        "S7",
        "prospect",
        "example.com",
        12,
    )

    assert source["source_id"] == "S7"
    assert source["source_type"] == "prospect"
    assert source["publication_date"] == "2026-02-03"
    assert source["content_type"] == "html"
    assert source["relevance_score"] == 12
    assert source["retrieved_at"].endswith("+00:00")
    assert links[0]["url"] == "https://example.com/about/leadership"


def test_html_extraction_falls_back_when_main_is_empty():
    html = """
    <html><head><title>Company</title></head><body>
    <main></main><section>Readable company strategy outside main.</section>
    </body></html>
    """

    title, text, publication_date = _extract_html_details(html)

    assert title == "Company"
    assert text == "Readable company strategy outside main."
    assert publication_date is None


def test_html_extraction_can_use_authored_meta_description():
    html = """
    <html><head><title>Company</title>
    <meta name="description" content="Official company overview.">
    <script src="/application.js"></script></head><body><div id="app"></div></body>
    </html>
    """

    _, text, _ = _extract_html_details(html)

    assert text == "Official company overview."


@pytest.mark.parametrize(
    ("html", "message"),
    [
        ("<html><body></body></html>", "No readable content"),
        (
            "<html><body><div id='app'></div><script src='/app.js'></script></body></html>",
            "appears to require JavaScript",
        ),
        (
            "<html><body><noscript>Enable JavaScript and cookies to continue</noscript></body></html>",
            "access was blocked",
        ),
    ],
    ids=["empty", "javascript-only", "blocked-noscript"],
)
def test_unreadable_html_reasons_include_source_url(monkeypatch, html, message):
    monkeypatch.setattr(
        "cap_931.web_research._fetch_bytes",
        lambda *args, **kwargs: FetchResult(
            "https://example.com/problem",
            200,
            "text/html",
            "utf-8",
            html.encode(),
        ),
    )

    with pytest.raises(ResearchError, match=message) as exc_info:
        _retrieve_source(
            object(),
            "https://example.com/problem",
            "S1",
            "prospect",
            "example.com",
            1,
        )

    assert "Source: https://example.com/problem" in str(exc_info.value)


@pytest.mark.parametrize("use_content_length", [True, False])
def test_oversized_html_is_rejected_with_url(monkeypatch, use_content_length):
    headers = {"Content-Type": "text/html"}
    data = b"x" * (MAX_HTML_BYTES + 1)
    if use_content_length:
        headers["Content-Length"] = str(len(data))
    session = FakeSession([FakeResponse(200, headers, data)])
    monkeypatch.setattr(
        "cap_931.web_research.validate_public_url", lambda url: url
    )

    with pytest.raises(ResearchError, match="3,000,000-byte limit") as exc_info:
        _fetch_bytes(session, "https://example.com/large", "example.com")

    assert "Source: https://example.com/large" in str(exc_info.value)


def test_non_article_structured_date_is_not_reported_as_publication_date():
    html = """
    <html><head><title>Home</title>
    <script type="application/ld+json">
    {"@type": "Organization", "dateCreated": "1990-01-01",
     "datePublished": "2026-01-02"}
    </script></head><body>Company home</body></html>
    """

    _, _, publication_date = _extract_html_details(html)

    assert publication_date is None


def test_public_pdf_extraction_obeys_page_limit():
    result = extract_public_pdf(
        make_text_pdf("First page strategy", "Second page detail"),
        "https://example.com/annual-report.pdf",
        max_pages=1,
    )

    assert result["page_count"] == 2
    assert result["content_truncated"]
    assert "First page strategy" in result["content"]
    assert "Second page detail" not in result["content"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"%PDF-1.7\nbroken", "malformed or unreadable"),
        (b"not pdf", "valid PDF signature"),
        (make_blank_pdf(encrypted=True), "Encrypted"),
        (make_blank_pdf(), "require OCR"),
        (b"%PDF-" + b"x" * MAX_PDF_BYTES, "5 MB"),
    ],
    ids=["malformed", "signature", "encrypted", "image-only", "oversized"],
)
def test_public_pdf_error_cases(data, message):
    with pytest.raises(ResearchError, match=message):
        extract_public_pdf(data, "https://example.com/report.pdf")


def test_pdf_url_requires_pdf_content_type(monkeypatch):
    monkeypatch.setattr(
        "cap_931.web_research._fetch_bytes",
        lambda *args, **kwargs: FetchResult(
            "https://example.com/report.pdf",
            200,
            "text/html",
            "utf-8",
            b"<html><body>Not a PDF</body></html>",
        ),
    )

    with pytest.raises(ResearchError, match="application/pdf"):
        _retrieve_source(
            object(),
            "https://example.com/report.pdf",
            "S1",
            "prospect",
            "example.com",
            10,
        )


def test_research_site_handles_partial_failures_and_preserves_source_ids(monkeypatch):
    links = [
        {"url": "https://example.com/strategy", "score": 20, "label": "Strategy"},
        {"url": "https://example.com/news", "score": 10, "label": "News"},
        {"url": "https://example.com/careers", "score": 5, "label": "Careers"},
    ]

    def retrieve(session, url, source_id, source_type, host, relevance_score):
        del session, host, relevance_score
        if url == "https://example.com/":
            return make_source(source_id, source_type, url), links
        if url.endswith("/strategy"):
            raise ResearchError("strategy page failed")
        return make_source(source_id, source_type, url), []

    monkeypatch.setattr(
        "cap_931.web_research._load_robots",
        lambda *args: (allow_all_robots(), None),
    )
    monkeypatch.setattr("cap_931.web_research._retrieve_source", retrieve)

    sources, warnings, next_index = research_site(
        "https://example.com/",
        "prospect",
        1,
        max_pages=2,
        max_attempts=3,
    )

    assert [source["source_id"] for source in sources] == ["S1", "S3"]
    assert sources[1]["url"].endswith("/news")
    assert next_index == 4
    assert any("strategy page failed" in warning for warning in warnings)
    assert any(
        "Source: https://example.com/strategy" in warning for warning in warnings
    )


def test_partial_failures_never_become_sources_and_warnings_are_deduplicated(
    monkeypatch,
):
    def fake_site(url, source_type, start_index, **kwargs):
        del url, kwargs
        if source_type == "prospect":
            return (
                [make_source(f"S{start_index}", source_type, "https://good.example/")],
                ["Repeated warning Source: https://failed.example/page"] * 2,
                start_index + 2,
            )
        return (
            [],
            ["Repeated warning Source: https://failed.example/page"],
            start_index + 1,
        )

    monkeypatch.setattr("cap_931.web_research.research_site", fake_site)

    sources, warnings = research_companies(
        "https://good.example/", ["https://failed.example/"]
    )

    assert [source["url"] for source in sources] == ["https://good.example/"]
    assert warnings == ["Repeated warning Source: https://failed.example/page"]


def test_robots_rules_prevent_disallowed_discovered_page(monkeypatch):
    robots = RobotFileParser()
    robots.parse(["User-agent: *", "Disallow: /about/leadership"])

    def retrieve(session, url, source_id, source_type, host, relevance_score):
        del session, host, relevance_score
        source = make_source(source_id, source_type, url)
        links = [
            {
                "url": "https://example.com/about/leadership",
                "score": 20,
                "label": "Leadership",
            }
        ]
        return source, links

    monkeypatch.setattr(
        "cap_931.web_research._load_robots", lambda *args: (robots, None)
    )
    monkeypatch.setattr("cap_931.web_research._retrieve_source", retrieve)

    sources, warnings, next_index = research_site(
        "https://example.com/", "prospect", 1, max_pages=2
    )

    assert [source["source_id"] for source in sources] == ["S1"]
    assert next_index == 3
    assert any("robots.txt disallows" in warning for warning in warnings)


def test_company_research_keeps_attribution_and_survives_site_failure(monkeypatch):
    def fake_site(url, source_type, start_index, **kwargs):
        del kwargs
        if "blocked" in url:
            return [], ["blocked site"], start_index + 1
        return (
            [make_source(f"S{start_index}", source_type, url)],
            [],
            start_index + 1,
        )

    monkeypatch.setattr("cap_931.web_research.research_site", fake_site)

    sources, warnings = research_companies(
        "https://prospect.example",
        ["https://blocked.example", "https://competitor.example"],
    )

    assert [source["source_id"] for source in sources] == ["S1", "S3"]
    assert [source["source_type"] for source in sources] == [
        "prospect",
        "competitor",
    ]
    assert warnings == ["blocked site"]


def test_company_research_deduplicates_sites_and_caps_total_text(monkeypatch):
    def fake_site(url, source_type, start_index, **kwargs):
        del kwargs
        return (
            [
                make_source(
                    f"S{start_index}",
                    source_type,
                    url,
                    "x" * (MAX_TOTAL_EXTRACTED_CHARS + 100),
                )
            ],
            [],
            start_index + 1,
        )

    monkeypatch.setattr("cap_931.web_research.research_site", fake_site)

    sources, warnings = research_companies(
        "https://example.com/", ["https://example.com"]
    )

    assert len(sources) == 1
    assert len(sources[0]["content"]) == MAX_TOTAL_EXTRACTED_CHARS
    assert sources[0]["content_truncated"]
    assert any("Duplicate company URL" in warning for warning in warnings)
