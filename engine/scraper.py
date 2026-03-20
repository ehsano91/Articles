"""
Scrape Lenny's Podcast and Melissa Perri's Product Thinking via RSS feeds.
RSS is reliable, fast, and doesn't require CSS selectors or JS rendering.
"""

import os
import sys
import re
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET
from html.parser import HTMLParser

try:
    import requests
except ImportError:
    print("[ERROR] Missing dependency. Run: pip3 install requests")
    sys.exit(1)

SOURCES = [
    {
        "id": "lennys_podcast",
        "name": "Lenny's Podcast",
        "rss": "https://www.lennysnewsletter.com/feed",
        "enabled": True,
    },
    {
        "id": "product_thinking",
        "name": "Product Thinking (Melissa Perri)",
        "rss": "https://anchor.fm/s/ff7e9014/podcast/rss",
        "enabled": True,
    },
]

HEADERS = {"User-Agent": "articles-pipeline/1.0"}


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self):
        return ' '.join(self._parts).strip()


def _strip_html(text: str) -> str:
    if not text:
        return ''
    p = _HTMLStripper()
    try:
        p.feed(text)
        return p.get_text()[:500]
    except Exception:
        return re.sub(r'<[^>]+>', '', text)[:500]


def _parse_rss_date(date_str: str) -> datetime | None:
    """Parse RFC 2822 dates from RSS feeds."""
    if not date_str:
        return None
    formats = [
        '%a, %d %b %Y %H:%M:%S %z',
        '%a, %d %b %Y %H:%M:%S %Z',
        '%a, %d %b %Y %H:%M:%S GMT',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None


def _scrape_rss(source: dict) -> list[dict]:
    """Fetch and parse an RSS feed into episode dicts."""
    try:
        resp = requests.get(source['rss'], headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Could not fetch {source['id']} RSS: {e}")
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        print(f"[WARN] Could not parse {source['id']} RSS XML: {e}")
        return []

    channel = root.find('channel')
    if channel is None:
        return []

    episodes = []
    for item in channel.findall('item'):
        title = item.findtext('title', '').strip()
        if not title:
            continue
        link = item.findtext('link', '').strip()
        pub_date = item.findtext('pubDate', '').strip()
        desc = _strip_html(item.findtext('description', '') or item.findtext('{http://www.itunes.com/dtds/podcast-1.0.dtd}summary', ''))

        episodes.append({
            'source': source['id'],
            'title': title,
            'url': link,
            'published_date': pub_date,
            'description': desc,
        })

    return episodes


def filter_new_episodes(episodes: list[dict], seen_urls: list[str], days: int = 7) -> list[dict]:
    """Return episodes from last N days not already seen."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for ep in episodes:
        if ep['url'] in seen_urls:
            continue
        parsed = _parse_rss_date(ep.get('published_date', ''))
        if parsed and parsed < cutoff:
            continue
        result.append(ep)
    return result


def scrape_all() -> list[dict]:
    all_episodes = []
    for source in SOURCES:
        if not source.get('enabled'):
            continue
        eps = _scrape_rss(source)
        all_episodes.extend(eps)
        print(f"[INFO] {source['id']}: found {len(eps)} episodes")
    return all_episodes


def scrape_lenny() -> list[dict]:
    return _scrape_rss(next(s for s in SOURCES if s['id'] == 'lennys_podcast'))


def scrape_melissa() -> list[dict]:
    return _scrape_rss(next(s for s in SOURCES if s['id'] == 'product_thinking'))


if __name__ == '__main__':
    print("Scraping all sources via RSS...\n")
    episodes = scrape_all()
    new = filter_new_episodes(episodes, [], days=7)
    print(f"\nTotal: {len(episodes)} episodes — {len(new)} from last 7 days\n")
    for ep in new:
        print(f"  [{ep['source']}] {ep['title'][:80]}")
        print(f"    Date: {ep['published_date']}  URL: {ep['url'][:70]}")
