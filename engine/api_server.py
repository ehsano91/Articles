"""
Stdlib HTTP server on port 8001.
Serves the dashboard and all pipeline API endpoints.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

# Add engine dir to path for sibling imports
sys.path.insert(0, os.path.dirname(__file__))

import state as state_module

DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), '..', 'dashboard', 'index.html')
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'settings.json')


def _load_settings() -> dict:
    try:
        with open(os.path.abspath(SETTINGS_PATH)) as f:
            return json.load(f)
    except Exception:
        return {"server_port": 8001}


def _json_response(handler, data, status=200):
    body = json.dumps(data, indent=2, default=str).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.end_headers()
    handler.wfile.write(body)


def _error(handler, msg, status=400):
    _json_response(handler, {'error': msg}, status)


def _spawn_generation(topic: dict):
    """Background thread: generate article for selected topic."""
    import claude_client
    import writer
    cur_state = state_module.load_state()
    episodes = cur_state.get('scraped_episodes', [])
    try:
        state_module.set_stage('generating')
        article = claude_client.write_article(topic, episodes)
        if not article:
            raise RuntimeError("Article generation returned None")
        writer.validate_length(article)
        # Generate tags
        try:
            tags = claude_client.generate_tags(article)
            article['tags'] = tags
            print(f"[INFO] Tags: {tags}")
        except Exception as e:
            print(f"[WARN] Tag generation failed: {e}")
        # Find cover image
        try:
            import image_finder
            image = image_finder.find_image(article)
            if image:
                article['cover_image'] = image
        except Exception as e:
            print(f"[WARN] Image search failed: {e}")
        cur_state = state_module.load_state()
        cur_state['draft_article'] = article
        cur_state['stage'] = 'awaiting_approval'
        state_module.save_state(cur_state)
        print("[INFO] Article generated — awaiting approval")
    except Exception as e:
        print(f"[ERROR] Generation failed: {e}")
        cur_state = state_module.load_state()
        cur_state['stage'] = 'awaiting_selection'
        cur_state['last_error'] = str(e)
        state_module.save_state(cur_state)


def _spawn_revision(existing_article: dict, feedback: str, topic: dict, episodes: list):
    """Background thread: revise article based on user feedback."""
    import claude_client
    import writer
    cur_state = state_module.load_state()
    try:
        cur_state['stage'] = 'generating'
        state_module.save_state(cur_state)
        article = claude_client.revise_article(existing_article, feedback, topic, episodes)
        if not article:
            raise RuntimeError("Revision returned None")
        writer.validate_length(article)
        # Preserve existing cover image or find new one
        if not article.get('cover_image') and existing_article.get('cover_image'):
            article['cover_image'] = existing_article['cover_image']
        cur_state = state_module.load_state()
        cur_state['draft_article'] = article
        cur_state['stage'] = 'awaiting_approval'
        state_module.save_state(cur_state)
        print("[INFO] Article revised — awaiting approval")
    except Exception as e:
        print(f"[ERROR] Revision failed: {e}")
        cur_state = state_module.load_state()
        cur_state['stage'] = 'awaiting_approval'
        cur_state['last_error'] = str(e)
        state_module.save_state(cur_state)


def _spawn_publish():
    """Background thread: publish approved draft."""
    import publisher
    cur_state = state_module.load_state()
    draft = cur_state.get('draft_article')
    topic = cur_state.get('selected_topic')
    if not draft:
        print("[ERROR] No draft to publish")
        return
    try:
        state_module.set_stage('publishing')
        result = publisher.publish(draft, topic)
        record = {
            **result,
            'title': draft.get('title', ''),
            'topic': topic.get('title', '') if topic else '',
            'word_count': draft.get('word_count', 0),
            'article': draft,
        }
        state_module.append_published(record)
        cur_state = state_module.load_state()
        cur_state['stage'] = 'idle'
        cur_state['draft_article'] = None
        cur_state['selected_topic'] = None
        cur_state['pending_topics'] = []
        state_module.save_state(cur_state)
        print(f"[INFO] Published via {result['published_via']}: {result.get('medium_url') or result['markdown_path']}")
    except Exception as e:
        print(f"[ERROR] Publish failed: {e}")
        cur_state = state_module.load_state()
        cur_state['stage'] = 'awaiting_approval'
        cur_state['last_error'] = str(e)
        state_module.save_state(cur_state)


class ArticlesHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Suppress default access log for cleaner output
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        if path == '' or path == '/':
            self._serve_dashboard()
        elif path == '/status':
            _json_response(self, state_module.load_state())
        elif path == '/topics':
            s = state_module.load_state()
            _json_response(self, s.get('pending_topics', []))
        elif path == '/draft':
            s = state_module.load_state()
            _json_response(self, s.get('draft_article') or {})
        elif path == '/published':
            _json_response(self, state_module.load_published())
        elif path == '/article':
            from urllib.parse import parse_qs
            params = parse_qs(urlparse(self.path).query)
            idx = params.get('id', [None])[0]
            records = state_module.load_published()
            if idx is None or not records:
                return _error(self, 'Not found', 404)
            try:
                record = records[int(idx)]
                _json_response(self, record.get('article') or {})
            except (IndexError, ValueError):
                _error(self, 'Not found', 404)
        else:
            _error(self, 'Not found', 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        body = b''
        length = int(self.headers.get('Content-Length', 0))
        if length:
            body = self.rfile.read(length)

        if path == '/select':
            try:
                data = json.loads(body) if body else {}
                topic_id = data.get('topic_id')
                if topic_id is None:
                    return _error(self, 'topic_id required')
                s = state_module.load_state()
                if s['stage'] not in ('awaiting_selection', 'idle'):
                    return _error(self, f"Cannot select in stage: {s['stage']}")
                topics = s.get('pending_topics', [])
                topic = next((t for t in topics if str(t.get('topic_id')) == str(topic_id)), None)
                if not topic:
                    return _error(self, f"Topic {topic_id} not found")
                s['selected_topic'] = topic
                state_module.save_state(s)
                t = threading.Thread(target=_spawn_generation, args=(topic,), daemon=True)
                t.start()
                _json_response(self, {'status': 'generating', 'topic': topic})
            except (json.JSONDecodeError, Exception) as e:
                _error(self, str(e))

        elif path == '/approve':
            s = state_module.load_state()
            if s['stage'] != 'awaiting_approval':
                return _error(self, f"Cannot approve in stage: {s['stage']}")
            t = threading.Thread(target=_spawn_publish, daemon=True)
            t.start()
            _json_response(self, {'status': 'publishing'})

        elif path == '/regenerate':
            s = state_module.load_state()
            if s['stage'] != 'awaiting_approval':
                return _error(self, f"Cannot regenerate in stage: {s['stage']}")
            try:
                data = json.loads(body) if body else {}
                feedback = data.get('feedback', '').strip()
                if not feedback:
                    return _error(self, 'feedback is required')
                topic = s.get('selected_topic', {})
                episodes = s.get('scraped_episodes', [])
                t = threading.Thread(
                    target=_spawn_revision,
                    args=(s.get('draft_article'), feedback, topic, episodes),
                    daemon=True,
                )
                t.start()
                _json_response(self, {'status': 'regenerating'})
            except Exception as e:
                _error(self, str(e))

        elif path == '/generate-tags':
            try:
                data = json.loads(body) if body else {}
                idx = data.get('id')
                records = state_module.load_published()
                if idx is None or int(idx) >= len(records):
                    return _error(self, 'Article not found')
                import claude_client
                article = records[int(idx)].get('article') or {}
                tags = claude_client.generate_tags(article)
                records[int(idx)]['article']['tags'] = tags
                records[int(idx)]['tags'] = tags
                import tempfile
                path_ = os.path.abspath(state_module.PUBLISHED_PATH)
                dir_ = os.path.dirname(path_)
                with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False, suffix='.tmp') as f:
                    json.dump(records, f, indent=2, default=str)
                    tmp = f.name
                os.rename(tmp, path_)
                _json_response(self, {'tags': tags})
            except Exception as e:
                _error(self, str(e))

        elif path == '/find-image':
            # Retroactively find an image for a published article
            try:
                data = json.loads(body) if body else {}
                idx = data.get('id')
                records = state_module.load_published()
                if idx is None or int(idx) >= len(records):
                    return _error(self, 'Article not found')
                import image_finder
                record = records[int(idx)]
                article = record.get('article') or {}
                image = image_finder.find_image(article)
                if not image:
                    return _error(self, 'No image found')
                records[int(idx)]['article']['cover_image'] = image
                records[int(idx)]['cover_image'] = image
                import tempfile
                path_ = os.path.abspath(state_module.PUBLISHED_PATH)
                dir_ = os.path.dirname(path_)
                with tempfile.NamedTemporaryFile('w', dir=dir_, delete=False, suffix='.tmp') as f:
                    json.dump(records, f, indent=2, default=str)
                    tmp = f.name
                os.rename(tmp, path_)
                _json_response(self, image)
            except Exception as e:
                _error(self, str(e))

        elif path == '/reject':
            s = state_module.load_state()
            if s['stage'] not in ('awaiting_approval', 'generating'):
                return _error(self, f"Cannot reject in stage: {s['stage']}")
            s['stage'] = 'awaiting_selection'
            s['draft_article'] = None
            s['last_error'] = None
            state_module.save_state(s)
            _json_response(self, {'status': 'awaiting_selection'})

        else:
            _error(self, 'Not found', 404)

    def _serve_dashboard(self):
        path = os.path.abspath(DASHBOARD_PATH)
        if not os.path.exists(path):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Dashboard not found')
            return
        with open(path, 'rb') as f:
            content = f.read()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def run(port: int = 8001):
    server = HTTPServer(('', port), ArticlesHandler)
    print(f"[INFO] Articles server running on http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Server stopped")


if __name__ == '__main__':
    settings = _load_settings()
    port = settings.get('server_port', 8001)
    run(port)
