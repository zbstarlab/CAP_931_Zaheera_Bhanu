"""Bounded public-source discovery and retrieval for CAP 931."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urljoin,
    urlsplit,
    urlunsplit,
)
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .validators import ValidationError, validate_public_url

USER_AGENT = "CAP931ResearchPrototype/1.0"
MAX_HTML_BYTES = 3_000_000
MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_ROBOTS_BYTES = 200_000
MAX_TEXT_CHARS = 8_000
MAX_PDF_TEXT_CHARS = 12_000
MAX_PDF_PAGES = 20
MAX_REDIRECTS = 3
MAX_PAGES_PER_SITE = 4
MAX_FETCH_ATTEMPTS_PER_SITE = 8
MAX_TOTAL_SOURCES = 12
MAX_TOTAL_EXTRACTED_CHARS = 72_000

logger = logging.getLogger(__name__)

REDIRECT_STATUSES = {301, 302, 303, 307, 308}
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
BLOCK_MARKERS = (
    "your request has been blocked",
    "your current user-agent string appears to be from an automated process",
    "verify you are human",
    "access denied",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "checking your browser",
    "complete the security check",
    "unusual traffic from your computer network",
    "automated queries",
    "sign in to continue",
    "subscribe to continue reading",
)
JAVASCRIPT_REQUIRED_MARKERS = (
    "enable javascript to continue",
    "enable javascript to view",
    "javascript is required",
    "javascript required",
    "requires javascript",
)
SENSITIVE_QUERY_TERMS = (
    "api_key",
    "apikey",
    "auth",
    "credential",
    "key",
    "password",
    "secret",
    "signature",
    "token",
)
LINK_KEYWORDS = {
    "leadership": 10,
    "executive": 9,
    "management": 8,
    "annual report": 10,
    "annual-report": 10,
    "investor": 9,
    "earnings": 8,
    "financial": 7,
    "press release": 9,
    "press-release": 9,
    "newsroom": 8,
    "news": 6,
    "strategy": 9,
    "initiative": 7,
    "transformation": 7,
    "innovation": 6,
    "technology": 6,
    "product": 5,
    "platform": 5,
    "career": 5,
    "jobs": 5,
    "about": 4,
}
EXCLUDED_LINK_TERMS = (
    "cookie",
    "privacy",
    "terms-of-use",
    "terms-of-service",
    "accessibility",
    "sign-in",
    "signin",
    "login",
    "cart",
)
NON_CONTENT_EXTENSIONS = {
    ".css",
    ".csv",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".mp3",
    ".mp4",
    ".png",
    ".svg",
    ".webp",
    ".xml",
    ".zip",
}


class ResearchError(Exception):
    """Raised when a public source cannot be safely retrieved."""


@dataclass(frozen=True)
class FetchResult:
    """Raw bounded HTTP response after manual redirect validation."""

    url: str
    status_code: int
    content_type: str
    encoding: str
    data: bytes


@dataclass(frozen=True)
class HtmlExtraction:
    """Extracted HTML content plus bounded diagnostics for failure handling."""

    title: str
    content: str
    publication_date: str | None
    diagnostic_text: str
    likely_javascript_only: bool


def looks_blocked(title: str, text: str) -> bool:
    """Detect access-denial, challenge, login, and paywall pages."""
    combined = (title + " " + text[:2500]).lower()
    return any(marker in combined for marker in BLOCK_MARKERS)


def _safe_url_for_message(url: str) -> str:
    """Return a bounded URL for logs and warnings with sensitive values hidden."""
    canonical = canonicalize_url(url)
    if not canonical:
        return "[invalid URL]"
    parts = urlsplit(canonical)
    query = urlencode(
        [
            (
                key,
                "[redacted]"
                if any(term in key.lower() for term in SENSITIVE_QUERY_TERMS)
                else value,
            )
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ],
        doseq=True,
    )
    safe = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    return safe[:500]


def _warning_for_url(url: str, message: str) -> str:
    """Attach the failed source URL unless the message already identifies it."""
    if "Source: " in message:
        return message
    return f"{message} Source: {_safe_url_for_message(url)}"


def _append_warning(warnings: list[str], warning: str) -> None:
    """Keep warning order while suppressing exact duplicates."""
    if warning not in warnings:
        warnings.append(warning)


def canonicalize_url(url: str) -> str:
    """Normalize a URL for deterministic scoring and request deduplication."""
    try:
        parts = urlsplit(url.strip())
        hostname = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except (AttributeError, ValueError):
        return ""
    if parts.scheme.lower() not in {"http", "https"} or not hostname:
        return ""
    if parts.username or parts.password:
        return ""

    default_port = (parts.scheme.lower() == "http" and port == 80) or (
        parts.scheme.lower() == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    # A non-root trailing slash can change both server routing and the meaning of
    # a relative redirect, so it is intentionally preserved.
    path = parts.path or "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in TRACKING_QUERY_KEYS
        ),
        doseq=True,
    )
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def approved_hostname(url: str) -> str:
    """Return the exact approved host boundary, treating www as the same host."""
    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    return hostname.removeprefix("www.")


def is_approved_domain(url: str, approved_host: str) -> bool:
    """Allow an approved company host and its subdomains, never sibling domains."""
    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    hostname = hostname.removeprefix("www.")
    return hostname == approved_host or hostname.endswith(f".{approved_host}")


def _normalize_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _normalize_publication_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?!\d)", value)
    if not match:
        return None
    candidate = match.group(0)
    try:
        return datetime.strptime(candidate, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _find_json_date(value: object) -> str | None:
    if isinstance(value, dict):
        raw_types = value.get("@type", [])
        types = {raw_types} if isinstance(raw_types, str) else set(raw_types)
        article_types = {"Article", "BlogPosting", "NewsArticle", "PressRelease"}
        published = value.get("datePublished")
        if types & article_types and isinstance(published, str):
            normalized = _normalize_publication_date(published)
            if normalized:
                return normalized
        for item in value.values():
            found = _find_json_date(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_json_date(item)
            if found:
                return found
    return None


def _extract_publication_date(soup: BeautifulSoup) -> str | None:
    reliable_names = {
        "article:published_time",
        "datepublished",
        "publication_date",
        "publishdate",
    }
    for meta in soup.find_all("meta"):
        key = str(
            meta.get("property") or meta.get("name") or meta.get("itemprop") or ""
        ).lower()
        if key in reliable_names:
            normalized = _normalize_publication_date(meta.get("content"))
            if normalized:
                return normalized
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            found = _find_json_date(json.loads(script.get_text()))
        except (json.JSONDecodeError, TypeError):
            continue
        if found:
            return found
    return None


def _node_text(node: object, *, remove_page_chrome: bool) -> str:
    """Extract text from a copied node without mutating other candidates."""
    copy = BeautifulSoup(str(node), "html.parser")
    tags = ["script", "style", "template", "svg", "form", "noscript"]
    if remove_page_chrome:
        tags.extend(["nav", "footer", "header", "aside"])
    for tag in copy(tags):
        tag.decompose()
    return _normalize_text(copy.get_text("\n", strip=True))


def _extract_html_document(html: str) -> HtmlExtraction:
    """Extract readable static text while retaining safe failure diagnostics."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "Untitled page"
    publication_date = _extract_publication_date(soup)

    diagnostic_copy = BeautifulSoup(html, "html.parser")
    for tag in diagnostic_copy(["script", "style", "template", "svg", "form"]):
        tag.decompose()
    diagnostic_node = diagnostic_copy.body or diagnostic_copy
    diagnostic_text = _normalize_text(
        diagnostic_node.get_text("\n", strip=True)
    )

    content = ""
    candidates = [
        soup.find("main"),
        soup.find("article"),
        soup.find(attrs={"role": "main"}),
    ]
    seen_nodes: set[int] = set()
    for node in candidates:
        if node is None or id(node) in seen_nodes:
            continue
        seen_nodes.add(id(node))
        content = _node_text(node, remove_page_chrome=True)
        if content:
            break

    if not content:
        body = soup.body or soup
        content = _node_text(body, remove_page_chrome=True)
        if not content:
            content = _node_text(body, remove_page_chrome=False)

    if not content:
        description = soup.find(
            "meta",
            attrs={"name": lambda value: value and value.lower() == "description"},
        )
        if description:
            content = _normalize_text(str(description.get("content") or ""))

    diagnostic_lower = diagnostic_text.lower()
    likely_javascript_only = not content and (
        bool(soup.find("script"))
        or any(marker in diagnostic_lower for marker in JAVASCRIPT_REQUIRED_MARKERS)
    )
    return HtmlExtraction(
        title=unescape(title)[:200],
        content=unescape(content)[:MAX_TEXT_CHARS],
        publication_date=publication_date,
        diagnostic_text=unescape(diagnostic_text)[:2500],
        likely_javascript_only=likely_javascript_only,
    )


def _extract_html_details(html: str) -> tuple[str, str, str | None]:
    details = _extract_html_document(html)
    return details.title, details.content, details.publication_date


def extract_page(html: str) -> tuple[str, str]:
    """Preserve the original title/text extraction contract."""
    title, text, _ = _extract_html_details(html)
    return title, text


def score_link(url: str, label: str = "") -> int:
    """Score likely research value deterministically from URL and link text."""
    combined = unquote(f"{url} {label}").lower().replace("_", " ")
    if any(term in combined for term in EXCLUDED_LINK_TERMS):
        return 0
    path_suffix = PurePosixPath(urlsplit(url).path.lower()).suffix
    if path_suffix in NON_CONTENT_EXTENSIONS:
        return 0
    score = sum(weight for keyword, weight in LINK_KEYWORDS.items() if keyword in combined)
    if path_suffix == ".pdf":
        score += 4
    return score


def discover_relevant_links(
    html: str,
    base_url: str,
    approved_host: str | None = None,
    limit: int = 30,
) -> list[dict]:
    """Return scored, same-company links in deterministic priority order."""
    host = approved_host or approved_hostname(base_url)
    base = canonicalize_url(base_url)
    candidates: dict[str, dict] = {}
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        url = canonicalize_url(urljoin(base_url, str(anchor["href"])))
        if not url or url == base or not is_approved_domain(url, host):
            continue
        label = " ".join(anchor.get_text(" ", strip=True).split())[:200]
        score = score_link(url, label)
        if score <= 0:
            continue
        existing = candidates.get(url)
        if existing is None or score > existing["score"]:
            candidates[url] = {"url": url, "score": score, "label": label}
    return sorted(candidates.values(), key=lambda item: (-item["score"], item["url"]))[
        :limit
    ]


def _response_limit(content_type: str, override: int | None) -> int:
    if override is not None:
        return override
    if "application/pdf" in content_type:
        return MAX_PDF_BYTES
    return MAX_HTML_BYTES


def _fetch_bytes(
    session: requests.Session,
    url: str,
    allowed_host: str,
    *,
    accepted_statuses: set[int] | None = None,
    max_bytes: int | None = None,
) -> FetchResult:
    """Fetch bytes with validation before every request and every redirect."""
    current = canonicalize_url(url)
    accepted = accepted_statuses or set()
    visited: set[str] = set()
    redirect_count = 0
    while True:
        if not current or not is_approved_domain(current, allowed_host):
            raise ResearchError(
                "A URL left the approved company domain. "
                f"Source: {_safe_url_for_message(current or url)}"
            )
        if current in visited:
            raise ResearchError(
                "Redirect loop detected; a canonical URL repeated. "
                f"Source: {_safe_url_for_message(current)}"
            )
        visited.add(current)
        try:
            current = validate_public_url(current)
            with session.get(
                current,
                timeout=(5, 10),
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise ResearchError(
                            "Redirect response did not include a destination. "
                            f"Source: {_safe_url_for_message(current)}"
                        )
                    destination = canonicalize_url(urljoin(current, location))
                    if not destination:
                        raise ResearchError(
                            "Redirect response included an invalid destination. "
                            f"Source: {_safe_url_for_message(current)}"
                        )
                    logger.debug(
                        "Public-source redirect %d/%d: %s -> %s",
                        redirect_count + 1,
                        MAX_REDIRECTS,
                        _safe_url_for_message(current),
                        _safe_url_for_message(destination),
                    )
                    if destination in visited:
                        raise ResearchError(
                            "Redirect loop detected; a canonical URL repeated. "
                            f"Source: {_safe_url_for_message(destination)}"
                        )
                    if redirect_count >= MAX_REDIRECTS:
                        raise ResearchError(
                            f"Website exceeded the {MAX_REDIRECTS}-redirect limit. "
                            f"Source: {_safe_url_for_message(current)}"
                        )
                    redirect_count += 1
                    current = destination
                    continue
                if response.status_code not in accepted and response.status_code >= 400:
                    raise ResearchError(
                        f"Could not retrieve {_safe_url_for_message(current)}: "
                        f"HTTP {response.status_code}."
                    )
                if response.status_code in accepted and response.status_code >= 400:
                    return FetchResult(current, response.status_code, "", "utf-8", b"")

                content_type = response.headers.get("Content-Type", "").lower()
                limit = _response_limit(content_type, max_bytes)
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > limit:
                    raise ResearchError(
                        f"Response exceeded the {limit:,}-byte limit. "
                        f"Source: {_safe_url_for_message(current)}"
                    )
                chunks = []
                size = 0
                for chunk in response.iter_content(chunk_size=8192):
                    size += len(chunk)
                    if size > limit:
                        raise ResearchError(
                            f"Response exceeded the {limit:,}-byte limit. "
                            f"Source: {_safe_url_for_message(current)}"
                        )
                    chunks.append(chunk)
                return FetchResult(
                    url=current,
                    status_code=response.status_code,
                    content_type=content_type,
                    encoding=response.encoding or "utf-8",
                    data=b"".join(chunks),
                )
        except ResearchError:
            raise
        except requests.Timeout as exc:
            raise ResearchError(
                f"Timed out while retrieving {_safe_url_for_message(current)}."
            ) from exc
        except requests.RequestException as exc:
            raise ResearchError(
                f"Could not retrieve {_safe_url_for_message(current)}: "
                f"{type(exc).__name__}."
            ) from exc
        except ValidationError as exc:
            raise ResearchError(
                f"Redirect destination was not a safe public URL: {exc} "
                f"Source: {_safe_url_for_message(current)}"
            ) from exc
        except (UnicodeError, ValueError) as exc:
            raise ResearchError(
                "The source returned invalid response metadata. "
                f"Source: {_safe_url_for_message(current)}"
            ) from exc


def extract_public_pdf(
    data: bytes,
    url: str,
    *,
    max_pages: int = MAX_PDF_PAGES,
) -> dict:
    """Extract bounded text and metadata from a retrieved public PDF."""
    if len(data) > MAX_PDF_BYTES:
        raise ResearchError("Public PDF exceeded the 5 MB limit.")
    if b"%PDF-" not in data[:1024]:
        raise ResearchError("Public PDF did not have a valid PDF signature.")
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ResearchError("Encrypted public PDFs are not supported.")
        page_count = len(reader.pages)
        chunks = []
        extracted_chars = 0
        for page in reader.pages[:max_pages]:
            text = page.extract_text() or ""
            chunks.append(text)
            extracted_chars += len(text)
            if extracted_chars > MAX_PDF_TEXT_CHARS:
                break
        metadata_title = reader.metadata.title if reader.metadata else None
    except ResearchError:
        raise
    except (PdfReadError, OSError, TypeError, ValueError) as exc:
        raise ResearchError("Public PDF was malformed or unreadable.") from exc
    except Exception as exc:
        raise ResearchError("Public PDF text could not be extracted.") from exc

    text = _normalize_text("\n".join(chunks))
    if not text:
        raise ResearchError(
            "Public PDF had no extractable text; image-only PDFs require OCR."
        )
    fallback = unquote(PurePosixPath(urlsplit(url).path).name) or "Public PDF"
    return {
        "title": str(metadata_title or fallback)[:200],
        "content": text[:MAX_PDF_TEXT_CHARS],
        "page_count": page_count,
        "content_truncated": (
            page_count > max_pages or len(text) > MAX_PDF_TEXT_CHARS
        ),
    }


def _retrieve_source(
    session: requests.Session,
    url: str,
    source_id: str,
    source_type: str,
    allowed_host: str,
    relevance_score: int,
) -> tuple[dict, list[dict]]:
    fetched = _fetch_bytes(session, url, allowed_host)
    path_is_pdf = urlsplit(fetched.url).path.lower().endswith(".pdf")
    is_pdf = "application/pdf" in fetched.content_type
    is_html = any(
        media_type in fetched.content_type
        for media_type in ("text/html", "application/xhtml+xml")
    )
    if path_is_pdf and not is_pdf:
        raise ResearchError(
            "PDF URL did not return an application/pdf content type. "
            f"Source: {_safe_url_for_message(fetched.url)}"
        )

    links: list[dict] = []
    if is_pdf:
        pdf = extract_public_pdf(fetched.data, fetched.url)
        title = pdf["title"]
        content = pdf["content"]
        publication_date = None
        content_type = "pdf"
        extra = {
            "page_count": pdf["page_count"],
            "content_truncated": pdf["content_truncated"],
        }
    elif is_html:
        if len(fetched.data) > MAX_HTML_BYTES:
            raise ResearchError(
                f"HTML response exceeded the {MAX_HTML_BYTES:,}-byte limit. "
                f"Source: {_safe_url_for_message(fetched.url)}"
            )
        html = fetched.data.decode(fetched.encoding, errors="replace")
        details = _extract_html_document(html)
        title = details.title
        content = details.content
        publication_date = details.publication_date
        if looks_blocked(title, details.diagnostic_text):
            raise ResearchError(
                "Website access was blocked. "
                f"Source: {_safe_url_for_message(fetched.url)}"
            )
        if not content.strip():
            if details.likely_javascript_only:
                raise ResearchError(
                    "No readable static HTML was found; the page appears to require "
                    "JavaScript. "
                    f"Source: {_safe_url_for_message(fetched.url)}"
                )
            raise ResearchError(
                "No readable content was found on this source. "
                f"Source: {_safe_url_for_message(fetched.url)}"
            )
        links = discover_relevant_links(html, fetched.url, allowed_host)
        content_type = "html"
        extra = {"content_truncated": len(content) >= MAX_TEXT_CHARS}
    else:
        raise ResearchError(
            "Source did not return supported HTML or PDF content. "
            f"Source: {_safe_url_for_message(fetched.url)}"
        )
    source = {
        "source_id": source_id,
        "url": fetched.url,
        "title": title,
        "retrieved_at": datetime.now(UTC).isoformat(),
        "source_type": source_type,
        "content_type": content_type,
        "publication_date": publication_date,
        "relevance_score": relevance_score,
        "content": content,
        **extra,
    }
    if canonicalize_url(url) != fetched.url:
        source["requested_url"] = canonicalize_url(url)
    return source, links


def _load_robots(
    session: requests.Session,
    site_url: str,
    allowed_host: str,
) -> tuple[RobotFileParser | None, str | None]:
    parts = urlsplit(site_url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    try:
        fetched = _fetch_bytes(
            session,
            robots_url,
            allowed_host,
            accepted_statuses={404},
            max_bytes=MAX_ROBOTS_BYTES,
        )
    except ResearchError as exc:
        return (
            None,
            (
                "Could not verify robots.txt; link discovery was skipped: "
                f"{_warning_for_url(robots_url, str(exc))}"
            ),
        )

    parser = RobotFileParser()
    parser.set_url(robots_url)
    if fetched.status_code == 404:
        parser.parse([])
        return parser, None
    text = fetched.data.decode(fetched.encoding, errors="replace")
    title, extracted = extract_page(text) if "<html" in text[:500].lower() else ("", text)
    if looks_blocked(title, extracted):
        return (
            None,
            (
                "robots.txt returned an access-block page; link discovery was skipped. "
                f"Source: {_safe_url_for_message(robots_url)}"
            ),
        )
    parser.parse(text.splitlines())
    return parser, None


def research_website(
    url: str,
    source_id: str = "S1",
    source_type: str = "prospect",
) -> dict:
    """Retrieve one public HTML or PDF source without performing discovery."""
    host = approved_hostname(url)
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": USER_AGENT})
    try:
        source, _ = _retrieve_source(session, url, source_id, source_type, host, 0)
        return source
    finally:
        session.close()


def _next_candidate(candidates: dict[str, dict]) -> dict:
    return min(candidates.values(), key=lambda item: (-item["score"], item["url"]))


def research_site(
    site_url: str,
    source_type: str,
    start_index: int,
    *,
    max_pages: int = MAX_PAGES_PER_SITE,
    max_attempts: int = MAX_FETCH_ATTEMPTS_PER_SITE,
) -> tuple[list[dict], list[str], int]:
    """Retrieve a homepage and a bounded set of relevant same-company sources."""
    sources: list[dict] = []
    warnings: list[str] = []
    next_index = start_index
    host = approved_hostname(site_url)
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": USER_AGENT})
    attempted = {canonicalize_url(site_url)}
    retrieved_urls: set[str] = set()

    try:
        robots, robots_warning = _load_robots(session, site_url, host)
        if robots_warning:
            _append_warning(warnings, robots_warning)

        homepage_id = f"S{next_index}"
        next_index += 1
        if robots is not None and not robots.can_fetch(USER_AGENT, site_url):
            _append_warning(
                warnings,
                f"robots.txt disallows retrieval of {_safe_url_for_message(site_url)}",
            )
            return sources, warnings, next_index
        try:
            homepage, discovered = _retrieve_source(
                session,
                site_url,
                homepage_id,
                source_type,
                host,
                1,
            )
        except ResearchError as exc:
            _append_warning(warnings, _warning_for_url(site_url, str(exc)))
            return sources, warnings, next_index
        sources.append(homepage)
        homepage_url = canonicalize_url(homepage["url"])
        attempted.add(homepage_url)
        retrieved_urls.add(homepage_url)

        if robots is None:
            return sources, warnings, next_index

        candidates = {
            item["url"]: item for item in discovered if item["url"] not in attempted
        }
        attempts = 1
        while candidates and len(sources) < max_pages and attempts < max_attempts:
            candidate = _next_candidate(candidates)
            candidates.pop(candidate["url"], None)
            url = candidate["url"]
            if url in attempted:
                continue
            attempted.add(url)
            source_id = f"S{next_index}"
            next_index += 1
            attempts += 1
            if not robots.can_fetch(USER_AGENT, url):
                _append_warning(
                    warnings,
                    f"robots.txt disallows retrieval of {_safe_url_for_message(url)}",
                )
                continue
            try:
                source, new_links = _retrieve_source(
                    session,
                    url,
                    source_id,
                    source_type,
                    host,
                    candidate["score"],
                )
            except ResearchError as exc:
                _append_warning(warnings, _warning_for_url(url, str(exc)))
                continue
            final_url = canonicalize_url(source["url"])
            attempted.add(final_url)
            if final_url in retrieved_urls:
                _append_warning(
                    warnings,
                    "Duplicate redirected source was skipped: "
                    f"{_safe_url_for_message(final_url)}",
                )
                continue
            retrieved_urls.add(final_url)
            sources.append(source)
            for item in new_links:
                if item["url"] in attempted:
                    continue
                existing = candidates.get(item["url"])
                if existing is None or item["score"] > existing["score"]:
                    candidates[item["url"]] = item
        return sources, warnings, next_index
    finally:
        session.close()


def research_companies(
    company_url: str,
    competitors: list[str],
    *,
    max_pages_per_site: int = MAX_PAGES_PER_SITE,
    max_total_sources: int = MAX_TOTAL_SOURCES,
) -> tuple[list[dict], list[str]]:
    """Discover prospect and competitor sources with fair, global limits."""
    sources: list[dict] = []
    warnings: list[str] = []
    sites = [(company_url, "prospect"), *((url, "competitor") for url in competitors)]
    unique_sites = []
    seen_sites = set()
    for url, source_type in sites:
        canonical = canonicalize_url(url)
        if canonical in seen_sites:
            _append_warning(
                warnings,
                f"Duplicate company URL was skipped: {_safe_url_for_message(url)}",
            )
            continue
        seen_sites.add(canonical)
        unique_sites.append((url, source_type))

    if not unique_sites:
        return sources, warnings
    fair_page_limit = min(
        max_pages_per_site,
        max(1, max_total_sources // len(unique_sites)),
    )
    next_index = 1
    total_characters = 0
    for site_url, source_type in unique_sites:
        remaining_slots = max_total_sources - len(sources)
        if remaining_slots <= 0:
            _append_warning(warnings, "The total public-source limit was reached.")
            break
        site_sources, site_warnings, next_index = research_site(
            site_url,
            source_type,
            next_index,
            max_pages=min(fair_page_limit, remaining_slots),
        )
        for warning in site_warnings:
            _append_warning(warnings, warning)
        for source in site_sources:
            remaining_characters = MAX_TOTAL_EXTRACTED_CHARS - total_characters
            if remaining_characters <= 0:
                _append_warning(
                    warnings, "The total extracted-text limit was reached."
                )
                return sources, warnings
            if len(source["content"]) > remaining_characters:
                source = {
                    **source,
                    "content": source["content"][:remaining_characters],
                    "content_truncated": True,
                }
            sources.append(source)
            total_characters += len(source["content"])
    return sources, warnings
