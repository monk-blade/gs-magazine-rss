#!/usr/bin/env python3
"""Remove unwanted markup from existing generated Atom feed content."""

from __future__ import annotations

import argparse
import html
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from generate_feeds import sanitize_content


def content_base(entry: BeautifulSoup) -> str:
    link = entry.find("link", attrs={"rel": "alternate"}) or entry.find("link")
    href = (link.get("href") if link else "") or ""
    parts = urlsplit(href)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


def cleanup_feed(path: Path, dry_run: bool = False) -> tuple[int, bool]:
    source = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(source, "xml")
    changed = False
    cleaned = 0

    for entry in soup.find_all("entry"):
        base = content_base(entry)
        for content in entry.find_all("content", attrs={"type": "html"}):
            raw = html.unescape(content.string or content.get_text())
            clean = sanitize_content(raw, base)
            if clean == raw:
                continue
            content.string = clean
            cleaned += 1
            changed = True

    if changed and not dry_run:
        path.write_text(str(soup), encoding="utf-8")
    return cleaned, changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feeds", type=Path, default=Path("feeds"))
    parser.add_argument("--pattern", default="*.xml")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report changes without rewriting files"
    )
    args = parser.parse_args()

    files = sorted(args.feeds.glob(args.pattern))
    if not files:
        print(f"No feed files found in {args.feeds}")
        return 0

    total = 0
    changed_files = 0
    for path in files:
        cleaned, changed = cleanup_feed(path, dry_run=args.dry_run)
        total += cleaned
        if changed:
            changed_files += 1
            action = "would clean" if args.dry_run else "cleaned"
            print(f"{action} {path}: {cleaned} content block(s)")

    print(f"Processed {len(files)} feed(s); cleaned {total} content block(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
