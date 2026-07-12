#!/usr/bin/env python3
"""Generate Atom feeds for Gujarat Samachar magazines (Ravi Purti, Shatdal)."""

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
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

BASE = "https://www.gujaratsamachar.com"
USER_AGENT = (
    "Mozilla/5.0 (compatible; gs-magazine-rss/1.0; +https://github.com/)"
    " AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

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
    "img.lazyloading",
]


@dataclass(frozen=True)
class Magazine:
    slug: str
    title: str
    category_path: str  # e.g. magazines/ravi-purti
    url_pattern: str  # e.g. /news/ravi-purti/


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
]


@dataclass
class Article:
    url: str
    title: str
    content_html: str
    published: datetime | None = None
    author: str | None = None


def fetch(url: str, retries: int = 3, pause: float = 1.0) -> str:
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "gu,en;q=0.8",
                },
            )
            with urlopen(req, timeout=45) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError) as exc:
            last_err = exc
            time.sleep(pause * attempt)
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")


def absolutize(base: str, href: str) -> str:
    return urljoin(base, href)


def listing_urls(magazine: Magazine, pages: int) -> list[str]:
    # /category/magazines/<slug>/1 is the canonical first page
    return [
        f"{BASE}/category/{magazine.category_path}/{page}"
        for page in range(1, pages + 1)
    ]


def extract_listing_links(html_text: str, magazine: Magazine) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html_text, "lxml")
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in soup.select(".title-wrapper a"):
        href = a.get("href") or ""
        if magazine.url_pattern not in href:
            continue
        url = absolutize(BASE, href)
        if url in seen:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        seen.add(url)
        items.append((url, title))
    return items


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
        raw = el.get("content") or el.get("datetime") or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            # Handle Z suffix
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    # JSON-LD
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


def clean_content(node) -> str:
    for sel in CLEANUP_SELECTORS:
        for junk in node.select(sel):
            junk.decompose()
    # Drop 1x1 trackers
    for img in node.select("img"):
        src = img.get("src") or ""
        if "track_1x1" in src or img.get("width") == "1" or img.get("height") == "1":
            img.decompose()
    # Absolutize remaining links/images
    for tag in node.select("[href], [src]"):
        if tag.has_attr("href"):
            tag["href"] = absolutize(BASE, tag["href"])
        if tag.has_attr("src"):
            tag["src"] = absolutize(BASE, tag["src"])
    return str(node)


def fetch_article(url: str, fallback_title: str) -> Article | None:
    try:
        page = fetch(url)
    except RuntimeError as exc:
        print(f"  ! skip article {url}: {exc}", file=sys.stderr)
        return None
    soup = BeautifulSoup(page, "lxml")
    title_el = soup.select_one("h1") or soup.title
    title = (
        title_el.get_text(" ", strip=True)
        if title_el
        else fallback_title
    )
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
        content_html=clean_content(content_el),
        published=parse_datetime(soup),
        author=extract_author(soup),
    )


def collect_articles(magazine: Magazine, pages: int, delay: float) -> list[Article]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for listing in listing_urls(magazine, pages):
        print(f"  listing {listing}")
        try:
            html_text = fetch(listing)
        except RuntimeError as exc:
            print(f"  ! {exc}", file=sys.stderr)
            continue
        for url, title in extract_listing_links(html_text, magazine):
            if url in seen:
                continue
            seen.add(url)
            links.append((url, title))
        time.sleep(delay)

    articles: list[Article] = []
    for url, title in links:
        print(f"  article {url}")
        art = fetch_article(url, title)
        if art:
            articles.append(art)
        time.sleep(delay)
    return articles


def atom_text(value: str) -> str:
    return html.escape(value, quote=False)


def build_atom(
    magazine: Magazine,
    articles: Iterable[Article],
    feed_self_url: str | None,
) -> str:
    return render_atom_manual(
        magazine,
        list(articles),
        feed_self_url,
        datetime.now(timezone.utc),
    )


def render_atom_manual(
    magazine: Magazine,
    articles: list[Article],
    feed_self_url: str | None,
    now: datetime,
) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        f"  <title>{atom_text(magazine.title)}</title>",
        f'  <link rel="alternate" href="{atom_text(BASE + "/category/" + magazine.category_path)}"/>',
    ]
    if feed_self_url:
        lines.append(
            f'  <link rel="self" type="application/atom+xml" href="{atom_text(feed_self_url)}"/>'
        )
    lines.extend(
        [
            f"  <updated>{now.isoformat()}</updated>",
            f"  <id>urn:gs-magazine-rss:{magazine.slug}</id>",
            "  <author><name>Gujarat Samachar</name></author>",
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
            lines.append(
                f"    <author><name>{atom_text(art.author)}</name></author>"
            )
        # Escape only XML-significant chars that would break the document,
        # keeping tags intact: wrap as text by escaping <>& for safety then
        # using type=html with properly escaped content (standard Atom).
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
    # Ignore <updated> timestamps when comparing
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"<updated>[^<]+</updated>", "", text)
    return hashlib.sha256(text.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pages",
        type=int,
        default=2,
        help="Listing pages to crawl per magazine (default: 2 ≈ 20 articles)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="Delay between HTTP requests in seconds",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("feeds"),
        help="Output directory for Atom files",
    )
    parser.add_argument(
        "--base-feed-url",
        default="",
        help="Public base URL for feed files, e.g. https://USER.github.io/gs-magazine-rss/feeds",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    changed = False

    for magazine in MAGAZINES:
        print(f"== {magazine.slug} ==")
        articles = collect_articles(magazine, pages=args.pages, delay=args.delay)
        print(f"  collected {len(articles)} articles")
        if not articles:
            print(f"  ! no articles for {magazine.slug}", file=sys.stderr)
            continue

        self_url = (
            f"{args.base_feed_url.rstrip('/')}/{magazine.slug}.xml"
            if args.base_feed_url
            else None
        )
        atom = build_atom(magazine, articles, self_url)
        out_path = args.out / f"{magazine.slug}.xml"
        old_fp = feed_fingerprint(out_path)
        out_path.write_text(atom, encoding="utf-8")
        new_fp = feed_fingerprint(out_path)
        if old_fp != new_fp:
            changed = True
            print(f"  wrote {out_path} (changed)")
        else:
            # Still write so timestamps refresh, but note unchanged body
            print(f"  wrote {out_path} (content unchanged)")

    # Marker for the workflow
    marker = args.out / ".changed"
    if changed:
        marker.write_text("1\n", encoding="utf-8")
    elif marker.exists():
        marker.unlink()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
