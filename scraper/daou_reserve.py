# -*- coding: utf-8 -*-
"""
다우오피스 회의실 예약 모듈
- create(room, title, start_iso, end_iso) → reservation_id
- delete(reservation_id)
- update(reservation_id, room, title, start_iso, end_iso) → new_reservation_id

start_iso / end_iso 형식: "2026-06-15T10:00:00"
"""

import os
import sys
import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

BASE = "https://po.dongkoo.co.kr"

ROOM_MAP = {
    '본사_대회의실':        {'assetId': '20', 'itemId': '22'},
    '본사_중회의실':        {'assetId': '20', 'itemId': '23'},
    '본사_1408호 중회의실': {'assetId': '20', 'itemId': '40'},
    '본사_1407호 중회의실': {'assetId': '20', 'itemId': '50'},
}

_HEADERS = {
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Content-Type': 'application/json',
    'X-Requested-With': 'XMLHttpRequest',
    'GO-Agent': '',
    'timezoneoffset': '540',
}

DEFAULT_USER_ID = '645'  # 김수민 (계정 소유자)


def _login() -> requests.Session:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on('dialog', lambda d: d.dismiss())
        page.goto(os.getenv('DAOU_URL'))
        page.wait_for_load_state('domcontentloaded')
        page.fill('#username', os.getenv('DAOU_ID'))
        page.fill('#password', os.getenv('DAOU_PW'))
        page.click("input[type='submit'], button[type='submit'], .btn_login")
        page.wait_for_url(lambda u: '/login' not in u, timeout=15000)
        page.wait_for_timeout(2000)
        cookies = page.context.cookies()
        browser.close()

    sess = requests.Session()
    for c in cookies:
        sess.cookies.set(c['name'], c['value'], domain=c.get('domain', ''))
    return sess


def _to_kst(s: str) -> str:
    s = s.replace(' ', 'T')
    if len(s) == 16:
        s += ':00'
    return s[:19] + '.000+09:00'


class DaouReserver:
    def __init__(self):
        self._sess: requests.Session | None = None

    def _get_sess(self) -> requests.Session:
        if self._sess is None:
            print('[다우오피스] 로그인 중...')
            self._sess = _login()
            print('[다우오피스] 로그인 완료')
        return self._sess

    def _req(self, method: str, path: str, **kwargs):
        s = self._get_sess()
        r = s.request(method, BASE + path, headers=_HEADERS, **kwargs)
        if r.status_code in (401, 403):
            print('[다우오피스] 세션 만료 → 재로그인')
            self._sess = _login()
            r = self._sess.request(method, BASE + path, headers=_HEADERS, **kwargs)
        return r

    def create(self, room: str, title: str, start_iso: str, end_iso: str,
               user_id: str = DEFAULT_USER_ID) -> int:
        info = ROOM_MAP.get(room)
        if not info:
            raise ValueError(f'알 수 없는 회의실: {room}')

        payload = {
            'assetId':    info['assetId'],
            'itemId':     info['itemId'],
            'type':       'reserve',
            'startTime':  _to_kst(start_iso),
            'endTime':    _to_kst(end_iso),
            'useAnonym':  False,
            'user':       {'id': user_id},
            'properties': [{'attributeId': '33', 'content': title}],
            'allday':     False,
        }
        r = self._req('POST',
                      f"/api/asset/{info['assetId']}/item/{info['itemId']}/reserve",
                      json=payload)
        if r.status_code != 200:
            try:
                msg = r.json().get('message', r.text[:100])
            except Exception:
                msg = r.text[:100]
            # 500 = 이미 예약된 시간대
            if r.status_code == 500:
                raise RuntimeError(f'해당 시간에 이미 예약이 있습니다 ({room})')
            raise RuntimeError(f'예약 실패: {msg}')
        return r.json()['data']['id']

    def delete(self, reservation_id: int) -> None:
        r = self._req('DELETE', '/api/asset/item/reservation',
                      json={'ids': [reservation_id]})
        if r.status_code != 200:
            raise RuntimeError(f'예약 삭제 실패 ({r.status_code}): {r.text[:200]}')

    def update(self, reservation_id: int, room: str, title: str,
               start_iso: str, end_iso: str, user_id: str = DEFAULT_USER_ID) -> int:
        self.delete(reservation_id)
        return self.create(room, title, start_iso, end_iso, user_id)
