# -*- coding: utf-8 -*-
"""
다우오피스 회의실 예약 브리지 서버 (표준 라이브러리 전용)
실행: python scraper/server.py
기본 포트: 8765

캘린더 프론트엔드 → localhost:8765 → 다우오피스 REST API
"""

import sys
import json
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer

# pythonw.exe(콘솔 없는 모드)에서도 안전하게 동작
if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from daou_reserve import DaouReserver, ROOM_MAP

PORT = 8765
reserver = DaouReserver()


class Handler(BaseHTTPRequestHandler):

    def _respond(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _body(self):
        length = int(self.headers.get('Content-Length', 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode('utf-8'))

    def do_GET(self):
        if self.path == '/api/rooms':
            self._respond(200, list(ROOM_MAP.keys()))
        else:
            self._respond(404, {'error': 'not found'})

    def do_POST(self):
        if self.path == '/api/reserve':
            req = self._body()
            try:
                rid = reserver.create(
                    req['room'], req['title'], req['start'], req['end'],
                    req.get('user_id', '645')
                )
                print(f'[예약 생성] {req["room"]} | {req["title"]} | {req["start"]} ~ {req["end"]} → ID {rid}')
                self._respond(200, {'ok': True, 'reservation_id': rid})
            except ValueError as e:
                self._respond(400, {'ok': False, 'error': str(e)})
            except Exception as e:
                traceback.print_exc()
                self._respond(502, {'ok': False, 'error': str(e)})
        else:
            self._respond(404, {'error': 'not found'})

    def do_PUT(self):
        if self.path == '/api/reserve':
            req = self._body()
            old_id = req.get('reservation_id')
            try:
                if old_id:
                    reserver.delete(int(old_id))
                new_id = reserver.create(
                    req['room'], req['title'], req['start'], req['end'],
                    req.get('user_id', '645')
                )
                print(f'[예약 수정] ID {old_id} → {new_id} | {req["room"]}')
                self._respond(200, {'ok': True, 'reservation_id': new_id})
            except ValueError as e:
                self._respond(400, {'ok': False, 'error': str(e)})
            except Exception as e:
                traceback.print_exc()
                self._respond(502, {'ok': False, 'error': str(e)})
        else:
            self._respond(404, {'error': 'not found'})

    def do_DELETE(self):
        if self.path == '/api/reserve':
            req = self._body()
            rid = req.get('reservation_id')
            if not rid:
                self._respond(400, {'ok': False, 'error': 'reservation_id 필요'})
                return
            try:
                reserver.delete(int(rid))
                print(f'[예약 삭제] ID {rid}')
                self._respond(200, {'ok': True})
            except Exception as e:
                traceback.print_exc()
                self._respond(502, {'ok': False, 'error': str(e)})
        else:
            self._respond(404, {'error': 'not found'})

    def log_message(self, fmt, *args):
        pass

    def _print(self, msg):
        try:
            if sys.stdout:
                print(msg)
        except Exception:
            pass


if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', PORT), Handler)
    print(f'다우오피스 예약 서버 시작 → http://localhost:{PORT}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n서버 종료')
