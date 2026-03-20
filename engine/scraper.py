"""
Scrape Lenny's Podcast and Melissa Perri's Product Thinking episodes.
Uses CSS selectors from config/sources.json.
"""

import json
import os
import sys
import re
from datetime import datetime, timezone, timedelta

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("[ERROR] Missing dependencies. Run: pip3 install requests beautifulsoup4")
    sys.exit(1)

SOURCES_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'sources.json')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}


def _load_sources() -> list:
    with open(os.path.abspath(SOURCES_PATH)) as f:
        return json.load(f)['sources']


def _parse_date(date_str: str) -> datetime | None:
    """Try to parse a date string into an aware datetime."""
    if not date_str:
        return None
    date_str = date_str.strip()
    formats = [
        '%b %d, %Y', '%B %d, %Y', '%Y-%m-%d',
        '%d %b %Y', '%d %B %Y', '%b %d %Y',
        '%m/%d/%Y', '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S%z',
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return None


def _scrape_source(source: dict) -> list[dict]:
    """Generic scraper for a single source config."""
    url = source['url']
    sel = source['selectors']
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Could not fetch {url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    containers = soup.select(sel['episode_container'])
    if not containers:
        # Try alternate Substack selectors
        containers = soup.select('div[class*="post"]') or soup.select('article')

    episodes = []
    for container in containers:
        try:
            title_el = container.select_one(sel['title']) or container.select_one('h2') or container.select_one('h3')
            date_el = container.select_one(sel['date']) or container.select_one('time')
            desc_el = container.select_one(sel['description']) or container.select_one('p')
            link_el = container.select_one(sel['link']) or container.select_one('a')

            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if not title:
                continue

            # Date: prefer datetime attribute on <time>
            date_str = ''
            if date_el:
                date_str = date_el.get('datetime') or date_el.get_text(strip=True)

            description = desc_el.get_text(strip=True) if desc_el else ''

            link = ''
            if link_el:
                href = link_el.get('href', '')
                if href:
                    link = href if href.startswith('http') else f"https://{url.split('/')[2]}{href}"

            episodes.append({
                'source': source['id'],
                'title': title,
                'url': link,
                'published_date': date_str,
                'description': description[:500],
            })
        except Exception as e:
            print(f"[WARN] Error parsing episode from {source['id']}: {e}")
            continue

    return episodes


def filter_new_episodes(episodes: list[dict], seen_urls: list[str], days: int = 7) -> list[dict]:
    """Return episodes from last N days that haven't been seen before."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for ep in episodes:
        if ep['url'] in seen_urls:
            continue
        parsed = _parse_date(ep.get('published_date', ''))
        # If we can't parse the date, include it (better to over-include)
        if parsed and parsed < cutoff:
            continue
        result.append(ep)
    return result


def scrape_lenny() -> list[dict]:
    sources = _load_sources()
    for s in sources:
        if s['id'] == 'lennys_podcast' and s.get('enabled'):
            return _scrape_source(s)
    return []


def scrape_melissa() -> list[dict]:
    sources = _load_sources()
    for s in sources:
        if s['id'] == 'product_thinking' and s.get('enabled'):
            return _scrape_source(s)
    return []


def scrape_all() -> list[dict]:
    """Scrape all enabled sources."""
    sources = _load_sources()
    all_episodes = []
    for s in sources:
        if s.get('enabled'):
            eps = _scrape_source(s)
            all_episodes.extend(eps)
            print(f"[INFO] {s['id']}: found {len(eps)} episodes")
    return all_episodes


if __name__ == '__main__':
    print("Scraping all sources...\n")
    episodes = scrape_all()
    print(f"\nTotal episodes found: {len(episodes)}")
    for ep in episodes[:10]:
        print(f"  [{ep['source']}] {ep['title'][:80]}")
        print(f"    Date: {ep['published_date']}  URL: {ep['url'][:70]}")
