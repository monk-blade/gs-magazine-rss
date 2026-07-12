# Gujarat Samachar magazine RSS

GitHub Actions scrapes **Ravi Purti** and **Shatdal** from [Gujarat Samachar](https://www.gujaratsamachar.com), including listing pagination (`/1`, `/2`, …), and publishes clean Atom feeds.

## Feeds (after first successful Action run)

Replace `USER` with your GitHub username:

| Magazine | Feed URL |
|----------|----------|
| Ravi Purti | `https://raw.githubusercontent.com/USER/gs-magazine-rss/main/feeds/ravi-purti.xml` |
| Shatdal | `https://raw.githubusercontent.com/USER/gs-magazine-rss/main/feeds/shatdal.xml` |

Add those URLs in FreshRSS. Leave **Article CSS selector** empty — full HTML is already in the feed.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python generate_feeds.py --pages 2 --out feeds
```

## GitHub setup

1. Create a new GitHub repo and push this project.
2. Actions → **Update magazine feeds** → **Run workflow** (or wait for the 6-hour cron).
3. Optional repo variable `BASE_FEED_URL`  
   e.g. `https://raw.githubusercontent.com/USER/gs-magazine-rss/main/feeds`  
   (sets Atom `<link rel="self">`).

## Notes

- Default crawl: **2 listing pages** per magazine (~20 articles each).
- Public RSS-Bridge hosts are flaky for this site; this Action is the stable path.
- Be polite to the origin: the script uses delays between requests.
