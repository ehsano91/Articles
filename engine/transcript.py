"""
Fetch Lenny's podcast transcripts from GitHub repo:
https://github.com/ChatPRD/lennys-podcast-transcripts
"""

import json
import os
import sys
from datetime import datetime, timezone

try:
    import requests
    import yaml
except ImportError:
    print("[ERROR] Missing dependencies. Run: pip3 install requests PyYAML")
    sys.exit(1)

GITHUB_API = "https://api.github.com/repos/ChatPRD/lennys-podcast-transcripts/contents/episodes"
RAW_BASE = "https://raw.githubusercontent.com/ChatPRD/lennys-podcast-transcripts/main/episodes"

HEADERS = {
    'User-Agent': 'articles-pipeline/1.0',
    'Accept': 'application/vnd.github.v3+json',
}


def list_episode_slugs() -> list[dict]:
    """Return list of {name, download_url} from GitHub contents API."""
    try:
        resp = requests.get(GITHUB_API, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        items = resp.json()
        return [
            {'name': item['name'], 'download_url': item['download_url']}
            for item in items
            if item['type'] == 'file' and item['name'].endswith('.md')
        ]
    except Exception as e:
        print(f"[WARN] Could not list episode slugs: {e}")
        return []


def fetch_transcript(slug_info: dict) -> dict | None:
    """Fetch a single transcript file and parse YAML frontmatter."""
    url = slug_info.get('download_url') or f"{RAW_BASE}/{slug_info['name']}"
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        content = resp.text
    except Exception as e:
        print(f"[WARN] Could not fetch transcript {slug_info['name']}: {e}")
        return None

    # Parse YAML frontmatter (--- ... ---)
    frontmatter = {}
    body = content
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                frontmatter = {}
            body = parts[2].strip()

    return {
        'slug': slug_info['name'].replace('.md', ''),
        'title': frontmatter.get('title', slug_info['name']),
        'publish_date': str(frontmatter.get('publish_date') or frontmatter.get('date') or ''),
        'guest': frontmatter.get('guest', ''),
        'description': frontmatter.get('description', ''),
        'body_preview': body[:1000],
    }


def fetch_recent_transcripts(n: int = 15) -> list[dict]:
    """Fetch the N most recent transcripts by publish_date."""
    slugs = list_episode_slugs()
    if not slugs:
        print("[WARN] No transcript slugs found")
        return []

    print(f"[INFO] Found {len(slugs)} transcript files, fetching up to {n}...")

    transcripts = []
    for slug_info in slugs[:n * 2]:  # Fetch more than needed to sort properly
        t = fetch_transcript(slug_info)
        if t:
            transcripts.append(t)

    # Sort by publish_date descending
    def sort_key(t):
        d = t.get('publish_date', '')
        if not d:
            return ''
        return str(d)

    transcripts.sort(key=sort_key, reverse=True)
    return transcripts[:n]


def extract_covered_themes(transcripts: list[dict]) -> list[str]:
    """Return list of topic strings (title + description) for Claude context."""
    themes = []
    for t in transcripts:
        parts = [t['title']]
        if t.get('guest'):
            parts.append(f"with {t['guest']}")
        if t.get('description'):
            parts.append(t['description'][:200])
        themes.append(' — '.join(parts))
    return themes


if __name__ == '__main__':
    print("Fetching recent transcripts from GitHub...\n")
    transcripts = fetch_recent_transcripts(15)
    print(f"Fetched {len(transcripts)} transcripts:\n")
    for t in transcripts:
        print(f"  [{t['publish_date']}] {t['title'][:80]}")
        if t.get('guest'):
            print(f"    Guest: {t['guest']}")
    print("\nCovered themes (for Claude context):")
    for theme in extract_covered_themes(transcripts):
        print(f"  - {theme[:100]}")
