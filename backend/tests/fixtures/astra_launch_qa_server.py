"""Isolated browser fixture. Bind only loopback; never proxy to a real backend."""

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = 'frontend/dist'
lock = threading.Lock()
records = {}
requests = []
drop_next = True
reject_next = False
launch_status = 'dispatched'
drop_abandon = False


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        encoded = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_POST(self):
        global drop_next, reject_next, launch_status, drop_abandon
        payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or '{}')
        if self.path == '/__qa/reset':
            with lock:
                records.clear()
                requests.clear()
                drop_next = payload.get('drop_next', True)
                reject_next = payload.get('reject_next', False)
                launch_status = payload.get('launch_status', 'dispatched')
                drop_abandon = payload.get('drop_abandon', False)
            return self.send_json({'ok': True})
        if self.path.startswith('/api/research/launch-intents/') and self.path.endswith('/abandon'):
            key = self.path.split('/')[-2]
            with lock:
                requests.append({'method': 'POST', 'path': self.path, 'key': key})
                if key not in records:
                    records[key] = {'pipeline_id': None, 'task_id': None, 'mode': None, 'status': 'cancelled',
                                    'launch_status': 'abandoned', 'replayed': True, 'recovery_required': False}
                result = dict(records[key])
                should_drop = drop_abandon
                drop_abandon = False
            if should_drop:
                self.close_connection = True
                return
            return self.send_json({'success': True, 'data': result})
        if self.path != '/api/research/run':
            return self.send_json({'success': False, 'error': 'Unexpected test POST'}, 400)
        key = self.headers.get('Idempotency-Key')
        with lock:
            requests.append({'method': 'POST', 'key': key, 'payload': payload})
            if not key:
                return self.send_json({'success': False, 'error': 'Missing key'}, 400)
            if reject_next:
                reject_next = False
                return self.send_json({'success': False, 'error': 'Fixture rejected immutable parameters'}, 400)
            if key not in records:
                records[key] = {'pipeline_id': f'pipe_qa{len(records) + 1}', 'task_id': f'qa{len(records) + 1}',
                                'mode': payload['mode'], 'status': 'running' if launch_status == 'dispatched' else 'failed', 'launch_status': launch_status,
                                'replayed': False, 'recovery_required': launch_status != 'dispatched', 'prompt': payload['prompt']}
            result = dict(records[key])
            should_drop = drop_next
            drop_next = False
        if should_drop:
            self.close_connection = True
            return
        self.send_json({'success': True, 'data': result})

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/__qa':
            return self.send_json({'fixture': 'astra-launch-offline/v1', 'requests': requests, 'records': records})
        if path.startswith('/api/'):
            with lock:
                requests.append({'method': 'GET', 'path': path})
            if path == '/api/research/preflight':
                data = {'ready': True, 'errors': []}
            elif path.startswith('/api/research/launch-intents/'):
                key = path.rsplit('/', 1)[-1]
                if key not in records:
                    return self.send_json({'success': False, 'error': 'Unknown test intent', 'code': 'launch_intent_not_found'}, 404)
                data = {**records[key], 'replayed': True}
            elif path.startswith('/api/research/status/'):
                pid = path.rsplit('/', 1)[-1]
                data = next((dict(x) for x in records.values() if x['pipeline_id'] == pid), {})
                data.update({'stages': {}, 'current_stage': 'research', 'global_progress': 1})
            elif path.endswith('/progress'):
                data = {'lines': ['Offline launch acceptance fixture.'], 'scope': 'full', 'total_exact': True, 'truncated': False, 'source_count': 1}
            elif path.endswith('/list'):
                data = {'pipelines': []}
            else:
                data = {}
            return self.send_json({'success': True, 'data': data})
        if path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return
        if not path.startswith('/assets/'):
            self.path = '/index.html'
        super().do_GET()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', default=ROOT)
    args = parser.parse_args()
    ROOT = args.directory
    ThreadingHTTPServer(('127.0.0.1', 18743), Handler).serve_forever()
