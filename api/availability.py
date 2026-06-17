# -*- coding: utf-8 -*-
"""
Vercel Serverless Function — 다우오피스 회의실 예약 현황
GET /api/availability?date=YYYY-MM-DD&room=본사_대회의실
"""

import json
import os
import requests
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE = 'https://po.dongkoo.co.kr'

ROOM_MAP = {
    '본사_대회의실':        {'assetId': '20', 'itemId': '22'},
    '본사_중회의실':        {'assetId': '20', 'itemId': '23'},
    '본사_1408호 중회의실': {'assetId': '20', 'itemId': '40'},
    '본사_1407호 중회의실': {'assetId': '20', 'itemId': '50'},
}

_H = {
    'Accept': 'application/json',
    'X-Requested-With': 'XMLHttpRequest',
    'GO-Agent': '',
    'timezoneoffset': '540',
}


def _get_session(cookie: str = None):
    s = requests.Session()
    s.headers['User-Agent'] = 'Mozilla/5.0'
    if cookie:
        s.headers['Cookie'] = cookie
        return s
    s.post(f'{BASE}/api/login', json={
        'username': os.environ.get('DAOU_ID'),
        'password': os.environ.get('DAOU_PW'),
    })
    return s


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        date = qs.get('date', [''])[0]
        room = qs.get('room', [''])[0]
        info = ROOM_MAP.get(room)

        if not date or not info:
            self._respond(400, {'error': 'date, room 파라미터 필요'})
            return

        try:
            cookie = self.headers.get('X-Daou-Session')
            sess = _get_session(cookie)
            r = sess.get(
                f"{BASE}/api/asset/20/items/daily"
                f"?fromDate={date}T00:00:00.000%2B09:00"
                f"&toDate={date}T23:59:59.000%2B09:00",
                headers=_H
            )
            if cookie and 'application/json' not in r.headers.get('Content-Type', ''):
                # 세션 만료 시 다우오피스가 로그인 페이지(HTML)를 응답함
                self._respond(401, {'error': '다우오피스 세션이 만료되었습니다. 다시 로그인해주세요.'})
                return
            all_items = r.json().get('data', []) if r.status_code == 200 else []
            result = []
            for item in all_items:
                if str(item.get('itemId')) != str(info['itemId']):
                    continue
                start_time = item.get('startTime', '')
                if not start_time.startswith(date):
                    continue
                result.append({
                    'start': start_time[11:16],
                    'end':   item['endTime'][11:16],
                    'name':  item.get('user', {}).get('name', ''),
                    'title': item.get('properties', [{}])[0].get('content', '') if item.get('properties') else '',
                })
            result.sort(key=lambda x: x['start'])
            self._respond(200, {'ok': True, 'data': result})
        except Exception as e:
            self._respond(500, {'error': str(e)})

    def _respond(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
