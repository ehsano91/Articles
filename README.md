# Weekly Product Article Writing Tool

Automated pipeline that scrapes product podcasts, uses Claude to suggest article topics, waits for human selection, generates a full article, and publishes to Medium — with a local web dashboard as the human review layer.

## Pipeline

```
idle → scraping → awaiting_selection → generating → awaiting_approval → publishing → idle
```

## Setup

1. Install dependencies:
   ```bash
   pip3 install requests beautifulsoup4 anthropic PyYAML
   ```

2. Configure API keys in `config/settings.json`:
   ```json
   {
     "claude_model": "claude-sonnet-4-6",
     "medium_integration_token": "YOUR_MEDIUM_TOKEN",
     "medium_author_id": "",
     "medium_publish_status": "draft",
     "medium_tags": ["product-management"],
     "transcript_context_count": 15,
     "dedup_window_days": 28,
     "server_port": 8001
   }
   ```
   Also set `ANTHROPIC_API_KEY` environment variable.

3. Install launchd services (runs weekly on Fridays at 09:00):
   ```bash
   python3 engine/scheduler.py --install
   ```

## Manual Usage

```bash
# Run the scraping + topic suggestion pipeline
python3 engine/scheduler.py --run

# Select a topic by number (1, 2, or 3)
python3 engine/scheduler.py --select 1

# Approve the draft for publishing
python3 engine/scheduler.py --approve

# Reject the draft and go back to topic selection
python3 engine/scheduler.py --reject
```

## Dashboard

Open `http://localhost:8001` in your browser after starting the server.

```bash
python3 engine/api_server.py
```

## Project Structure

```
engine/         Backend modules
dashboard/      Web UI (single index.html, no npm)
config/         Sources + settings (settings.json is gitignored)
data/           Runtime state (all gitignored)
launchd/        Generated plists (gitignored)
```
