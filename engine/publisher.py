"""
Publish articles to Medium. Falls back to local markdown if Medium unavailable.
Always saves a local markdown copy regardless.
"""

import json
import os
import sys
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("[ERROR] Missing dependency. Run: pip3 install requests")
    sys.exit(1)

from writer import save_markdown

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.json')

MEDIUM_API_BASE = "https://api.medium.com/v1"


def _load_settings() -> dict:
    with open(os.path.abspath(SETTINGS_PATH)) as f:
        return json.load(f)


def get_medium_user_id(token: str) -> str | None:
    """GET /v1/me, returns data.id or None on failure."""
    try:
        resp = requests.get(
            f"{MEDIUM_API_BASE}/me",
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()['data']['id']
    except Exception as e:
        print(f"[WARN] Could not get Medium user ID: {e}")
        return None


def publish_to_medium(article: dict, token: str, author_id: str) -> dict | None:
    """
    POST to /v1/users/{id}/posts with markdown content.
    Returns Medium post data dict or None on failure.
    """
    settings = _load_settings()
    from writer import assemble_markdown
    markdown_content = assemble_markdown(article)

    payload = {
        "title": article.get('title', 'Untitled'),
        "contentFormat": "markdown",
        "content": markdown_content,
        "publishStatus": settings.get('medium_publish_status', 'draft'),
        "tags": settings.get('medium_tags', ['product-management']),
    }

    try:
        resp = requests.post(
            f"{MEDIUM_API_BASE}/users/{author_id}/posts",
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get('data', {})
        return {
            'medium_url': data.get('url', ''),
            'medium_id': data.get('id', ''),
        }
    except Exception as e:
        print(f"[WARN] Medium publish failed: {e}")
        return None


def publish(article: dict, topic: dict | None = None) -> dict:
    """
    Main publish entry point.
    1. Always save local markdown copy.
    2. If Medium token set, try to publish there.
    3. Return PublishResult dict.
    """
    settings = _load_settings()
    token = settings.get('medium_integration_token', '').strip()
    author_id = settings.get('medium_author_id', '').strip()

    # Always save local markdown first
    markdown_path = save_markdown(article, topic)

    published_at = datetime.now(timezone.utc).isoformat()
    result = {
        'medium_url': None,
        'markdown_path': markdown_path,
        'published_via': 'local',
        'published_at': published_at,
        'title': article.get('title', ''),
    }

    if not token:
        print("[WARN] Medium token not configured — saved locally only")
        return result

    # Resolve author_id if not cached
    if not author_id:
        author_id = get_medium_user_id(token)
        if not author_id:
            print("[WARN] Medium unavailable (could not get user ID), falling back to local")
            return result

    medium_data = publish_to_medium(article, token, author_id)
    if medium_data:
        result['medium_url'] = medium_data.get('medium_url')
        result['published_via'] = 'medium'
        print(f"[INFO] Published to Medium: {result['medium_url']}")
    else:
        print("[WARN] Medium unavailable, falling back to local markdown")

    return result
