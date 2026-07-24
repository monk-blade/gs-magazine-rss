# Gujarat Samachar + Drishti IAS RSS

GitHub Actions scrapes magazine/current-affairs pages and publishes clean Atom feeds.

## Feeds

In FreshRSS, append `#force_feed` (GitHub raw serves `text/plain`):

| Feed | URL |
|------|-----|
| Business Standard — Opinion Columns | `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/business-standard-opinion.xml#force_feed` |
| The Indian Express — Explained | `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/indian-express-explained.xml#force_feed` |
| Ravi Purti | `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/ravi-purti.xml#force_feed` |
| Shatdal | `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/shatdal.xml#force_feed` |
| Editorial | `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/editorial.xml#force_feed` |
| Drishti — करेंट अफेयर्स | `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/drishti-current-affairs.xml#force_feed` |
| Drishti — प्रमुख एडिटोरियल | `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/drishti-editorials.xml#force_feed` |
| Drishti — प्रिलिम्स फैक्ट्स | `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/drishti-prelims-facts.xml#force_feed` |

Source page for Drishti columns:  
https://www.drishtiias.com/hindi/current-affairs-news-analysis-editorials

Leave **Article CSS selector** empty — full HTML is already in the feed.

Business Standard Opinion source page:
https://www.business-standard.com/opinion

The Indian Express Explained source page:
https://indianexpress.com/section/explained/

## How Drishti columns are scraped

| Column | Selector | Strategy |
|--------|----------|----------|
| करेंट अफेयर्स (green) | `.subheading.bg-green` | Date digests → expand `.article-detail h1 a` |
| प्रमुख एडिटोरियल (purple) | `.subheading.bg-purple` | Direct editorial article links |
| प्रिलिम्स फैक्ट्स (pink) | `.subheading.bg-pink` | Date digests → expand `.article-detail h1 a` |

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

# Clean existing generated feeds (also run automatically in GitHub Actions)
python cleanup_feeds.py --feeds feeds

# all feeds (~50 articles each, deduped)
python generate_feeds.py --max-articles 50 --out feeds

# Drishti only
python generate_feeds.py --only drishti-current-affairs drishti-editorials drishti-prelims-facts --out feeds
```

## GitHub

1. Actions → **Update magazine feeds** → **Run workflow**
2. Optional repo variable `BASE_FEED_URL`  
   `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds`

## Notes

- Default: **50 articles** per feed after URL/title dedupe.
- Gujarat Samachar: 5 listing pages (capped at `--max-articles`).
- Drishti: up to 30 day digests for CA/Prelims; 50 editorials from the purple column.
- Business Standard Opinion: opinion-page links are expanded into full article HTML.
- The Indian Express Explained: section links are expanded into full article HTML.
- Business Standard and Indian Express are fetched through a shared headless Chromium session.
- If Business Standard's Akamai edge returns HTTP 403, the same Playwright session retries through an HTML reader transport.
- Generated content is sanitized to remove ads, tracking, wrappers, and empty markup while preserving article images.
- Deduplication: normalized URL (trailing slash / utm stripped) and normalized title.
- Be polite to origins: the script delays between requests.
