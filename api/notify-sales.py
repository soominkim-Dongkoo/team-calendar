# -*- coding: utf-8 -*-
"""
Vercel Serverless Function — 매출 업데이트 푸시 알림
POST /api/notify-sales
  - 매출 업로드 후 index.html 또는 exe에서 호출
  - users.sales_notify=true인 유저의 push_subscriptions 에 발송
"""

import json, os, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler
from datetime import date

try:
    from pywebpush import webpush, WebPushException
    _WEBPUSH_OK = True
except Exception:
    _WEBPUSH_OK = False

SB_URL            = os.environ.get('SUPABASE_URL', '')
SB_KEY            = os.environ.get('SUPABASE_SERVICE_KEY', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_SUBJECT     = os.environ.get('VAPID_SUBJECT', '')


def _sb_headers():
    return {
        'apikey': SB_KEY,
        'Authorization': f'Bearer {SB_KEY}',
        'Content-Type': 'application/json',
    }

def sb_get(table, params):
    qs = urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(
            f'{SB_URL}/rest/v1/{table}?{qs}',
            headers=_sb_headers(),
        )
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode())
    except Exception:
        return []

def sb_delete_sub(sub_id):
    req = urllib.request.Request(
        f'{SB_URL}/rest/v1/push_subscriptions?id=eq.{sub_id}',
        headers=_sb_headers(),
        method='DELETE',
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

def send_push(user_ids):
    if not VAPID_PRIVATE_KEY or not _WEBPUSH_OK or not user_ids:
        return 0

    ids_str = ','.join(user_ids)
    subs = sb_get('push_subscriptions', [('select', '*'), ('user_id', f'in.({ids_str})')])
    if not subs:
        return 0

    today_str = date.today().strftime('%-m월 %-d일')
    payload = json.dumps({
        'title': '📊 매출 업데이트',
        'body':  f'{today_str} 매출 데이터가 업데이트되었습니다.',
        'url':   '/',
    }, ensure_ascii=False)

    sent = 0
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    'endpoint': sub['endpoint'],
                    'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={'sub': VAPID_SUBJECT},
            )
            sent += 1
        except WebPushException as ex:
            status = ex.response.status_code if ex.response is not None else None
            if status in (404, 410):
                sb_delete_sub(sub['id'])
    return sent


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        users = sb_get('users', [('select', 'user_id'), ('sales_notify', 'eq.true')])
        if not users:
            self._send(200, {'sent': 0, 'message': 'no recipients'})
            return

        user_ids = [u['user_id'] for u in users]
        sent = send_push(user_ids)
        print(f'[notify-sales] recipients={len(user_ids)} sent={sent}')
        self._send(200, {'sent': sent, 'recipients': len(user_ids)})

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _send(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
