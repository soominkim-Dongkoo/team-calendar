# -*- coding: utf-8 -*-
"""
Vercel Serverless Function — 다우오피스 로그인 (세션 쿠키 반환)
POST /api/daou_login  body: {username, password}
-> {ok: true, session: "cookie_str"}
비밀번호는 저장하지 않고 세션 쿠키만 반환합니다.
"""
import json
import requests
from http.server import BaseHTTPRequestHandler

BASE = 'https://po.dongkoo.co.kr'


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
        username = body.get('username', '').strip()
        password = body.get('password', '')
        if not username or not password:
            return self._respond(400, {'ok': False, 'error': '아이디와 비밀번호를 입력하세요.'})

        sess = requests.Session()
        sess.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        r = sess.post(f'{BASE}/api/login', json={'username': username, 'password': password})
        if r.status_code != 200:
            return self._respond(401, {'ok': False, 'error': '로그인 실패. 아이디/비밀번호를 확인하세요.'})

        cookie_str = '; '.join(f'{k}={v}' for k, v in sess.cookies.items())
        if not cookie_str:
            return self._respond(401, {'ok': False, 'error': '로그인 실패. 아이디/비밀번호를 확인하세요.'})

        self._respond(200, {'ok': True, 'session': cookie_str})

    def _respond(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
