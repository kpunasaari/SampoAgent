"""Bounded adapters for explicitly enabled public feeds and official APIs.

These adapters never use browser fingerprinting, proxies, login sessions, or
anti-bot workarounds. Browser-only sources are rejected before any request.
"""

from __future__ import annotations

from datetime import date, datetime
from email.utils import parsedate_to_datetime
import http.client
from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import socket
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

from sampoagent.jobs.service import NormalizedJob, normalize_job


MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_TIMEOUT_SECONDS = 8.0
USER_AGENT = "SampoAgent/0.1 (+local job search; respects robots.txt)"
JOB_MARKET_FINLAND_ENDPOINT = "https://api.ahtp.fi/kipa/p67/v2/jobpostings"
JOB_MARKET_FINLAND_TEST_ENDPOINT = "https://api-qa.ahtp.fi/kipa/p67/v2/jobpostings"

FeedFetcher = Callable[[str, float, int], bytes]
ApiPoster = Callable[[str, dict[str, object], dict[str, str], float, int], bytes]


class SourceAdapterError(ValueError):
    """A source is unsupported, unsafe, blocked, or returned invalid data."""


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "noscript"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _PlainText()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return " ".join(value.split())
    return " ".join(" ".join(parser.parts).split())


def _source_value(source: Mapping[str, object], key: str, default: str = "") -> str:
    return str(source.get(key, default) or default).strip()


def _capability(source: Mapping[str, object]) -> str:
    return _source_value(source, "capability", "Browser search only").casefold()


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _resolve_public_addresses(url: str) -> tuple[str, int, tuple[str, ...]]:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise SourceAdapterError("Source URL is invalid.") from exc
    if parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password:
        raise SourceAdapterError("Source URL must be a credential-free HTTP(S) address.")
    if parsed.scheme != "https":
        raise SourceAdapterError("Source URL must use HTTPS.")
    if _is_loopback_host(host):
        raise SourceAdapterError("Loopback addresses are not allowed for remote sources.")
    try:
        resolved = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise SourceAdapterError("Source hostname could not be verified.") from exc
    addresses = tuple(dict.fromkeys(entry[4][0] for entry in resolved))
    if not addresses:
        raise SourceAdapterError("Source hostname did not resolve.")
    for address in addresses:
        try:
            parsed_ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise SourceAdapterError("Source resolved to an invalid network address.") from exc
        if not parsed_ip.is_global:
            raise SourceAdapterError("Private or reserved network destinations are not allowed.")
    return host, port or 443, addresses


def validate_remote_url(url: str) -> None:
    """Reject non-web, credential-bearing, private, and unresolved targets."""
    _resolve_public_addresses(url)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to a previously validated public IP address."""

    def __init__(self, host: str, pinned_ip: str, *args: Any, **kwargs: Any) -> None:
        super().__init__(host, *args, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_ip, self.port), timeout=self.timeout, source_address=self.source_address
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


class _SafeRedirects(HTTPRedirectHandler):
    def __init__(self, *, check_robots: bool = False, allowed_origin: str | None = None, timeout_seconds: float = 8.0, max_bytes: int = 2_000_000) -> None:
        super().__init__()
        self.check_robots = check_robots
        self.allowed_origin = allowed_origin
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def redirect_request(self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str) -> Request | None:
        validate_remote_url(newurl)
        parsed = urlsplit(newurl)
        if self.allowed_origin and f"{parsed.scheme}://{parsed.netloc}".casefold() != self.allowed_origin.casefold():
            raise SourceAdapterError("Authenticated API redirects must remain on the official API origin.")
        if self.check_robots:
            robots = _robots_parser(newurl, timeout_seconds=self.timeout_seconds, max_bytes=self.max_bytes)
            if not robots.can_fetch(USER_AGENT, newurl):
                raise SourceAdapterError("robots.txt disallows the redirected feed URL.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_response(request: Request, *, timeout_seconds: float, max_bytes: int, check_robots: bool = False, allowed_redirect_origin: str | None = None) -> bytes:
    redirect_policy = _SafeRedirects(check_robots=check_robots, allowed_origin=allowed_redirect_origin, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
    current = request
    for redirect_count in range(11):
        host, port, addresses = _resolve_public_addresses(current.full_url)
        connection = _PinnedHTTPSConnection(host, addresses[0], port=port, timeout=timeout_seconds, context=ssl.create_default_context())
        parsed = urlsplit(current.full_url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        headers = dict(current.header_items())
        headers.setdefault("Host", parsed.netloc)
        try:
            connection.request(current.get_method(), target, body=current.data, headers=headers)
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                response.read(max_bytes + 1)
                if not location or redirect_count == 10:
                    raise SourceAdapterError("Source returned too many or an invalid redirects.")
                next_url = urljoin(current.full_url, location)
                next_request = redirect_policy.redirect_request(current, response, response.status, response.reason, response.headers, next_url)
                if next_request is None:
                    raise SourceAdapterError("Source redirect was rejected.")
                current = next_request
                continue
            if response.status < 200 or response.status >= 300:
                raise SourceAdapterError(f"Source returned HTTP {response.status}.")
            data = response.read(max_bytes + 1)
        except SourceAdapterError:
            raise
        except (TimeoutError, OSError, http.client.HTTPException, ssl.SSLError) as exc:
            if isinstance(exc, (TimeoutError, socket.timeout)):
                raise SourceAdapterError("Source request timed out.") from exc
            raise SourceAdapterError("Source request failed or timed out.") from exc
        finally:
            connection.close()
        break
    if len(data) > max_bytes:
        raise SourceAdapterError("Source response exceeded the configured size limit.")
    return data


_robots_cache: dict[str, tuple[float, RobotFileParser]] = {}


def _robots_parser(url: str, *, timeout_seconds: float, max_bytes: int) -> RobotFileParser:
    parsed = urlsplit(url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    cached = _robots_cache.get(robots_url)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    validate_remote_url(robots_url)
    request = Request(robots_url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain"})
    try:
        payload = _read_response(request, timeout_seconds=timeout_seconds, max_bytes=min(max_bytes, 128_000))
    except SourceAdapterError as exc:
        if "HTTP 404" in str(exc):
            payload = b"User-agent: *\nAllow: /\n"
        else:
            raise SourceAdapterError("Could not verify robots.txt permission for this source.") from exc
    parser = RobotFileParser(robots_url)
    parser.parse(payload.decode("utf-8", errors="replace").splitlines())
    _robots_cache[robots_url] = (time.monotonic() + 900, parser)
    return parser


def _fetch_url(url: str, timeout_seconds: float, max_bytes: int, *, check_robots: bool) -> bytes:
    validate_remote_url(url)
    if check_robots:
        robots = _robots_parser(url, timeout_seconds=timeout_seconds, max_bytes=max_bytes)
        if not robots.can_fetch(USER_AGENT, url):
            raise SourceAdapterError("robots.txt disallows automated access to this feed.")
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/feed+json, application/json, application/atom+xml, application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.1"},
    )
    return _read_response(request, timeout_seconds=timeout_seconds, max_bytes=max_bytes, check_robots=check_robots)


def _text_child(element: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in element.iter():
        if child is element:
            continue
        if child.tag.rsplit("}", 1)[-1].casefold() in wanted and child.text:
            clean = _plain_text(child.text)
            if clean:
                return clean
    return ""


def _direct_text(element: ET.Element, *names: str) -> str:
    wanted = {name.casefold() for name in names}
    for child in list(element):
        if child.tag.rsplit("}", 1)[-1].casefold() in wanted and child.text:
            clean = _plain_text(child.text)
            if clean:
                return clean
    return ""


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    clean = value.strip()
    try:
        return datetime.fromisoformat(clean.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return parsedate_to_datetime(clean).date()
        except (TypeError, ValueError, OverflowError):
            try:
                return date.fromisoformat(clean[:10])
            except ValueError:
                return None


def _localized(value: object, preferred_language: str = "fi") -> str:
    if isinstance(value, str):
        return _plain_text(value)
    if isinstance(value, Mapping):
        for language in (preferred_language, "fi", "en", "sv"):
            text = value.get(language)
            if isinstance(text, str) and text.strip():
                return _plain_text(text)
        for text in value.values():
            if isinstance(text, str) and text.strip():
                return _plain_text(text)
    return ""


def _job_url(url: str) -> str:
    if not url:
        raise SourceAdapterError("Feed item is missing a job URL.")
    validate_remote_url(url)
    return url


def _build_job(
    *,
    title: str,
    company: str,
    location: str,
    description: str,
    application_url: str,
    source: Mapping[str, object],
    deadline: object = None,
    language: str | None = None,
) -> NormalizedJob | None:
    title = title.strip()
    description = description.strip()
    company = company.strip() or _source_value(source, "name", "Unknown employer")
    if not title or not application_url:
        return None
    safe_url = _job_url(application_url)
    job = normalize_job(
        title=title,
        company=company,
        location=location.strip(),
        description=description,
        application_url=safe_url,
    )
    source_id = source.get("id")
    try:
        source_number = int(source_id) if source_id is not None else None
    except (TypeError, ValueError):
        source_number = None
    # The existing dataclass remains backwards-compatible while adapters attach
    # provenance/deadline attributes used by the discovery persistence layer.
    return replace(
        job,
        language=language or job.language,
        source_id=source_number,
        source_name=_source_value(source, "name", "Job source"),
        source_url=_source_value(source, "url"),
        deadline=_parse_date(deadline),
    )


class RssAtomAdapter:
    def __init__(self, *, fetcher: FeedFetcher | None = None) -> None:
        self.fetcher = fetcher

    def search(
        self,
        source: Mapping[str, object],
        query: object | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> list[NormalizedJob]:
        del query
        capability = _capability(source)
        if "browser search only" in capability:
            raise SourceAdapterError("Source is browser search only and cannot be fetched automatically.")
        if not any(token in capability for token in ("rss", "atom", "feed")):
            raise SourceAdapterError("Source is not configured as an RSS/Atom feed.")
        url = _source_value(source, "url")
        validate_remote_url(url)
        try:
            payload = (
                self.fetcher(url, timeout_seconds, max_bytes)
                if self.fetcher
                else _fetch_url(url, timeout_seconds, max_bytes, check_robots=True)
            )
        except SourceAdapterError:
            raise
        except TimeoutError as exc:
            raise SourceAdapterError("Source request timed out.") from exc
        except Exception as exc:
            raise SourceAdapterError("Source request failed.") from exc
        if len(payload) > max_bytes:
            raise SourceAdapterError("Source response exceeded the configured size limit.")
        if b"<!doctype" in payload[:2048].lower() or b"<!entity" in payload[:2048].lower():
            raise SourceAdapterError("Feed XML declarations with custom entities are not accepted.")
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise SourceAdapterError("Feed is not valid RSS or Atom XML.") from exc
        root_kind = root.tag.rsplit("}", 1)[-1].casefold()
        if root_kind == "rss":
            entries = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1].casefold() == "item"]
        elif root_kind == "feed":
            entries = [element for element in list(root) if element.tag.rsplit("}", 1)[-1].casefold() == "entry"]
        else:
            raise SourceAdapterError("Feed is not valid RSS or Atom XML.")
        jobs: list[NormalizedJob] = []
        for entry in entries[:250]:
            title = _direct_text(entry, "title")
            link = ""
            for child in list(entry):
                if child.tag.rsplit("}", 1)[-1].casefold() == "link":
                    link = child.attrib.get("href", "") or _plain_text(child.text or "")
                    if child.attrib.get("rel", "alternate") == "alternate":
                        break
            if not link:
                link = _direct_text(entry, "link")
            description = _direct_text(entry, "description", "summary", "content", "encoded")
            company = _direct_text(entry, "company", "employer", "organization", "author", "creator")
            if not company:
                author = next((e for e in entry.iter() if e.tag.rsplit("}", 1)[-1].casefold() == "author"), None)
                if author is not None:
                    company = _direct_text(author, "name") or _plain_text(author.text or "")
            location = _direct_text(entry, "location", "workplace", "joblocation", "addresslocality")
            apply_url = _direct_text(entry, "application_url", "apply_url") or link
            deadline = _direct_text(entry, "deadline", "validthrough", "expires", "expirationdate")
            try:
                job = _build_job(
                    title=title,
                    company=company,
                    location=location,
                    description=description,
                    application_url=apply_url,
                    source=source,
                    deadline=deadline,
                )
            except SourceAdapterError:
                continue
            if job:
                jobs.append(job)
        return jobs


class JsonFeedAdapter:
    def __init__(self, *, fetcher: FeedFetcher | None = None) -> None:
        self.fetcher = fetcher

    def search(
        self,
        source: Mapping[str, object],
        query: object | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> list[NormalizedJob]:
        del query
        capability = _capability(source)
        if "browser search only" in capability:
            raise SourceAdapterError("Source is browser search only and cannot be fetched automatically.")
        if not any(token in capability for token in ("json", "feed")):
            raise SourceAdapterError("Source is not configured as a JSON Feed.")
        url = _source_value(source, "url")
        validate_remote_url(url)
        try:
            payload = self.fetcher(url, timeout_seconds, max_bytes) if self.fetcher else _fetch_url(url, timeout_seconds, max_bytes, check_robots=True)
        except SourceAdapterError:
            raise
        except TimeoutError as exc:
            raise SourceAdapterError("Source request timed out.") from exc
        except Exception as exc:
            raise SourceAdapterError("Source request failed.") from exc
        if len(payload) > max_bytes:
            raise SourceAdapterError("Source response exceeded the configured size limit.")
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SourceAdapterError("Source did not return valid JSON Feed data.") from exc
        if not isinstance(data, dict) or not str(data.get("version", "")).startswith("https://jsonfeed.org/version/"):
            raise SourceAdapterError("Source did not return valid JSON Feed data.")
        items = data.get("items")
        if not isinstance(items, list):
            raise SourceAdapterError("JSON Feed items must be a list.")
        jobs: list[NormalizedJob] = []
        for item in items[:250]:
            if not isinstance(item, dict):
                continue
            authors = item.get("authors") or []
            author = authors[0] if authors and isinstance(authors[0], dict) else item.get("author")
            company = str(author.get("name", "")) if isinstance(author, dict) else ""
            description = str(item.get("content_text") or item.get("summary") or item.get("content_html") or "")
            try:
                job = _build_job(
                    title=str(item.get("title", "")),
                    company=company,
                    location=str(item.get("location", "")),
                    description=_plain_text(description),
                    application_url=str(item.get("external_url") or item.get("url") or ""),
                    source=source,
                    deadline=item.get("expires") or item.get("valid_through"),
                    language=str(item.get("language", ""))[:2] or None,
                )
            except SourceAdapterError:
                continue
            if job:
                jobs.append(job)
        return jobs


def _selector_values(element: Any, selector: str) -> list[str]:
    selected = element.css(selector)
    values = selected.getall() if hasattr(selected, "getall") else list(selected)
    return [_plain_text(str(value)) for value in values if value is not None and str(value).strip()]


def _jsonld_jobpostings(values: list[str]) -> list[Mapping[str, object]]:
    postings: list[Mapping[str, object]] = []

    def visit(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            types = value.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if isinstance(types, list) and any(str(item).casefold() == "jobposting" for item in types):
                postings.append(value)
            if "@graph" in value:
                visit(value["@graph"])

    for raw in values:
        try:
            visit(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return postings


def _jsonld_location(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(filter(None, (_jsonld_location(item) for item in value)))
    if not isinstance(value, Mapping):
        return str(value or "").strip()
    address = value.get("address", value)
    if isinstance(address, Mapping):
        parts = [address.get(key) for key in ("addressLocality", "addressRegion", "addressCountry")]
        return ", ".join(str(part.get("name", "") if isinstance(part, Mapping) else part).strip() for part in parts if part)
    return str(address or "").strip()


class ScraplingAdapter:
    """Use Scrapling's static CSS parser on explicitly selected public HTML pages."""

    DEFAULT_CARD_SELECTOR = "article, li[class*='job'], div[class*='job-card'], div[class*='job-listing'], [data-job-id]"

    def __init__(self, *, fetcher: FeedFetcher | None = None, selector_factory: Callable[[str], Any] | None = None) -> None:
        self.fetcher = fetcher
        self.selector_factory = selector_factory

    def _selector(self, html: str) -> Any:
        if self.selector_factory:
            return self.selector_factory(html)
        try:
            from scrapling.parser import Selector
        except ImportError as exc:
            raise SourceAdapterError("Scrapling parser is not installed. Reinstall SampoAgent dependencies to enable this source.") from exc
        return Selector(html)

    def search(
        self,
        source: Mapping[str, object],
        query: object | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> list[NormalizedJob]:
        del query
        if "scrapling public page" not in _capability(source):
            raise SourceAdapterError("Source is not configured for the Scrapling public-page adapter.")
        url = _source_value(source, "url")
        validate_remote_url(url)
        try:
            payload = self.fetcher(url, timeout_seconds, max_bytes) if self.fetcher else _fetch_url(url, timeout_seconds, max_bytes, check_robots=True)
        except SourceAdapterError:
            raise
        except Exception as exc:
            raise SourceAdapterError("Public job page could not be fetched; access was not retried or bypassed.") from exc
        if len(payload) > max_bytes:
            raise SourceAdapterError("Scrapling page exceeded the configured size limit.")
        try:
            page = self._selector(payload.decode("utf-8", errors="replace"))
        except SourceAdapterError:
            raise
        except Exception as exc:
            raise SourceAdapterError("Scrapling could not parse the public job page.") from exc

        jobs: list[NormalizedJob] = []
        for item in _jsonld_jobpostings(_selector_values(page, 'script[type="application/ld+json"]::text'))[:250]:
            organization = item.get("hiringOrganization", {})
            company = organization.get("name", "") if isinstance(organization, Mapping) else str(organization or "")
            try:
                job = _build_job(
                    title=str(item.get("title", "")), company=str(company),
                    location=_jsonld_location(item.get("jobLocation", "")),
                    description=_plain_text(str(item.get("description", ""))),
                    application_url=urljoin(url, str(item.get("url") or item.get("applicationUrl") or "")),
                    source=source, deadline=item.get("validThrough"),
                )
            except SourceAdapterError:
                continue
            if job:
                jobs.append(job)
        if jobs:
            return jobs

        card_selector = _source_value(source, "listing_selector") or self.DEFAULT_CARD_SELECTOR
        cards = page.css(card_selector)
        for card in list(cards)[:250]:
            try:
                title = " ".join(_selector_values(card, "h1::text, h2::text, h3::text, [class*='title']::text"))
                links = _selector_values(card, "a::attr(href)")
                apply_url = urljoin(url, links[0]) if links else ""
                company = " ".join(_selector_values(card, "[class*='company']::text, [class*='employer']::text"))
                location = " ".join(_selector_values(card, "[class*='location']::text"))
                description = " ".join(_selector_values(card, "[class*='description']::text, p::text"))
                deadline = " ".join(_selector_values(card, "[class*='deadline']::text, time::attr(datetime)"))
                job = _build_job(title=title, company=company, location=location, description=description, application_url=apply_url, source=source, deadline=deadline)
            except SourceAdapterError:
                continue
            if job:
                jobs.append(job)
        return jobs


class JobMarketFinlandAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = JOB_MARKET_FINLAND_ENDPOINT,
        post: ApiPoster | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.endpoint = endpoint
        self.post = post

    @classmethod
    def from_environment(cls) -> "JobMarketFinlandAdapter":
        api_key = os.environ.get("KIPA_SUBSCRIPTION_KEY", "").strip()
        if not api_key:
            raise SourceAdapterError("Job Market Finland API requires KEHA Centre activation and an issued KIPA key.")
        endpoint = os.environ.get("JOBMARKETFINLAND_API_URL", JOB_MARKET_FINLAND_ENDPOINT).strip()
        allowed = {JOB_MARKET_FINLAND_ENDPOINT, JOB_MARKET_FINLAND_TEST_ENDPOINT}
        if endpoint not in allowed:
            raise SourceAdapterError("Job Market Finland endpoint must be the official KEHA Centre endpoint.")
        return cls(api_key=api_key, endpoint=endpoint)

    def _send(self, payload: dict[str, object], timeout_seconds: float, max_bytes: int) -> bytes:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson",
            "KIPA-Subscription-Key": self.api_key,
            "User-Agent": USER_AGENT,
        }
        if self.post:
            return self.post(self.endpoint, payload, headers, timeout_seconds, max_bytes)
        validate_remote_url(self.endpoint)
        request = Request(self.endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        endpoint_parts = urlsplit(self.endpoint)
        return _read_response(request, timeout_seconds=timeout_seconds, max_bytes=max_bytes, allowed_redirect_origin=f"{endpoint_parts.scheme}://{endpoint_parts.netloc}")

    def search(
        self,
        source: Mapping[str, object],
        query: object | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> list[NormalizedJob]:
        del query
        if "job market finland api" not in _capability(source):
            raise SourceAdapterError("Source is not configured for the official Job Market Finland API.")
        if not self.api_key:
            raise SourceAdapterError("Job Market Finland API requires KEHA Centre activation and an issued KIPA key.")
        source_url = _source_value(source, "url")
        parsed = urlsplit(source_url)
        if parsed.hostname != "tyomarkkinatori.fi":
            raise SourceAdapterError("Job Market Finland API source must be tyomarkkinatori.fi.")
        validate_remote_url(self.endpoint)
        payload: dict[str, object] = {"onlyStatus": "PUBLISHED"}
        try:
            body = self._send(payload, timeout_seconds, max_bytes)
        except SourceAdapterError:
            raise
        except TimeoutError as exc:
            raise SourceAdapterError("Job Market Finland API request timed out.") from exc
        except Exception as exc:
            raise SourceAdapterError("Job Market Finland API request failed.") from exc
        if len(body) > max_bytes:
            raise SourceAdapterError("Job Market Finland API response exceeded the configured size limit.")
        jobs: list[NormalizedJob] = []
        for line in body.splitlines()[:1000]:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise SourceAdapterError("Job Market Finland API returned malformed NDJSON.") from exc
            if not isinstance(item, dict):
                continue
            position = item.get("position") if isinstance(item.get("position"), dict) else {}
            application = item.get("application") if isinstance(item.get("application"), dict) else {}
            client = item.get("client") if isinstance(item.get("client"), dict) else {}
            location_info = item.get("location") if isinstance(item.get("location"), dict) else {}
            languages = item.get("languages")
            language = "fi"
            if isinstance(languages, list) and languages:
                language = next((str(value) for value in languages if value in {"fi", "en", "sv"}), str(languages[0]))
            preferred_language = language if language in {"fi", "en", "sv"} else "fi"
            title = _localized(position.get("title"), preferred_language)
            description = _localized(position.get("jobDescription"), preferred_language)
            apply_url = _localized(application.get("url"), preferred_language)
            if not apply_url:
                links = item.get("externalLinks")
                if isinstance(links, list):
                    apply_url = next((str(link.get("url", "")) for link in links if isinstance(link, dict) and link.get("url")), "")
            job = _build_job(
                title=title,
                company=str(client.get("company", "")),
                location=str(location_info.get("workplacePostOffice", "")),
                description=description,
                application_url=apply_url,
                source=source,
                deadline=application.get("expires"),
                language=language,
            )
            if job:
                jobs.append(job)
        return jobs
