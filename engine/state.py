"""
Atomic state file R/W — single source of truth for pipeline state.
All writes use tempfile + os.rename for atomicity.
"""

import json
import os
import tempfile
from datetime import datetime, timezone

STATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'state.json')
PUBLISHED_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'published.json')

DEFAULT_STATE = {
    "stage": "idle",
    "last_run": None,
    "no_new_episodes": False,
    "scraped_episodes": [],
    "pending_topics": [],
    "selected_topic": None,
    "draft_article": None,
    "last_error": None,
}


def _resolve(path: str) -> str:
    return os.path.abspath(path)


def load_state() -> dict:
    path = _resolve(STATE_PATH)
    if not os.path.exists(path):
        return dict(DEFAULT_STATE)
    with open(path, 'r') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return dict(DEFAULT_STATE)
    # Fill in any missing keys from defaults
    for k, v in DEFAULT_STATE.items():
        data.setdefault(k, v)
    return data


def save_state(state: dict) -> None:
    path = _resolve(STATE_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dir_ = os.path.dirname(path)
    with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False, suffix='.tmp') as f:
        json.dump(state, f, indent=2, default=str)
        tmp_path = f.name
    os.rename(tmp_path, path)


def set_stage(stage: str) -> dict:
    state = load_state()
    state['stage'] = stage
    state['last_run'] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return state


def load_published() -> list:
    path = _resolve(PUBLISHED_PATH)
    if not os.path.exists(path):
        return []
    with open(path, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def append_published(article: dict) -> None:
    path = _resolve(PUBLISHED_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    records = load_published()
    records.append(article)
    dir_ = os.path.dirname(path)
    with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False, suffix='.tmp') as f:
        json.dump(records, f, indent=2, default=str)
        tmp_path = f.name
    os.rename(tmp_path, path)


def get_recent_topics(days: int = 28) -> list[str]:
    """Return topic titles published within the last N days for dedup."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    records = load_published()
    recent = []
    for r in records:
        pub_at = r.get('published_at') or r.get('generated_at')
        if not pub_at:
            continue
        try:
            dt = datetime.fromisoformat(pub_at.replace('Z', '+00:00'))
            if dt > cutoff:
                recent.append(r.get('title', ''))
        except (ValueError, AttributeError):
            pass
    return recent
