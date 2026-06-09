# -*- coding: utf-8 -*-
"""
다우오피스 회의실 예약 브리지 서버 (HTTP 로그인, 세션 캐시)
실행: python scraper/server.py  |  포트: 8765
"""

import sys
import json
import traceback
import os
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv

if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

PORT = 8765
BASE = 'https://po.dongkoo.co.kr'

ROOM_MAP = {
    '본사_대회의실':        {'assetId': '20', 'itemId': '22'},
    '본사_중회의실':        {'assetId': '20', 'itemId': '23'},
    '본사_1408호 중회의실': {'assetId': '20', 'itemId': '40'},
    '본사_1407호 중회의실': {'assetId': '20', 'itemId': '50'},
}

_DAOU_HEADERS = {
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Content-Type': 'application/json',
    'X-Requested-With': 'XMLHttpRequest',
    'GO-Agent': '',
    'timezoneoffset': '540',
}

_sess: requests.Session | None = None


def _get_sess() -> requests.Session:
    global _sess
    if _sess is None:
        _sess = _login()
    return _sess


def _login() -> requests.Session:
    s = requests.Session()
    s.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    r = s.post(f'{BASE}/api/login', json={
        'username': os.getenv('DAOU_ID'),
        'password': os.getenv('DAOU_PW'),
    })
    if r.status_code != 200:
        raise RuntimeError(f'다우오피스 로그인 실패 ({r.status_code})')
    return s


def _req(method, path, **kwargs):
    global _sess
    s = _get_sess()
    r = s.request(method, BASE + path, headers=_DAOU_HEADERS, **kwargs)
    if r.status_code in (401, 403):
        _sess = _login()
        r = _sess.request(method, BASE + path, headers=_DAOU_HEADERS, **kwargs)
    return r


def _to_kst(s):
    s = s.replace(' ', 'T')
    if len(s) == 16:
        s += ':00'
    return s[:19] + '.000+09:00'


def _create(room, title, start, end):
    info = ROOM_MAP.get(room)
    if not info:
        raise ValueError(f'알 수 없는 회의실: {room}')
    payload = {
        'assetId': info['assetId'], 'itemId': info['itemId'],
        'type': 'reserve',
        'startTime': _to_kst(start), 'endTime': _to_kst(end),
        'useAnonym': False,
        'user': {'id': os.getenv('DAOU_USER_ID', '645')},
        'properties': [{'attributeId': '33', 'content': title}],
        'allday': False,
    }
    r = _req('POST', f"/api/asset/{info['assetId']}/item/{info['itemId']}/reserve", json=payload)
    if r.status_code == 500:
        raise RuntimeError(f'해당 시간에 이미 예약이 있습니다 ({room})')
    if r.status_code != 200:
        raise RuntimeError(f'예약 실패 ({r.status_code}): {r.text[:100]}')
    return r.json()['data']['id']


def _delete(reservation_id):
    r = _req('DELETE', '/api/asset/item/reservation', json={'ids': [int(reservation_id)]})
    if r.status_code != 200:
        raise RuntimeError(f'예약 삭제 실패 ({r.status_code})')


def _availability(date_str, item_id):
    """YYYY-MM-DD 날짜의 특정 회의실 예약 목록 반환."""
    url = (f'/api/asset/20/items/daily'
           f'?fromDate={date_str}T00:00:00.000%2B09:00'
           f'&toDate={date_str}T23:59:59.000%2B09:00')
    r = _req('GET', url)
    if r.status_code != 200:
        return []
    all_items = r.json().get('data', [])
    result = []
    for item in all_items:
        if str(item.get('itemId')) != str(item_id):
            continue
        start_time = item.get('startTime', '')
        if not start_time.startswith(date_str):
            continue
        result.append({
            'start': start_time[11:16],
            'end':   item['endTime'][11:16],
            'name':  item.get('user', {}).get('name', ''),
            'title': item.get('properties', [{}])[0].get('content', '') if item.get('properties') else '',
        })
    return sorted(result, key=lambda x: x['start'])


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
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == '/api/rooms':
            self._respond(200, list(ROOM_MAP.keys()))
        elif parsed.path == '/api/availability':
            date = qs.get('date', [''])[0]
            room = qs.get('room', [''])[0]
            info = ROOM_MAP.get(room)
            if not date or not info:
                self._respond(400, {'error': 'date, room 필요'})
                return
            try:
                items = _availability(date, info['itemId'])
                self._respond(200, {'ok': True, 'data': items})
            except Exception as e:
                self._respond(502, {'ok': False, 'error': str(e)})
        else:
            self._respond(404, {'error': 'not found'})

    def do_POST(self):
        if self.path == '/api/reserve':
            req = self._body()
            try:
                rid = _create(req['room'], req['title'], req['start'], req['end'])
                self._respond(200, {'ok': True, 'reservation_id': rid})
            except (ValueError, RuntimeError) as e:
                self._respond(502, {'ok': False, 'error': str(e)})
            except Exception as e:
                traceback.print_exc()
                self._respond(500, {'ok': False, 'error': str(e)})
        else:
            self._respond(404, {'error': 'not found'})

    def do_PUT(self):
        if self.path == '/api/reserve':
            req = self._body()
            old_id = req.get('reservation_id')
            try:
                if old_id:
                    _delete(old_id)
                new_id = _create(req['room'], req['title'], req['start'], req['end'])
                self._respond(200, {'ok': True, 'reservation_id': new_id})
            except (ValueError, RuntimeError) as e:
                self._respond(502, {'ok': False, 'error': str(e)})
            except Exception as e:
                traceback.print_exc()
                self._respond(500, {'ok': False, 'error': str(e)})
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
                _delete(rid)
                self._respond(200, {'ok': True})
            except Exception as e:
                traceback.print_exc()
                self._respond(502, {'ok': False, 'error': str(e)})
        else:
            self._respond(404, {'error': 'not found'})

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', PORT), Handler)
    print(f'다우오피스 예약 서버 시작 → http://localhost:{PORT}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n서버 종료')
