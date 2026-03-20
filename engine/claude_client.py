"""
Claude API wrapper for theme extraction and article writing.
Uses claude-sonnet-4-6 with up to 3 retries + exponential backoff.
"""

import json
import os
import sys
import time
import re
from datetime import datetime, timezone

try:
    import anthropic
except ImportError:
    print("[ERROR] Missing dependency. Run: pip3 install anthropic")
    sys.exit(1)

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.json')


def _load_settings() -> dict:
    with open(os.path.abspath(SETTINGS_PATH)) as f:
        return json.load(f)


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=api_key)


def _call_claude(messages: list[dict], system: str, model: str, max_tokens: int = 2048) -> str:
    """Call Claude with retries. Returns response text or raises."""
    client = _get_client()
    last_err = None
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            return resp.content[0].text
        except anthropic.RateLimitError as e:
            wait = 2 ** attempt * 5
            print(f"[WARN] Rate limited, retrying in {wait}s... (attempt {attempt + 1}/3)")
            time.sleep(wait)
            last_err = e
        except anthropic.APIError as e:
            wait = 2 ** attempt * 2
            print(f"[WARN] API error: {e}, retrying in {wait}s... (attempt {attempt + 1}/3)")
            time.sleep(wait)
            last_err = e
    raise RuntimeError(f"Claude API failed after 3 attempts: {last_err}")


def _strip_code_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` wrappers."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    return text.strip()


def extract_themes(
    episodes: list[dict],
    transcripts: list[dict],
    recent_topics: list[str],
) -> list[dict]:
    """
    Given scraped episodes + transcript context, return 2-3 topic suggestions.
    Each topic: {topic_id, title, goal, source_episodes, why}
    """
    settings = _load_settings()
    model = settings.get('claude_model', 'claude-sonnet-4-6')

    episodes_text = "\n".join(
        f"- [{ep['source']}] {ep['title']}: {ep.get('description', '')[:200]}"
        for ep in episodes
    )

    transcripts_text = "\n".join(
        f"- {t['title']}" + (f" (with {t['guest']})" if t.get('guest') else '')
        + (f": {t.get('description', '')[:150]}" if t.get('description') else '')
        for t in transcripts[:15]
    )

    recent_text = "\n".join(f"- {t}" for t in recent_topics) if recent_topics else "(none yet)"

    system = (
        "You are a product management thought leader and editor. "
        "You identify compelling article topics for a broad PM audience: senior PMs, aspiring PMs, and founders. "
        "Topics should be strategic, analytical, and grounded in real practice — in the style of Lenny's Newsletter meets Melissa Perri. "
        "Always respond with valid JSON only — no markdown, no explanation."
    )

    prompt = f"""Based on the following recent podcast episodes and topics, suggest 2-3 compelling article topics for a product management audience.

RECENT PODCAST EPISODES (this week):
{episodes_text or "(no new episodes this week)"}

RECENTLY COVERED LENNY'S TRANSCRIPT THEMES (last 15 episodes):
{transcripts_text or "(none available)"}

TOPICS ALREADY WRITTEN IN LAST 28 DAYS (avoid duplicating):
{recent_text}

Return a JSON array of 2-3 topic objects. Each must have:
- topic_id: integer (1, 2, or 3)
- title: string — compelling article title
- goal: string — what the reader will learn or be able to do
- source_episodes: array of episode titles that inspired this topic
- why: string — why this topic is timely and valuable right now

Example format:
[
  {{
    "topic_id": 1,
    "title": "Why Product Managers Keep Misreading User Signals",
    "goal": "Help PMs distinguish signal from noise in qualitative research",
    "source_episodes": ["Episode title here"],
    "why": "Multiple recent episodes touch on discovery failures..."
  }}
]"""

    raw = _call_claude(
        messages=[{"role": "user", "content": prompt}],
        system=system,
        model=model,
        max_tokens=1500,
    )

    try:
        cleaned = _strip_code_fences(raw)
        topics = json.loads(cleaned)
        if not isinstance(topics, list):
            raise ValueError("Expected a JSON array")
        return topics
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[FAIL] Could not parse topics JSON: {e}\nRaw response: {raw[:500]}")
        return []


def write_article(topic: dict, episodes: list[dict]) -> dict | None:
    """
    Generate a full article for the selected topic.
    Returns: {title, hook, sections:[{heading, body}], takeaway, word_count, generated_at}
    """
    settings = _load_settings()
    model = settings.get('claude_model', 'claude-sonnet-4-6')

    episodes_text = "\n".join(
        f"- {ep['title']}: {ep.get('description', '')[:300]}"
        for ep in episodes
    )

    system = (
        "You are an expert product management writer. "
        "Your audience is broad: senior PMs, aspiring PMs, and founders. "
        "Your voice is leadership-oriented and analytical — strategic thinking combined with clear, practical frameworks. "
        "Write in the style of Lenny's Newsletter meets Melissa Perri: direct, opinionated but not preachy, "
        "framework-driven without being academic, and always grounded in real-world practice. "
        "Use third person as the default. Only use first person ('I') when sharing a specific concrete example. "
        "Avoid buzzwords, filler phrases, and vague generalisations. Every sentence should earn its place. "
        "Never use em dashes (—) or en dashes (–) anywhere in the article. Write out full sentences instead. "
        "Always respond with valid JSON only — no markdown, no explanation outside the JSON."
    )

    prompt = f"""Write a complete product management article based on the following topic brief.

TOPIC: {topic.get('title', '')}
GOAL: {topic.get('goal', '')}
WHY NOW: {topic.get('why', '')}

SOURCE EPISODES FOR INSPIRATION:
{episodes_text or "(no episode context)"}

Requirements:
- Total length: 700-850 words
- Strategic and analytical, but immediately actionable
- 3-4 body sections with clear, specific headings (not generic like "Introduction")
- No fluff, no buzzwords, no vague generalisations
- Third person default; first person only for a specific concrete example
- Written for senior PMs, aspiring PMs, and founders equally
- Style: Lenny's Newsletter meets Melissa Perri: clear, direct, framework-driven
- Never use em dashes (—) or en dashes (–). Write out full sentences instead.

Return a JSON object with:
- title: string — final article title (can refine from topic brief)
- hook: string — opening 2-3 sentences that grab the reader
- sections: array of {{heading: string, body: string}} (3-4 sections, ~150-200 words each)
- takeaway: string — 2-3 sentence conclusion/call to action
- word_count: integer — approximate total word count

Example format:
{{
  "title": "Why Product Managers Keep Misreading User Signals",
  "hook": "Every product manager has been there...",
  "sections": [
    {{"heading": "The Signal/Noise Problem", "body": "..."}},
    {{"heading": "What Users Say vs. What They Do", "body": "..."}},
    {{"heading": "A Better Framework", "body": "..."}},
    {{"heading": "Putting It Into Practice", "body": "..."}}
  ],
  "takeaway": "The best PMs treat user research as...",
  "word_count": 780
}}"""

    raw = _call_claude(
        messages=[{"role": "user", "content": prompt}],
        system=system,
        model=model,
        max_tokens=3000,
    )

    try:
        cleaned = _strip_code_fences(raw)
        article = json.loads(cleaned)
        article['generated_at'] = datetime.now(timezone.utc).isoformat()
        return article
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[FAIL] Could not parse article JSON: {e}\nRaw response: {raw[:500]}")
        return None


def revise_article(existing_article: dict, feedback: str, topic: dict, episodes: list[dict]) -> dict | None:
    """
    Revise an existing article based on user feedback.
    Returns a new article dict with the same structure.
    """
    settings = _load_settings()
    model = settings.get('claude_model', 'claude-sonnet-4-6')

    import json as _json
    existing_text = _json.dumps(existing_article, indent=2)

    system = (
        "You are an expert product management writer. "
        "Your audience is broad: senior PMs, aspiring PMs, and founders. "
        "Your voice is leadership-oriented and analytical — strategic thinking combined with clear, practical frameworks. "
        "Write in the style of Lenny's Newsletter meets Melissa Perri: direct, opinionated but not preachy, "
        "framework-driven without being academic, and always grounded in real-world practice. "
        "Use third person as the default. Only use first person ('I') when sharing a specific concrete example. "
        "Avoid buzzwords, filler phrases, and vague generalisations. Every sentence should earn its place. "
        "Never use em dashes (—) or en dashes (–). Write out full sentences instead. "
        "Always respond with valid JSON only — no markdown, no explanation outside the JSON."
    )

    prompt = f"""Revise the following product management article based on the feedback provided.

FEEDBACK FROM AUTHOR:
{feedback}

CURRENT ARTICLE (JSON):
{existing_text}

Apply the feedback carefully. Keep what is working. Fix what the author flagged.
Maintain the same JSON structure and all the same style rules as the original.
Never use em dashes or en dashes. Write out full sentences instead.

Return the revised article as a JSON object with the same fields:
title, hook, sections (array of heading+body), takeaway, word_count."""

    raw = _call_claude(
        messages=[{"role": "user", "content": prompt}],
        system=system,
        model=model,
        max_tokens=3000,
    )

    try:
        cleaned = _strip_code_fences(raw)
        article = json.loads(cleaned)
        article['generated_at'] = datetime.now(timezone.utc).isoformat()
        return article
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[FAIL] Could not parse revised article JSON: {e}\nRaw response: {raw[:500]}")
        return None
