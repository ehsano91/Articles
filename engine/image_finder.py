"""
Find a relevant cover image for an article using Unsplash API.
Requires a free Unsplash access key in config/settings.json.
"""

import json
import os
import sys

try:
    import requests
except ImportError:
    print("[ERROR] Missing dependency. Run: pip3 install requests")
    sys.exit(1)

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.json')
UNSPLASH_API = "https://api.unsplash.com/search/photos"


def _load_settings() -> dict:
    with open(os.path.abspath(SETTINGS_PATH)) as f:
        return json.load(f)


def _suggest_keywords(article: dict) -> list[str]:
    """
    Use Claude to suggest 3 image search keywords for the article.
    Falls back to extracting words from the title if Claude unavailable.
    """
    try:
        import claude_client
        settings = _load_settings()
        model = settings.get('claude_model', 'claude-sonnet-4-6')
        client = claude_client._get_client()

        title = article.get('title', '')
        hook = article.get('hook', '')[:200]

        resp = client.messages.create(
            model=model,
            max_tokens=100,
            system="You suggest Unsplash image search keywords. Respond with only a JSON array of 3 short keyword phrases, no explanation.",
            messages=[{
                "role": "user",
                "content": f"Suggest 3 Unsplash search keywords for a cover image for this article.\nTitle: {title}\nOpening: {hook}\n\nReturn only a JSON array like: [\"keyword one\", \"keyword two\", \"keyword three\"]"
            }]
        )
        raw = resp.content[0].text.strip()
        raw = raw.strip('`').strip()
        if raw.startswith('json'):
            raw = raw[4:].strip()
        keywords = json.loads(raw)
        return keywords[:3]
    except Exception as e:
        print(f"[WARN] Could not get Claude keywords: {e}")
        # Fallback: use words from title
        title = article.get('title', 'product management')
        words = [w for w in title.split() if len(w) > 4][:3]
        return words or ['product management', 'technology', 'teamwork']


def find_image(article: dict) -> dict | None:
    """
    Find a relevant Unsplash image for the article.
    Returns: {url, thumb_url, photographer, photographer_url, alt, keywords}
    Returns None if no key configured or search fails.
    """
    settings = _load_settings()
    access_key = settings.get('unsplash_access_key', '').strip()

    keywords = _suggest_keywords(article)
    query = ' '.join(keywords)
    print(f"[INFO] Searching Unsplash for: {query}")

    if not access_key:
        print("[WARN] unsplash_access_key not set in config/settings.json — skipping image search")
        return {
            'url': None,
            'thumb_url': None,
            'photographer': None,
            'photographer_url': None,
            'alt': article.get('title', ''),
            'keywords': keywords,
            'missing_key': True,
        }

    try:
        resp = requests.get(
            UNSPLASH_API,
            headers={'Authorization': f'Client-ID {access_key}'},
            params={
                'query': query,
                'per_page': 5,
                'orientation': 'landscape',
                'content_filter': 'high',
            },
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get('results', [])

        if not results:
            # Try a simpler fallback query
            resp2 = requests.get(
                UNSPLASH_API,
                headers={'Authorization': f'Client-ID {access_key}'},
                params={'query': 'product management office', 'per_page': 1, 'orientation': 'landscape'},
                timeout=10,
            )
            results = resp2.json().get('results', [])

        if not results:
            print("[WARN] No Unsplash results found")
            return None

        photo = results[0]
        return {
            'url': photo['urls']['regular'],
            'full_url': photo['urls']['full'],
            'thumb_url': photo['urls']['small'],
            'photographer': photo['user']['name'],
            'photographer_url': photo['user']['links']['html'] + '?utm_source=articles_pipeline&utm_medium=referral',
            'unsplash_url': photo['links']['html'] + '?utm_source=articles_pipeline&utm_medium=referral',
            'alt': photo.get('alt_description') or article.get('title', ''),
            'keywords': keywords,
            'missing_key': False,
        }
    except Exception as e:
        print(f"[WARN] Unsplash search failed: {e}")
        return None
