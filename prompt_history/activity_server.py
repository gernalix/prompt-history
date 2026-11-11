"""Loopback-only dashboard, also suitable behind Tailscale Serve."""
from __future__ import annotations
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .activity import aggregate, summarize

STATIC = Path(__file__).with_name('activity_static')


class Cache:
    def __init__(self, db, threads):
        self.db, self.threads = db, threads
        self.data = None
        self.error = False
        self.lock = threading.Lock()

    def refresh(self):
        while True:
            try:
                data = aggregate(self.db, self.threads)
                with self.lock:
                    self.data, self.error = data, False
            except Exception:
                # Do not log SQL, private paths, or source payloads.
                with self.lock:
                    self.error = True
            time.sleep(900)


def handler(cache):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            path = url.path
            # Tailscale path prefixes may be stripped by the proxy.
            if path.startswith('/activity/'):
                path = path[len('/activity'):]
            status = 200
            kind = 'text/html; charset=utf-8'
            if path == '/api/activity':
                kind = 'application/json; charset=utf-8'
                with cache.lock:
                    data, error = cache.data, cache.error
                if data is None:
                    status = 503 if error else 202
                    body = json.dumps({'status': 'error' if error else 'loading'}).encode()
                else:
                    try:
                        query = parse_qs(url.query)
                        result = summarize(data, query.get('period',['30'])[0], query.get('author',['both'])[0], query.get('metric',['characters'])[0])
                        result['refresh_error'] = error
                        body = json.dumps(result).encode()
                    except ValueError:
                        status, body = 400, b'{"status":"invalid_filter"}'
            elif path in ('/', '/index.html', '/app.js', '/style.css'):
                name = 'index.html' if path == '/' else path[1:]
                kind = {'app.js':'text/javascript; charset=utf-8', 'style.css':'text/css; charset=utf-8'}.get(name, kind)
                body = (STATIC / name).read_bytes()
            else:
                status, body = 404, b'Not found'
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default='~/.local/share/prompt-history/prompt_history.sqlite')
    parser.add_argument('--threads', default='~/.codex/state_5.sqlite')
    parser.add_argument('--port', type=int, default=47831)
    args = parser.parse_args()
    cache = Cache(args.db, args.threads)
    threading.Thread(target=cache.refresh, daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1', args.port), handler(cache)).serve_forever()


if __name__ == '__main__':
    main()
