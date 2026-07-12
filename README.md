# Gujarat Samachar + Drishti IAS RSS

GitHub Actions scrapes magazine/current-affairs pages and publishes clean Atom feeds.

## Feeds

| Feed | URL |
|------|-----|
| Ravi Purti | https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/ravi-purti.xml |
| Shatdal | https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/shatdal.xml |
| Drishti — करेंट अफेयर्स | https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/drishti-current-affairs.xml |
| Drishti — प्रमुख एडिटोरियल | https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/drishti-editorials.xml |
| Drishti — प्रिलिम्स फैक्ट्स | https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds/drishti-prelims-facts.xml |

Source page for Drishti columns:  
https://www.drishtiias.com/hindi/current-affairs-news-analysis-editorials

Add those URLs in FreshRSS. Leave **Article CSS selector** empty — full HTML is already in the feed.

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

# all feeds
python generate_feeds.py --pages 2 --drishti-days 5 --out feeds

# Drishti only
python generate_feeds.py --only drishti-current-affairs drishti-editorials drishti-prelims-facts --drishti-days 3 --out feeds
```

## GitHub

1. Actions → **Update magazine feeds** → **Run workflow**
2. Optional repo variable `BASE_FEED_URL`  
   `https://raw.githubusercontent.com/monk-blade/gs-magazine-rss/master/feeds`

## Notes

- Gujarat Samachar: 2 listing pages by default (~20 articles each).
- Drishti: 5 day digests for CA/Prelims; 12 editorials from the purple column.
- Be polite to origins: the script delays between requests.
