#!/usr/bin/env python3
"""Generate Atom feeds for Gujarat Samachar magazines and Drishti IAS Hindi columns."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Comment, Tag

VERBOSE = False

USER_AGENT = (
    "Mozilla/5.0 (compatible; gs-magazine-rss/1.1; +https://github.com/monk-blade/gs-magazine-rss) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

GS_BASE = "https://www.gujaratsamachar.com"
DRISHTI_BASE = "https://www.drishtiias.com"
DRISHTI_INDEX = (
    "https://www.drishtiias.com/hindi/current-affairs-news-analysis-editorials"
)
BUSINESS_STANDARD_OPINION = "https://www.business-standard.com/opinion"
BUSINESS_STANDARD_BASE = "https://www.business-standard.com"
INDIAN_EXPRESS_EXPLAINED = "https://indianexpress.com/section/explained/"
INDIAN_EXPRESS_BASE = "https://indianexpress.com"

CLEANUP_SELECTORS = [
    ".social-share-wrapper",
    ".share-popup-wrapper",
    ".mostread-item",
    ".widget-card-container",
    ".ads",
    ".desktop-full-ad",
    ".adsbox970x90",
    "[id^=taboola]",
    "script",
    "style",
    "ev-engagement",
    ".btn-group",
    ".next-post",
    ".prev",
    ".breadcrumb",
    ".sharethis-inline-share-buttons",
    ".addtoany_share_save_container",
    "ul.actions",
    ".actions",
    "a.switch_to",
]

CONTENT_TAGS = {
    "a",
    "b",
    "blockquote",
    "br",
    "code",
    "em",
    "figcaption",
    "figure",
    "h2",
    "h3",
    "h4",
    "hr",
    "i",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "s",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

NOISE_TEXT = {
    "advertisement",
    "listen to this article",
    "read more",
    "share this article",
    "story continues below this ad",
    "subscribe to our newsletter",
}


def debug(message: str) -> None:
    if VERBOSE:
        print(message)


@dataclass
class Article:
    url: str
    title: str
    content_html: str
    published: datetime | None = None
    author: str | None = None


@dataclass(frozen=True)
class FeedSpec:
    slug: str
    title: str
    alternate_url: str
    author_name: str
    collect: Callable[..., list[Article]]


class BrowserFetcher:
    """Reusable headless Chromium fetcher for sites that need a real browser."""

    def __enter__(self) -> "BrowserFetcher":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for Business Standard and Indian Express feeds"
            ) from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="en-IN",
            viewport={"width": 1365, "height": 900},
            extra_http_headers={"Accept-Language": "en-IN,en;q=0.9"},
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._context.close()
        self._browser.close()
        self._playwright.stop()

    def fetch(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            page = self._context.new_page()
            try:
                response = page.goto(
                    encode_url(url), wait_until="domcontentloaded", timeout=45_000
                )
                if response and response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")
                # Business Standard can populate the article list shortly after
                # DOMContentLoaded, especially on a fresh GitHub Actions runner.
                page.wait_for_timeout(2_000)
                content = page.content()
                if len(content) < 500:
                    raise RuntimeError("empty or incomplete HTML response")
                return content
            except Exception as exc:
                last_error = exc
                debug(f"  browser retry {attempt}/3 for {url}: {exc}")
                if attempt < 3:
                    time.sleep(attempt)
            finally:
                page.close()
        raise RuntimeError(f"Browser failed to fetch {url}: {last_error}") from last_error


def fetch(url: str, retries: int = 3, pause: float = 1.0) -> str:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(
                encode_url(url),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "hi,gu,en;q=0.8",
                },
            )
            with urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_err = exc
            time.sleep(pause * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def encode_url(url: str) -> str:
    """Percent-encode Unicode URL components before passing them to urllib."""
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@-._~!$&'()*+,;=")
    query = quote(parts.query, safe="=&/%:@-._~!$'()*+,;?[]")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def absolutize(base: str, href: str) -> str:
    return urljoin(base, href)


def normalize_url(url: str) -> str:
    """Canonicalize URL for deduplication (scheme/host case, trailing slash, tracking params)."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    # Drop common tracking / fragment noise
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    query = urlencode(query_pairs)
    return encode_url(urlunsplit((scheme, netloc, path, query, "")))


def normalize_title(title: str) -> str:
    t = re.sub(r"\s+", " ", title).strip().casefold()
    return t


def dedupe_articles(articles: list[Article], limit: int | None = None) -> list[Article]:
    """Keep first occurrence by URL, then by title; optionally cap length."""
    out: list[Article] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    dropped = 0
    hit_limit = False
    for art in articles:
        key_url = normalize_url(art.url)
        key_title = normalize_title(art.title)
        if key_url in seen_urls or (key_title and key_title in seen_titles):
            dropped += 1
            continue
        seen_urls.add(key_url)
        if key_title:
            seen_titles.add(key_title)
        out.append(art)
        if limit is not None and len(out) >= limit:
            hit_limit = True
            break
    if dropped:
        debug(f"  deduped away {dropped} duplicate(s)")
    if hit_limit:
        debug(f"  capped at {limit} articles")
    return out


def parse_datetime(soup: BeautifulSoup) -> datetime | None:
    for sel in [
        'meta[property="article:published_time"]',
        'meta[name="publish-date"]',
        'meta[itemprop="datePublished"]',
        "time[datetime]",
    ]:
        el = soup.select_one(sel)
        if not el:
            continue
        raw = (el.get("content") or el.get("datetime") or "").strip()
        if not raw:
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for obj in candidates:
            if not isinstance(obj, dict):
                continue
            for key in ("datePublished", "dateCreated", "dateModified"):
                raw = obj.get(key)
                if not raw:
                    continue
                try:
                    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except ValueError:
                    continue

    # Drishti often shows "11 Jul 2026" near the title
    text = soup.get_text(" ", strip=True)
    m = re.search(
        r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\b",
        text,
    )
    if m:
        try:
            return datetime.strptime(m.group(0), "%d %b %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def parse_date_from_drishti_url(url: str) -> datetime | None:
    # .../news-analysis/11-07-2026 or .../prelims-facts/11-07-2026
    m = re.search(r"/(\d{2})-(\d{2})-(\d{4})/?$", url)
    if not m:
        return None
    day, month, year = m.groups()
    try:
        return datetime(int(year), int(month), int(day), tzinfo=timezone.utc)
    except ValueError:
        return None


def extract_author(soup: BeautifulSoup) -> str | None:
    for sel in [
        'meta[name="author"]',
        'meta[property="article:author"]',
        ".author-name",
        ".byline",
        '[itemprop="author"]',
    ]:
        el = soup.select_one(sel)
        if not el:
            continue
        text = (el.get("content") or el.get_text(" ", strip=True) or "").strip()
        if text:
            return text
    return None


def clean_content(node: Tag, base: str) -> str:
    for sel in CLEANUP_SELECTORS:
        for junk in node.select(sel):
            junk.decompose()
    for comment in node.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()
    for img in node.select("img"):
        src = img.get("src") or ""
        if "track_1x1" in src or img.get("width") == "1" or img.get("height") == "1":
            img.decompose()
    for tag in node.select("[href], [src]"):
        if tag.has_attr("href"):
            tag["href"] = absolutize(base, tag["href"])
        if tag.has_attr("src"):
            tag["src"] = absolutize(base, tag["src"])
    return sanitize_content(str(node), base)


def sanitize_content(html_fragment: str, base: str = "") -> str:
    """Keep readable article markup, links, and real images only."""
    soup = BeautifulSoup(html_fragment, "html.parser")
    root = soup.body or soup

    for selector in CLEANUP_SELECTORS:
        for junk in root.select(selector):
            junk.decompose()

    for comment in root.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for node in list(root.find_all(["aside", "div", "li", "p", "section", "span"])):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).casefold()
        if text in NOISE_TEXT or text.startswith("story continues below this ad"):
            node.decompose()

    for image in list(root.select("img")):
        src = ""
        source_attr = ""
        for attr in (
            "data-src",
            "data-original",
            "data-lazy-src",
            "data-srcset",
            "src",
            "srcset",
        ):
            candidate = (image.get(attr) or "").strip()
            if candidate and not candidate.startswith("data:image"):
                src = candidate.split(",", 1)[0].strip().split()[0]
                source_attr = attr
                break
        placeholder_size = source_attr in {"src", "srcset"} and (
            image.get("width") == "1" or image.get("height") == "1"
        )
        if not src or "track_1x1" in src or placeholder_size:
            image.decompose()
            continue
        image.attrs = {
            "src": absolutize(base, src),
            **({"alt": image.get("alt", "").strip()} if image.get("alt") else {}),
        }

    for tag in list(root.find_all(True)):
        if tag.name not in CONTENT_TAGS:
            tag.unwrap()
            continue
        if tag.name == "a":
            href = (tag.get("href") or "").strip()
            if href and not href.lower().startswith(("http://", "https://", "mailto:")):
                href = absolutize(base, href)
            tag.attrs = {"href": href} if href else {}
        elif tag.name not in {"img"}:
            tag.attrs = {}

    for tag in list(root.find_all(["a", "b", "em", "i", "s", "strong", "u"])):
        if not tag.get_text(strip=True) and not tag.find("img"):
            tag.decompose()

    return "".join(str(child) for child in root.contents).strip()


def atom_text(value: str) -> str:
    return html.escape(value, quote=False)


def render_atom(
    slug: str,
    title: str,
    alternate_url: str,
    author_name: str,
    articles: list[Article],
    feed_self_url: str | None,
) -> str:
    now = datetime.now(timezone.utc)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{atom_text(title)}</title>",
        f'  <link rel="alternate" href="{atom_text(alternate_url)}"/>',
    ]
    if feed_self_url:
        lines.append(
            f'  <link rel="self" type="application/atom+xml" href="{atom_text(feed_self_url)}"/>'
        )
    lines.extend(
        [
            f"  <updated>{now.isoformat()}</updated>",
            f"  <id>urn:gs-magazine-rss:{slug}</id>",
            f"  <author><name>{atom_text(author_name)}</name></author>",
        ]
    )

    for art in articles:
        ts = art.published or now
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        lines.append("  <entry>")
        lines.append(f"    <title>{atom_text(art.title)}</title>")
        lines.append(f"    <id>{atom_text(art.url)}</id>")
        lines.append(f'    <link rel="alternate" href="{atom_text(art.url)}"/>')
        lines.append(f"    <published>{ts.isoformat()}</published>")
        lines.append(f"    <updated>{ts.isoformat()}</updated>")
        if art.author:
            lines.append(f"    <author><name>{atom_text(art.author)}</name></author>")
        safe_html = (
            art.content_html.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        lines.append(f'    <content type="html">{safe_html}</content>')
        lines.append("  </entry>")

    lines.append("</feed>")
    return "\n".join(lines) + "\n"


def feed_fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<updated>[^<]+</updated>", "", text)
    return hashlib.sha256(text.encode()).hexdigest()


# --- Gujarat Samachar -------------------------------------------------------


@dataclass(frozen=True)
class Magazine:
    slug: str
    title: str
    category_path: str
    url_pattern: str


MAGAZINES = [
    Magazine(
        slug="ravi-purti",
        title="Ravi Purti — Gujarat Samachar",
        category_path="magazines/ravi-purti",
        url_pattern="/news/ravi-purti/",
    ),
    Magazine(
        slug="shatdal",
        title="Shatdal — Gujarat Samachar",
        category_path="magazines/shatdal",
        url_pattern="/news/shatdal/",
    ),
    Magazine(
        slug="editorial",
        title="Editorial — Gujarat Samachar",
        category_path="editorial",
        # Category mixes delhi-ni-vaat, news-focus, editorial, prasangpat, tantri-lekh, etc.
        url_pattern="/news/",
    ),
]


def fetch_gs_article(url: str, fallback_title: str) -> Article | None:
    try:
        page = fetch(url)
    except RuntimeError as exc:
        print(f"  ! skip article {url}: {exc}", file=sys.stderr)
        return None
    soup = BeautifulSoup(page, "lxml")
    title_el = soup.select_one("h1") or soup.title
    title = title_el.get_text(" ", strip=True) if title_el else fallback_title
    title = re.sub(r"\s*\|\s*Gujarat Samachar.*$", "", title).strip() or fallback_title
    content_el = soup.select_one(".article-cms-content") or soup.select_one(
        ".gutenberg-content"
    )
    if not content_el:
        print(f"  ! no content for {url}", file=sys.stderr)
        return None
    return Article(
        url=url,
        title=title,
        content_html=clean_content(content_el, GS_BASE),
        published=parse_datetime(soup),
        author=extract_author(soup),
    )


def fetch_business_standard_article(
    url: str,
    fallback_title: str,
    fallback_date: datetime | None,
    fetch_page: Callable[[str], str],
) -> Article | None:
    try:
        page = fetch_page(url)
    except RuntimeError as exc:
        print(f"  ! skip article {url}: {exc}", file=sys.stderr)
        return None

    soup = BeautifulSoup(page, "lxml")
    title_el = soup.select_one("h1") or soup.title
    title = title_el.get_text(" ", strip=True) if title_el else fallback_title
    title = re.sub(r"\s*\|\s*Business Standard.*$", "", title, flags=re.I).strip()
    title = title or fallback_title

    content_el = None
    for selector in (
        '[itemprop="articleBody"]',
        ".article-content",
        ".article-body",
        ".story-content",
        ".story-content-area",
        ".story-body",
    ):
        candidate = soup.select_one(selector)
        if candidate and len(candidate.get_text(" ", strip=True)) > 200:
            content_el = candidate
            break
    if content_el is None:
        candidate = soup.select_one("article")
        if candidate and len(candidate.get_text(" ", strip=True)) > 200:
            content_el = candidate
    if content_el is None:
        print(f"  ! no content for {url}", file=sys.stderr)
        return None

    return Article(
        url=url,
        title=title,
        content_html=clean_content(content_el, BUSINESS_STANDARD_BASE),
        published=parse_datetime(soup) or fallback_date,
        author=extract_author(soup) or "Business Standard",
    )


def collect_business_standard_opinion(
    delay: float, max_articles: int, fetch_page: Callable[[str], str]
) -> list[Article]:
    """Opinion page links → Business Standard article pages with full HTML content."""
    debug(f"  page {BUSINESS_STANDARD_OPINION}")
    links: list[tuple[str, str]] = []
    for attempt in range(1, 4):
        soup = BeautifulSoup(fetch_page(BUSINESS_STANDARD_OPINION), "lxml")
        seen: set[str] = set()
        for anchor in soup.select("a[href]"):
            title = anchor.get_text(" ", strip=True)
            href = anchor.get("href") or ""
            if not title or not href:
                continue
            url = normalize_url(absolutize(BUSINESS_STANDARD_BASE, href))
            parts = urlsplit(url)
            hostname = (parts.hostname or "").lower()
            if (
                hostname not in {"business-standard.com", "www.business-standard.com"}
                or not parts.path.lower().startswith("/opinion/")
                or not parts.path.lower().endswith(".html")
                or not re.search(r"\d{8,}", parts.path)
                or url in seen
            ):
                continue
            seen.add(url)
            links.append((title, url))
            if len(links) >= max_articles:
                break
        if links:
            break
        debug(f"  no opinion links found; retry {attempt}/3")
        if attempt < 3:
            time.sleep(max(1.0, delay) * attempt)

    if not links:
        raise RuntimeError("Business Standard Opinion page returned no articles")

    debug(f"  {len(links)} opinion articles")
    articles: list[Article] = []
    for title, url in links:
        debug(f"  article {url}")
        art = fetch_business_standard_article(url, title, None, fetch_page)
        if art:
            art.url = normalize_url(art.url)
            articles.append(art)
        time.sleep(delay)
    articles = dedupe_articles(articles, limit=max_articles)
    if not articles:
        raise RuntimeError("Business Standard Opinion page returned no full-text articles")
    return articles


def fetch_indian_express_article(
    url: str, fallback_title: str, fetch_page: Callable[[str], str]
) -> Article | None:
    try:
        page = fetch_page(url)
    except RuntimeError as exc:
        print(f"  ! skip article {url}: {exc}", file=sys.stderr)
        return None

    soup = BeautifulSoup(page, "lxml")
    title_el = soup.select_one("h1") or soup.title
    title = title_el.get_text(" ", strip=True) if title_el else fallback_title
    title = re.sub(r"\s*\|\s*The Indian Express.*$", "", title, flags=re.I).strip()
    title = title or fallback_title

    content_el = None
    for selector in (
        '[itemprop="articleBody"]',
        ".story-details",
        ".story-details__content",
        ".article-content",
        ".article-body",
        ".full-details",
        ".story__content",
    ):
        candidate = soup.select_one(selector)
        if candidate and len(candidate.get_text(" ", strip=True)) > 200:
            content_el = candidate
            break
    if content_el is None:
        candidate = soup.select_one("article")
        if candidate and len(candidate.get_text(" ", strip=True)) > 200:
            content_el = candidate
    if content_el is None:
        print(f"  ! no content for {url}", file=sys.stderr)
        return None

    return Article(
        url=url,
        title=title,
        content_html=clean_content(content_el, INDIAN_EXPRESS_BASE),
        published=parse_datetime(soup),
        author=extract_author(soup) or "The Indian Express",
    )


def collect_indian_express_explained(
    delay: float, max_articles: int, fetch_page: Callable[[str], str]
) -> list[Article]:
    """Explained section links → article pages with full HTML content."""
    debug(f"  section {INDIAN_EXPRESS_EXPLAINED}")
    soup = BeautifulSoup(fetch_page(INDIAN_EXPRESS_EXPLAINED), "lxml")
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="/article/"]'):
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href") or ""
        if not title or not href:
            continue
        url = normalize_url(absolutize(INDIAN_EXPRESS_BASE, href))
        if url in seen or url == normalize_url(INDIAN_EXPRESS_EXPLAINED):
            continue
        seen.add(url)
        links.append((title, url))
        if len(links) >= max_articles:
            break

    debug(f"  {len(links)} section articles")
    articles: list[Article] = []
    for title, url in links:
        debug(f"  article {url}")
        art = fetch_indian_express_article(url, title, fetch_page)
        if art:
            art.url = normalize_url(art.url)
            articles.append(art)
        time.sleep(delay)
    return dedupe_articles(articles, limit=max_articles)


def collect_gs_magazine(
    magazine: Magazine, pages: int, delay: float, max_articles: int
) -> list[Article]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        if len(links) >= max_articles:
            break
        listing = f"{GS_BASE}/category/{magazine.category_path}/{page}"
        debug(f"  listing {listing}")
        try:
            html_text = fetch(listing)
        except RuntimeError as exc:
            print(f"  ! {exc}", file=sys.stderr)
            continue
        soup = BeautifulSoup(html_text, "lxml")
        for a in soup.select(".title-wrapper a"):
            href = a.get("href") or ""
            if magazine.url_pattern not in href:
                continue
            url = normalize_url(absolutize(GS_BASE, href))
            title = a.get_text(" ", strip=True)
            if not title or url in seen:
                continue
            seen.add(url)
            links.append((url, title))
            if len(links) >= max_articles:
                break
        time.sleep(delay)

    articles: list[Article] = []
    for url, title in links:
        debug(f"  article {url}")
        art = fetch_gs_article(url, title)
        if art:
            art.url = normalize_url(art.url)
            articles.append(art)
        time.sleep(delay)
    return dedupe_articles(articles, limit=max_articles)


# --- Drishti IAS ------------------------------------------------------------


def drishti_column_box(soup: BeautifulSoup, subheading_class: str) -> Tag | None:
    """Return the `.column.three.box-toggle` whose `.subheading` has the given class."""
    for box in soup.select(".column.three.box-toggle"):
        sub = box.select_one(".subheading")
        if not sub:
            continue
        classes = sub.get("class") or []
        if subheading_class in classes:
            return box
    return None


def column_links(box: Tag) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in box.select("a[href]"):
        title = a.get_text(" ", strip=True)
        href = a.get("href") or ""
        if not title or not href:
            continue
        url = normalize_url(absolutize(DRISHTI_BASE, href))
        if url in seen:
            continue
        seen.add(url)
        items.append((title, url))
    return items


def fetch_drishti_article(
    url: str,
    fallback_title: str,
    fallback_date: datetime | None = None,
) -> Article | None:
    try:
        page = fetch(url)
    except RuntimeError as exc:
        print(f"  ! skip article {url}: {exc}", file=sys.stderr)
        return None
    soup = BeautifulSoup(page, "lxml")
    title_el = soup.select_one(".article-detail h1") or soup.select_one("h1") or soup.title
    title = title_el.get_text(" ", strip=True) if title_el else fallback_title
    title = re.sub(r"\s*\|\s*Drishti.*$", "", title, flags=re.I).strip() or fallback_title

    content_el = soup.select_one(".article-detail")
    if not content_el:
        print(f"  ! no content for {url}", file=sys.stderr)
        return None

    return Article(
        url=url,
        title=title,
        content_html=clean_content(content_el, DRISHTI_BASE),
        published=parse_datetime(soup) or fallback_date or parse_date_from_drishti_url(url),
        author=extract_author(soup) or "Drishti IAS",
    )


def expand_drishti_day_page(day_url: str, delay: float) -> list[tuple[str, str, datetime | None]]:
    """From a daily digest page, collect individual article (title, url, day_date)."""
    debug(f"  day {day_url}")
    try:
        html_text = fetch(day_url)
    except RuntimeError as exc:
        print(f"  ! {exc}", file=sys.stderr)
        return []
    soup = BeautifulSoup(html_text, "lxml")
    day_date = parse_date_from_drishti_url(day_url)
    items: list[tuple[str, str, datetime | None]] = []
    seen: set[str] = set()
    for a in soup.select(".article-detail h1 a"):
        title = a.get_text(" ", strip=True)
        href = a.get("href") or ""
        if not title or not href:
            continue
        url = normalize_url(absolutize(DRISHTI_BASE, href))
        if url in seen:
            continue
        seen.add(url)
        items.append((title, url, day_date))
    time.sleep(delay)
    return items


def collect_drishti_current_affairs(
    days: int, delay: float, max_articles: int
) -> list[Article]:
    """Green column: date digests → individual daily-news-analysis articles."""
    debug(f"  index {DRISHTI_INDEX}")
    soup = BeautifulSoup(fetch(DRISHTI_INDEX), "lxml")
    box = drishti_column_box(soup, "bg-green")
    if not box:
        raise RuntimeError("Drishti current-affairs column (.bg-green) not found")

    day_links = [
        (title, url)
        for title, url in column_links(box)
        if "/news-analysis/" in url and re.search(r"/\d{2}-\d{2}-\d{4}/?$", url)
    ][:days]
    debug(f"  {len(day_links)} day digests")

    article_links: list[tuple[str, str, datetime | None]] = []
    seen: set[str] = set()
    for _, day_url in day_links:
        if len(article_links) >= max_articles:
            break
        for title, url, day_date in expand_drishti_day_page(day_url, delay):
            if url in seen:
                continue
            seen.add(url)
            article_links.append((title, url, day_date))
            if len(article_links) >= max_articles:
                break

    articles: list[Article] = []
    for title, url, day_date in article_links:
        debug(f"  article {url}")
        art = fetch_drishti_article(url, title, day_date)
        if art:
            art.url = normalize_url(art.url)
            articles.append(art)
        time.sleep(delay)
    return dedupe_articles(articles, limit=max_articles)


def collect_drishti_editorials(
    limit: int, delay: float, max_articles: int
) -> list[Article]:
    """Purple column: direct editorial article links."""
    debug(f"  index {DRISHTI_INDEX}")
    soup = BeautifulSoup(fetch(DRISHTI_INDEX), "lxml")
    box = drishti_column_box(soup, "bg-purple")
    if not box:
        raise RuntimeError("Drishti editorials column (.bg-purple) not found")

    cap = min(limit, max_articles)
    links = [
        (title, url)
        for title, url in column_links(box)
        if "/daily-news-editorials/" in url or "/daily-updates/daily-news-editorials/" in url
    ][:cap]
    debug(f"  {len(links)} editorials")

    articles: list[Article] = []
    for title, url in links:
        debug(f"  article {url}")
        art = fetch_drishti_article(url, title)
        if art:
            art.url = normalize_url(art.url)
            articles.append(art)
        time.sleep(delay)
    return dedupe_articles(articles, limit=max_articles)


def collect_drishti_prelims_facts(
    days: int, delay: float, max_articles: int
) -> list[Article]:
    """Pink column: date digests → individual prelims-facts articles."""
    debug(f"  index {DRISHTI_INDEX}")
    soup = BeautifulSoup(fetch(DRISHTI_INDEX), "lxml")
    box = drishti_column_box(soup, "bg-pink")
    if not box:
        raise RuntimeError("Drishti prelims-facts column (.bg-pink) not found")

    day_links = [
        (title, url)
        for title, url in column_links(box)
        if "/prelims-facts/" in url and re.search(r"/\d{2}-\d{2}-\d{4}/?$", url)
    ][:days]
    debug(f"  {len(day_links)} day digests")

    article_links: list[tuple[str, str, datetime | None]] = []
    seen: set[str] = set()
    for _, day_url in day_links:
        if len(article_links) >= max_articles:
            break
        for title, url, day_date in expand_drishti_day_page(day_url, delay):
            if url in seen:
                continue
            # Prefer true prelims-facts article URLs; skip cross-links into other sections
            if "/prelims-facts/" not in url and "/daily-news-analysis/" not in url:
                # still allow if it's under daily-updates
                if "/daily-updates/" not in url:
                    continue
            seen.add(url)
            article_links.append((title, url, day_date))
            if len(article_links) >= max_articles:
                break

    articles: list[Article] = []
    for title, url, day_date in article_links:
        debug(f"  article {url}")
        art = fetch_drishti_article(url, title, day_date)
        if art:
            art.url = normalize_url(art.url)
            articles.append(art)
        time.sleep(delay)
    return dedupe_articles(articles, limit=max_articles)


def write_feed(
    out_dir: Path,
    slug: str,
    title: str,
    alternate_url: str,
    author_name: str,
    articles: list[Article],
    base_feed_url: str,
) -> bool:
    if not articles:
        print(f"  ! no articles for {slug}", file=sys.stderr)
        return False
    self_url = f"{base_feed_url.rstrip('/')}/{slug}.xml" if base_feed_url else None
    atom = render_atom(slug, title, alternate_url, author_name, articles, self_url)
    out_path = out_dir / f"{slug}.xml"
    old_fp = feed_fingerprint(out_path)
    out_path.write_text(atom, encoding="utf-8")
    new_fp = feed_fingerprint(out_path)
    changed = old_fp != new_fp
    print(f"  wrote {out_path} ({'changed' if changed else 'content unchanged'})")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-articles",
        type=int,
        default=50,
        help="Max articles per feed after dedupe (default: 50)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=5,
        help="Gujarat Samachar listing pages per magazine (default: 5 ≈ 50 items)",
    )
    parser.add_argument(
        "--drishti-days",
        type=int,
        default=30,
        help="Drishti CA/Prelims day digests to expand (default: 30)",
    )
    parser.add_argument(
        "--drishti-editorials",
        type=int,
        default=50,
        help="Drishti editorial articles from the purple column (default: 50)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="Delay between HTTP requests in seconds",
    )
    parser.add_argument("--out", type=Path, default=Path("feeds"))
    parser.add_argument(
        "--base-feed-url",
        default="",
        help="Public base URL for feed files",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Optional feed slugs to generate (default: all)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print listing, article, and deduplication debug output",
    )
    args = parser.parse_args()
    global VERBOSE
    VERBOSE = args.verbose
    args.out.mkdir(parents=True, exist_ok=True)
    only = set(args.only) if args.only else None
    changed = False
    max_articles = max(1, args.max_articles)

    browser_needed = {
        "business-standard-opinion",
        "indian-express-explained",
    }
    if not only or browser_needed & only:
        try:
            with BrowserFetcher() as browser:
                if not only or "business-standard-opinion" in only:
                    slug = "business-standard-opinion"
                    print(f"== {slug} ==")
                    try:
                        articles = collect_business_standard_opinion(
                            args.delay, max_articles, browser.fetch
                        )
                    except RuntimeError as exc:
                        print(f"  ! {exc}", file=sys.stderr)
                        return 1
                    else:
                        print(f"  collected {len(articles)} articles")
                        if not write_feed(
                            args.out,
                            slug,
                            "Business Standard — Opinion Columns",
                            BUSINESS_STANDARD_OPINION,
                            "Business Standard",
                            articles,
                            args.base_feed_url,
                        ):
                            return 1
                        changed = True

                if not only or "indian-express-explained" in only:
                    slug = "indian-express-explained"
                    print(f"== {slug} ==")
                    try:
                        articles = collect_indian_express_explained(
                            args.delay, max_articles, browser.fetch
                        )
                    except RuntimeError as exc:
                        print(f"  ! {exc}", file=sys.stderr)
                    else:
                        print(f"  collected {len(articles)} articles")
                        if write_feed(
                            args.out,
                            slug,
                            "The Indian Express — Explained",
                            INDIAN_EXPRESS_EXPLAINED,
                            "The Indian Express",
                            articles,
                            args.base_feed_url,
                        ):
                            changed = True
        except RuntimeError as exc:
            print(f"  ! {exc}", file=sys.stderr)

    # Gujarat Samachar
    for mag in MAGAZINES:
        if only and mag.slug not in only:
            continue
        print(f"== {mag.slug} ==")
        articles = collect_gs_magazine(
            mag, pages=args.pages, delay=args.delay, max_articles=max_articles
        )
        print(f"  collected {len(articles)} articles")
        if write_feed(
            args.out,
            mag.slug,
            mag.title,
            f"{GS_BASE}/category/{mag.category_path}",
            "Gujarat Samachar",
            articles,
            args.base_feed_url,
        ):
            changed = True

    # Drishti — three separate feeds
    drishti_jobs = [
        (
            "drishti-current-affairs",
            "Drishti IAS — करेंट अफेयर्स (Hindi)",
            lambda: collect_drishti_current_affairs(
                args.drishti_days, args.delay, max_articles
            ),
        ),
        (
            "drishti-editorials",
            "Drishti IAS — प्रमुख एडिटोरियल (Hindi)",
            lambda: collect_drishti_editorials(
                args.drishti_editorials, args.delay, max_articles
            ),
        ),
        (
            "drishti-prelims-facts",
            "Drishti IAS — प्रिलिम्स फैक्ट्स (Hindi)",
            lambda: collect_drishti_prelims_facts(
                args.drishti_days, args.delay, max_articles
            ),
        ),
    ]

    for slug, title, collector in drishti_jobs:
        if only and slug not in only:
            continue
        print(f"== {slug} ==")
        try:
            articles = collector()
        except RuntimeError as exc:
            print(f"  ! {exc}", file=sys.stderr)
            continue
        print(f"  collected {len(articles)} articles")
        if write_feed(
            args.out,
            slug,
            title,
            DRISHTI_INDEX,
            "Drishti IAS",
            articles,
            args.base_feed_url,
        ):
            changed = True

    marker = args.out / ".changed"
    if changed:
        marker.write_text("1\n", encoding="utf-8")
    elif marker.exists():
        marker.unlink()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
