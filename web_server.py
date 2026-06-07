"""
Web Sunucusu — UI ile CommController arasında köprü görevi görür.
Ek bağımlılık gerektirmez (Python stdlib).
API uç noktaları:
  GET  /             → UI ana sayfasını sunar
  GET  /api/status   → Anlık sistem durumu (port, protokol, gecikme vb.)
  GET  /api/messages?after=N → N indeksinden sonraki mesajları döner
  GET  /api/logs     → Port zıplama kayıtlarını döner
  POST /api/send     → Mesaj gönderir
"""
import http.server
import json
import os
import threading
from urllib.parse import urlparse, parse_qs

# UI dosyalarının bulunduğu klasör
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui')

# MIME türleri
CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.ico': 'image/x-icon',
}


def create_handler(comm, peer_id, bootstrap_params):
    """CommController referansını içeren HTTP handler sınıfı üretir."""

    class Handler(http.server.BaseHTTPRequestHandler):

        def log_message(self, fmt, *args):
            pass  # HTTP loglarını bastır (session.log karışmasın)

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path

            if path == '/api/status':
                state = comm.get_state()
                state['peer_id'] = peer_id
                state['interval'] = bootstrap_params.get('timing_interval', 10)
                state['port_range_min'] = bootstrap_params.get('port_range_min', 20000)
                state['port_range_max'] = bootstrap_params.get('port_range_max', 30000)
                self._send_json(state)

            elif path == '/api/messages':
                qs = parse_qs(parsed.query)
                after = int(qs.get('after', ['0'])[0])
                with comm.lock:
                    messages = list(comm.all_messages[after:])
                    total = len(comm.all_messages)
                self._send_json({'messages': messages, 'total': total})

            elif path == '/api/logs':
                with comm.lock:
                    logs = list(comm.port_switch_log)
                self._send_json({'logs': logs})

            else:
                self._serve_static(path)

        def do_POST(self):
            if self.path == '/api/send':
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode('utf-8'))
                text = data.get('text', '').strip()
                if text:
                    comm.send_message(text)
                self._send_json({'ok': True})
            elif self.path == '/api/config':
                length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(length)
                data = json.loads(body.decode('utf-8'))
                changes = data.get('changes', {})
                if changes:
                    effective_step = comm.send_config_update(changes)
                    if effective_step is not None:
                        self._send_json({'ok': True, 'effective_step': effective_step})
                    else:
                        self._send_json({'ok': False, 'error': 'Invalid config changes'})
                else:
                    self._send_json({'ok': False, 'error': 'No changes provided'})
            else:
                self.send_error(404)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()

        # ---- Yardımcı Metotlar ----

        def _send_json(self, data):
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', len(body))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)

        def _serve_static(self, path):
            if path in ('/', ''):
                path = '/index.html'
            # Cache-busting sorgu parametrelerini yoksay (?v=4 gibi)
            clean = path.split('?')[0]
            file_path = os.path.join(UI_DIR, clean.lstrip('/'))

            if not os.path.isfile(file_path):
                self.send_error(404)
                return

            ext = os.path.splitext(file_path)[1]
            ct = CONTENT_TYPES.get(ext, 'application/octet-stream')

            with open(file_path, 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)

    return Handler


def start_web_server(comm, peer_id, bootstrap_params, port=8080):
    """Web sunucusunu arka plan iş parçacığında başlatır."""
    handler = create_handler(comm, peer_id, bootstrap_params)
    server = http.server.HTTPServer(('0.0.0.0', port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
