"""
Pipeline runner and launchd installer.

Usage:
  python3 engine/scheduler.py --run       # Scrape + suggest topics
  python3 engine/scheduler.py --select 1  # Generate article for topic N
  python3 engine/scheduler.py --approve   # Publish approved draft
  python3 engine/scheduler.py --reject    # Clear draft, back to topic selection
  python3 engine/scheduler.py --install   # Write + load launchd plists
"""

import argparse
import json
import os
import subprocess
import sys
import time

# Add engine dir to path for sibling imports
ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE_DIR)

PROJECT_DIR = os.path.dirname(ENGINE_DIR)
LAUNCHD_DIR = os.path.join(PROJECT_DIR, 'launchd')
SETTINGS_PATH = os.path.join(PROJECT_DIR, 'config', 'settings.json')


def _load_settings() -> dict:
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def cmd_run():
    """Scrape episodes, fetch transcripts, generate topics, save to state."""
    import state as state_module
    import scraper
    import transcript
    import claude_client

    settings = _load_settings()
    dedup_days = settings.get('dedup_window_days', 28)
    n_transcripts = settings.get('transcript_context_count', 15)

    print("[INFO] Stage: scraping")
    state_module.set_stage('scraping')
    cur = state_module.load_state()

    # Scrape episodes
    episodes = scraper.scrape_all()
    seen_urls = [e.get('url', '') for e in cur.get('scraped_episodes', [])]
    new_eps = scraper.filter_new_episodes(episodes, seen_urls)
    print(f"[INFO] New episodes this week: {len(new_eps)}")

    if not new_eps:
        cur = state_module.load_state()
        cur['no_new_episodes'] = True
        cur['stage'] = 'idle'
        state_module.save_state(cur)
        print("[INFO] No new episodes — nothing to process this week.")
        return

    # Fetch transcripts
    print("[INFO] Fetching recent transcripts...")
    transcripts = transcript.fetch_recent_transcripts(n_transcripts)
    themes = transcript.extract_covered_themes(transcripts)

    # Get recent published topics for dedup
    recent_topics = state_module.get_recent_topics(dedup_days)
    print(f"[INFO] Dedup: {len(recent_topics)} topics from last {dedup_days} days")

    # Generate topic suggestions
    print("[INFO] Asking Claude for topic suggestions...")
    topics = claude_client.extract_themes(new_eps, transcripts, recent_topics)

    if not topics:
        print("[ERROR] No topics returned from Claude")
        cur = state_module.load_state()
        cur['stage'] = 'idle'
        cur['last_error'] = 'Claude returned no topics'
        state_module.save_state(cur)
        return

    # Save to state
    cur = state_module.load_state()
    cur['stage'] = 'awaiting_selection'
    cur['no_new_episodes'] = False
    cur['scraped_episodes'] = new_eps
    cur['pending_topics'] = topics
    cur['draft_article'] = None
    cur['selected_topic'] = None
    cur['last_error'] = None
    state_module.save_state(cur)

    print(f"\n[INFO] {len(topics)} topic(s) ready. Dashboard: http://localhost:8001\n")
    print("Topics:")
    for t in topics:
        print(f"  [{t['topic_id']}] {t['title']}")
        print(f"       {t.get('why', '')[:100]}")
    print("\nSelect with: python3 engine/scheduler.py --select <N>")


def cmd_select(topic_id: int):
    """Generate article for the given topic ID."""
    import state as state_module
    import claude_client
    import writer

    cur = state_module.load_state()
    topics = cur.get('pending_topics', [])
    topic = next((t for t in topics if str(t.get('topic_id')) == str(topic_id)), None)
    if not topic:
        print(f"[ERROR] Topic {topic_id} not found. Available: {[t['topic_id'] for t in topics]}")
        sys.exit(1)

    print(f"[INFO] Selected: {topic['title']}")
    cur['selected_topic'] = topic
    state_module.save_state(cur)

    state_module.set_stage('generating')
    episodes = cur.get('scraped_episodes', [])

    print("[INFO] Generating article with Claude...")
    article = claude_client.write_article(topic, episodes)
    if not article:
        print("[ERROR] Article generation failed")
        sys.exit(1)

    writer.validate_length(article)

    cur = state_module.load_state()
    cur['draft_article'] = article
    cur['stage'] = 'awaiting_approval'
    state_module.save_state(cur)

    print(f"\n[INFO] Draft ready: \"{article.get('title', '')}\"\n")
    print(f"Review at: http://localhost:8001\n")
    print("Approve: python3 engine/scheduler.py --approve")
    print("Reject:  python3 engine/scheduler.py --reject")


def cmd_approve():
    """Publish the approved draft."""
    import state as state_module
    import publisher

    cur = state_module.load_state()
    if cur['stage'] != 'awaiting_approval':
        print(f"[ERROR] No draft to approve (stage: {cur['stage']})")
        sys.exit(1)

    draft = cur.get('draft_article')
    topic = cur.get('selected_topic')
    if not draft:
        print("[ERROR] No draft article in state")
        sys.exit(1)

    state_module.set_stage('publishing')
    result = publisher.publish(draft, topic)

    record = {
        **result,
        'title': draft.get('title', ''),
        'topic': topic.get('title', '') if topic else '',
        'word_count': draft.get('word_count', 0),
    }
    state_module.append_published(record)

    cur = state_module.load_state()
    cur['stage'] = 'idle'
    cur['draft_article'] = None
    cur['selected_topic'] = None
    cur['pending_topics'] = []
    state_module.save_state(cur)

    print(f"\n[INFO] Published via {result['published_via']}")
    if result.get('medium_url'):
        print(f"  Medium: {result['medium_url']}")
    print(f"  Local:  {result['markdown_path']}")


def cmd_reject():
    """Clear draft, go back to topic selection."""
    import state as state_module

    cur = state_module.load_state()
    cur['stage'] = 'awaiting_selection'
    cur['draft_article'] = None
    cur['last_error'] = None
    state_module.save_state(cur)
    print("[INFO] Draft rejected. Pick a different topic at http://localhost:8001")


def cmd_install():
    """Write and load launchd plists for scheduler and server."""
    os.makedirs(LAUNCHD_DIR, exist_ok=True)
    python = sys.executable
    launch_agents = os.path.expanduser('~/Library/LaunchAgents')
    os.makedirs(launch_agents, exist_ok=True)

    # --- Scheduler plist (Friday 09:00) ---
    scheduler_label = 'com.articles.scheduler'
    scheduler_plist_path = os.path.join(LAUNCHD_DIR, f'{scheduler_label}.plist')
    scheduler_plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{scheduler_label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{os.path.join(ENGINE_DIR, 'scheduler.py')}</string>
    <string>--run</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{PROJECT_DIR}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key>
    <integer>5</integer>
    <key>Hour</key>
    <integer>9</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{os.path.join(PROJECT_DIR, 'data', 'scheduler.log')}</string>
  <key>StandardErrorPath</key>
  <string>{os.path.join(PROJECT_DIR, 'data', 'scheduler.err')}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHROPIC_API_KEY</key>
    <string>{os.environ.get('ANTHROPIC_API_KEY', '')}</string>
  </dict>
</dict>
</plist>"""

    with open(scheduler_plist_path, 'w') as f:
        f.write(scheduler_plist)

    # --- Server plist (always-on) ---
    server_label = 'com.articles.server'
    server_plist_path = os.path.join(LAUNCHD_DIR, f'{server_label}.plist')
    server_plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{server_label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{os.path.join(ENGINE_DIR, 'api_server.py')}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{PROJECT_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{os.path.join(PROJECT_DIR, 'data', 'server.log')}</string>
  <key>StandardErrorPath</key>
  <string>{os.path.join(PROJECT_DIR, 'data', 'server.err')}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHROPIC_API_KEY</key>
    <string>{os.environ.get('ANTHROPIC_API_KEY', '')}</string>
  </dict>
</dict>
</plist>"""

    with open(server_plist_path, 'w') as f:
        f.write(server_plist)

    # Symlink into LaunchAgents and load
    for label, plist_path in [
        (scheduler_label, scheduler_plist_path),
        (server_label, server_plist_path),
    ]:
        dest = os.path.join(launch_agents, f'{label}.plist')
        # Unload if already loaded
        subprocess.run(['launchctl', 'unload', dest], capture_output=True)
        if os.path.lexists(dest):
            os.remove(dest)
        os.symlink(plist_path, dest)
        result = subprocess.run(['launchctl', 'load', dest], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[INFO] Loaded: {label}")
        else:
            print(f"[WARN] Load may have failed for {label}: {result.stderr.strip()}")

    print(f"\n[INFO] Scheduler: Fridays at 09:00")
    print(f"[INFO] Server:    http://localhost:8001 (always-on)")
    print(f"\nPlist files: {LAUNCHD_DIR}/")


def main():
    parser = argparse.ArgumentParser(description='Articles pipeline scheduler')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--run', action='store_true', help='Scrape + generate topic suggestions')
    group.add_argument('--select', type=int, metavar='N', help='Select topic N and generate article')
    group.add_argument('--approve', action='store_true', help='Approve and publish draft')
    group.add_argument('--reject', action='store_true', help='Reject draft, return to topic selection')
    group.add_argument('--install', action='store_true', help='Install launchd services')
    args = parser.parse_args()

    if args.run:
        cmd_run()
    elif args.select is not None:
        cmd_select(args.select)
    elif args.approve:
        cmd_approve()
    elif args.reject:
        cmd_reject()
    elif args.install:
        cmd_install()


if __name__ == '__main__':
    main()
