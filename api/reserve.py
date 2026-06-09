# -*- coding: utf-8 -*-
"""
Vercel Serverless Function — 다우오피스 회의실 예약
POST   /api/reserve  → 예약 생성   body: {room, title, start, end}
PUT    /api/reserve  → 예약 수정   body: {reservation_id, room, title, start, end}
DELETE /api/reserve  → 예약 삭제   body: {reservation_id}

환경변수 (Vercel Dashboard → Settings → Environment Variables):
  DAOU_ID       다우오피스 아이디 (예: 20230005)
  DAOU_PW       다우오피스 비밀번호
  DAOU_USER_ID  다우오피스 사용자 ID 숫자 (기본값: 645)
"""

import json
import os
import requests
from http.server import BaseHTTPRequestHandler

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


def _login() -> requests.Session:
    sess = requests.Session()
    sess.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    r = sess.post(f'{BASE}/api/login', json={
        'username': os.environ.get('DAOU_ID'),
        'password': os.environ.get('DAOU_PW'),
    })
    if r.status_code != 200:
        raise RuntimeError(f'다우오피스 로그인 실패 ({r.status_code})')
    return sess


def _to_kst(s: str) -> str:
    s = s.replace(' ', 'T')
    if len(s) == 16:
        s += ':00'
    return s[:19] + '.000+09:00'


def _create(sess: requests.Session, room: str, title: str, start: str, end: str, user_id: str = None) -> int:
    info = ROOM_MAP.get(room)
    if not info:
        raise ValueError(f'알 수 없는 회의실: {room}')
    payload = {
        'assetId':    info['assetId'],
        'itemId':     info['itemId'],
        'type':       'reserve',
        'startTime':  _to_kst(start),
        'endTime':    _to_kst(end),
        'useAnonym':  False,
        'user':       {'id': user_id or os.environ.get('DAOU_USER_ID', '645')},
        'properties': [{'attributeId': '33', 'content': title}],
        'allday':     False,
    }
    r = sess.post(
        f"{BASE}/api/asset/{info['assetId']}/item/{info['itemId']}/reserve",
        json=payload, headers=_DAOU_HEADERS
    )
    if r.status_code == 500:
        raise RuntimeError(f'해당 시간에 이미 예약이 있습니다 ({room})')
    if r.status_code != 200:
        raise RuntimeError(f'예약 실패 ({r.status_code}): {r.text[:100]}')
    return r.json()['data']['id']


def _delete(sess: requests.Session, reservation_id: int) -> None:
    r = sess.delete(
        f'{BASE}/api/asset/item/reservation',
        json={'ids': [reservation_id]},
        headers=_DAOU_HEADERS
    )
    if r.status_code != 200:
        raise RuntimeError(f'예약 삭제 실패 ({r.status_code})')


class handler(BaseHTTPRequestHandler):

    def _body(self) -> dict:
        length = int(self.headers.get('Content-Length', 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def _ok(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, msg: str) -> None:
        body = json.dumps({'ok': False, 'error': msg}, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = self._body()
        user_id = req.get('userId') or os.environ.get('DAOU_USER_ID', '645')
        try:
            sess = _login()
            rid = _create(sess, req['room'], req['title'], req['start'], req['end'], user_id)
            self._ok({'ok': True, 'reservation_id': rid})
        except (ValueError, RuntimeError) as e:
            self._err(502, str(e))
        except Exception as e:
            self._err(500, str(e))

    def do_PUT(self):
        req = self._body()
        old_id = req.get('reservation_id')
        user_id = req.get('userId') or os.environ.get('DAOU_USER_ID', '645')
        try:
            sess = _login()
            if old_id:
                _delete(sess, int(old_id))
            new_id = _create(sess, req['room'], req['title'], req['start'], req['end'], user_id)
            self._ok({'ok': True, 'reservation_id': new_id})
        except (ValueError, RuntimeError) as e:
            self._err(502, str(e))
        except Exception as e:
            self._err(500, str(e))

    def do_DELETE(self):
        req = self._body()
        rid = req.get('reservation_id')
        if not rid:
            self._err(400, 'reservation_id 필요')
            return
        try:
            sess = _login()
            _delete(sess, int(rid))
            self._ok({'ok': True})
        except Exception as e:
            self._err(502, str(e))
