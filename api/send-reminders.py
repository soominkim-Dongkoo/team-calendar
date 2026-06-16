# -*- coding: utf-8 -*-
"""
Vercel Serverless Function — 알림 발송 (Slack + 웹 푸시)
GET /api/send-reminders
  - cron-job.org 에서 1분마다 호출
  - 헤더: x-cron-secret: {CRON_SECRET}

환경변수 (Vercel Dashboard → Settings → Environment Variables):
  SUPABASE_URL          Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY  Supabase service_role 키 (Settings → API)
  SLACK_BOT_TOKEN       Slack Bot OAuth 토큰 (xoxb-...)
  SLACK_TEAM_CHANNEL_ID 팀 채널 ID (예: C012AB3CD)
  CRON_SECRET           cron 호출 인증용 임의 문자열
  VAPID_PRIVATE_KEY     웹 푸시 VAPID 개인키
  VAPID_SUBJECT         웹 푸시 VAPID sub (예: mailto:team@example.com)
"""

import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone

from pywebpush import webpush, WebPushException

SB_URL       = os.environ.get('SUPABASE_URL', '')
SB_KEY       = os.environ.get('SUPABASE_SERVICE_KEY', '')
SLACK_TOKEN  = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CH     = os.environ.get('SLACK_TEAM_CHANNEL_ID', '')
CRON_SECRET  = os.environ.get('CRON_SECRET', '')
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY', '')
VAPID_SUBJECT     = os.environ.get('VAPID_SUBJECT', '')

SLACK_ENABLED = False  # 웹 푸시 전환으로 Slack 알림 끊어둠. 필요 시 True로 복구.

def _http_post(url, headers=None, data=None):
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return json.loads(res.read().decode())
    except Exception:
        return {}

def _http_patch(url, headers=None, data=None):
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(url, data=body, headers=headers or {}, method='PATCH')
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

def _sb_headers():
    return {
        'apikey': SB_KEY,
        'Authorization': f'Bearer {SB_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
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

def sb_patch(table, row_id, data):
    _http_patch(
        f'{SB_URL}/rest/v1/{table}?id=eq.{row_id}',
        headers=_sb_headers(),
        data=data,
    )

def sb_delete(table, row_id):
    req = urllib.request.Request(
        f'{SB_URL}/rest/v1/{table}?id=eq.{row_id}',
        headers=_sb_headers(),
        method='DELETE',
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

def _open_dm(user_id):
    result = _http_post(
        'https://slack.com/api/conversations.open',
        headers={'Authorization': f'Bearer {SLACK_TOKEN}', 'Content-Type': 'application/json'},
        data={'users': user_id},
    )
    print(f'[open_dm] user={user_id} ok={result.get("ok")} error={result.get("error")} channel={result.get("channel", {}).get("id")}')
    return result.get('channel', {}).get('id') if result.get('ok') else None

def send_slack(channel, reminder, is_team):
    if not SLACK_TOKEN or not channel:
        return
    # 개인 DM은 conversations.open으로 실제 DM 채널 ID 사용
    if not is_team:
        dm_channel = _open_dm(channel)
        if dm_channel:
            channel = dm_channel
    time_str = (reminder.get('start_time') or '')[:5] or '종일'
    label = '팀 일정' if is_team else '개인 일정'
    text = f'🔔 *[{label} 알림]* {reminder["title"]}\n📅 {reminder["start_date"]} {time_str}'
    result = _http_post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_TOKEN}', 'Content-Type': 'application/json'},
        data={'channel': channel, 'text': text},
    )
    print(f'[slack] channel={channel} ok={result.get("ok")} error={result.get("error")}')

def send_push(subscriptions, reminder, is_team):
    if not VAPID_PRIVATE_KEY:
        return
    time_str = (reminder.get('start_time') or '')[:5] or '종일'
    label = '팀 일정' if is_team else '개인 일정'
    payload = json.dumps({
        'title': f'🔔 {label} 알림',
        'body': f'{reminder["title"]} · {reminder["start_date"]} {time_str}',
        'url': '/',
    }, ensure_ascii=False)
    for sub in subscriptions:
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
        except WebPushException as ex:
            status = ex.response.status_code if ex.response is not None else None
            print(f'[push] failed endpoint={sub["endpoint"][:50]} status={status} error={ex}')
            if status in (404, 410):
                sb_delete('push_subscriptions', sub['id'])

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.headers.get('x-cron-secret', '') != CRON_SECRET:
            self._send(401, {'error': 'unauthorized'})
            return

        now = datetime.now(timezone.utc)
        ago = now - timedelta(minutes=2)

        reminders = sb_get('manual_events', [
            ('select', '*'),
            ('reminder_at', f'gte.{ago.isoformat()}'),
            ('reminder_at', f'lte.{now.isoformat()}'),
            ('reminder_sent_at', 'is.null'),
        ])
        print(f'[reminders] now={now.isoformat()} found={len(reminders or [])}')

        sent_titles = []
        for r in (reminders or []):
            owner = r.get('owner', '')
            users = sb_get('users', [('select', 'user_id,name,slack_id'), ('user_id', f'eq.{owner}')])
            user = users[0] if users else {}
            print(f'[event] title={r.get("title")} is_team={r.get("is_team")} owner={owner} slack_id={user.get("slack_id")}')

            if r.get('is_team'):
                if SLACK_ENABLED:
                    send_slack(SLACK_CH, r, is_team=True)
                subs = sb_get('push_subscriptions', [('select', '*')])
                send_push(subs, r, is_team=True)
            else:
                if SLACK_ENABLED and user.get('slack_id'):
                    send_slack(user['slack_id'], r, is_team=False)
                subs = sb_get('push_subscriptions', [('select', '*'), ('user_id', f'eq.{owner}')])
                send_push(subs, r, is_team=False)

            sb_patch('manual_events', r['id'], {'reminder_sent_at': now.isoformat()})
            sent_titles.append(r.get('title', ''))

        self._send(200, {'sent': len(sent_titles), 'titles': sent_titles})

    def _send(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass
