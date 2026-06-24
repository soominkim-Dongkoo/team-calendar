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

def sb_insert_history(user_ids, title, body):
    rows = [{'user_id': uid, 'title': title, 'body': body} for uid in user_ids]
    req = urllib.request.Request(
        f'{SB_URL}/rest/v1/notification_history',
        data=json.dumps(rows, ensure_ascii=False).encode(),
        headers={**_sb_headers(), 'Prefer': 'return=minimal'},
        method='POST',
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
    for uid in user_ids:
        sb_trim_history(uid)

def sb_trim_history(user_id, keep=10):
    rows = sb_get('notification_history', [('select', 'id'), ('user_id', f'eq.{user_id}'), ('order', 'created_at.desc')])
    if len(rows) > keep:
        ids = ','.join(r['id'] for r in rows[keep:])
        req = urllib.request.Request(
            f'{SB_URL}/rest/v1/notification_history?id=in.({ids})',
            headers=_sb_headers(),
            method='DELETE',
        )
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass

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

    today = date.today()
    today_iso   = today.isoformat()
    month_start = today.replace(day=1).isoformat()

    daily_rows = sb_get('sales_data', [('select', 'amount,returns'), ('sale_date', f'eq.{today_iso}')])
    month_rows = sb_get('sales_data', [('select', 'amount,returns'), ('sale_date', f'gte.{month_start}'), ('sale_date', f'lte.{today_iso}')])

    def net(rows):
        return sum((r.get('amount', 0) or 0) - (r.get('returns', 0) or 0) for r in (rows or []))

    def fmt(won):
        return f'{won / 1e8:.1f}억'

    daily_net   = net(daily_rows)
    monthly_net = net(month_rows)

    today_str = today.strftime('%-m월 %-d일')

    push_title = '📊 매출 업데이트'
    push_body  = f'{today_str} 매출 데이터가 업데이트되었습니다.\n당일 : {fmt(daily_net)}\n누적 : {fmt(monthly_net)}'

    # history 먼저 insert → 정확한 미읽음 수 계산
    sb_insert_history(user_ids, push_title, push_body)

    unread_rows = sb_get('notification_history', [
        ('select', 'user_id'),
        ('user_id', f'in.({ids_str})'),
        ('is_read', 'eq.false'),
    ])
    from collections import Counter
    unread_counts = Counter(r['user_id'] for r in (unread_rows or []))

    sent = 0
    for sub in subs:
        badge_n = unread_counts.get(sub['user_id'], 1)
        payload = json.dumps({'title': push_title, 'body': push_body, 'url': '/?view=sales', 'badge': badge_n}, ensure_ascii=False)
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
