import json
import urllib.parse
from http.server import BaseHTTPRequestHandler
import holidays as holidays_lib

APP_TOKEN = 'dkbio-cal-2026'

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.headers.get('x-app-token', '') != APP_TOKEN:
            self._send(401, {'error': 'unauthorized'})
            return

        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            year = int(params.get('year', ['2026'])[0])
            if year < 2020 or year > 2099:
                raise ValueError
        except (ValueError, IndexError):
            self._send(400, {'error': '올바른 연도를 입력하세요'})
            return

        try:
            try:
                kr = holidays_lib.country_holidays('KR', years=year)
            except Exception:
                kr = holidays_lib.KR(years=year)
            result = [{'date': str(d), 'name': n} for d, n in sorted(kr.items())]
            self._send(200, result)
        except Exception as e:
            self._send(500, {'error': str(e)})

    def _send(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
