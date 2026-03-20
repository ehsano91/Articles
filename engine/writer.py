"""
Assemble, validate, and save markdown articles.
"""

import os
import re
from datetime import datetime, timezone

ARTICLES_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'articles')


def _slugify(title: str) -> str:
    """Convert title to filesystem-safe slug."""
    slug = title.lower()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')[:60]


def assemble_markdown(article: dict) -> str:
    """Build full markdown string with YAML frontmatter."""
    title = article.get('title', 'Untitled')
    hook = article.get('hook', '')
    sections = article.get('sections', [])
    takeaway = article.get('takeaway', '')
    generated_at = article.get('generated_at', datetime.now(timezone.utc).isoformat())
    word_count = article.get('word_count', 0)

    lines = [
        '---',
        f'title: "{title}"',
        f'generated_at: "{generated_at}"',
        f'word_count: {word_count}',
        '---',
        '',
        f'# {title}',
        '',
        hook,
        '',
    ]

    for section in sections:
        heading = section.get('heading', '')
        body = section.get('body', '')
        if heading:
            lines.append(f'## {heading}')
            lines.append('')
        if body:
            lines.append(body)
            lines.append('')

    if takeaway:
        lines.append('## Takeaway')
        lines.append('')
        lines.append(takeaway)
        lines.append('')

    return '\n'.join(lines)


def validate_length(article: dict) -> bool:
    """
    Check if article is within 600–900 words.
    Logs [WARN] if out of range but always returns True (non-blocking).
    """
    text = assemble_markdown(article)
    word_count = len(text.split())
    stated_count = article.get('word_count', word_count)

    if word_count < 600:
        print(f"[WARN] Article is short: ~{word_count} words (target: 600-900)")
    elif word_count > 900:
        print(f"[WARN] Article is long: ~{word_count} words (target: 600-900)")
    else:
        print(f"[INFO] Article length OK: ~{word_count} words")

    return True  # Non-blocking


def save_markdown(article: dict, topic: dict | None = None) -> str:
    """
    Save article to data/articles/YYYY-MM-DD-{slug}.md
    Returns the file path.
    """
    os.makedirs(os.path.abspath(ARTICLES_DIR), exist_ok=True)

    title = article.get('title', 'untitled')
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    slug = _slugify(title)
    filename = f"{date_str}-{slug}.md"
    path = os.path.join(os.path.abspath(ARTICLES_DIR), filename)

    content = assemble_markdown(article)
    with open(path, 'w') as f:
        f.write(content)

    print(f"[INFO] Saved markdown: {path}")
    return path
