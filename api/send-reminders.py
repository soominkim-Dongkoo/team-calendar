# -*- coding: utf-8 -*-
"""
Vercel Serverless Function — 알림 발송 (Slack)
GET /api/send-reminders
  - cron-job.org 에서 1분마다 호출
  - 헤더: x-cron-secret: {CRON_SECRET}

환경변수 (Vercel Dashboard → Settings → Environment Variables):
  SUPABASE_URL          Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY  Supabase service_role 키 (Settings → API)
  SLACK_BOT_TOKEN       Slack Bot OAuth 토큰 (xoxb-...)
  SLACK_TEAM_CHANNEL_ID 팀 채널 ID (예: C012AB3CD)
  CRON_SECRET           cron 호출 인증용 임의 문자열
"""

import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone

SB_URL      = os.environ.get('SUPABASE_URL', '')
SB_KEY      = os.environ.get('SUPABASE_SERVICE_KEY', '')
SLACK_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CH    = os.environ.get('SLACK_TEAM_CHANNEL_ID', '')
CRON_SECRET = os.environ.get('CRON_SECRET', '')

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

def send_slack(channel, reminder, is_team):
    if not SLACK_TOKEN or not channel:
        return
    time_str = (reminder.get('start_time') or '')[:5] or '종일'
    label = '팀 일정' if is_team else '개인 일정'
    text = f'🔔 *[{label} 알림]* {reminder["title"]}\n📅 {reminder["start_date"]} {time_str}'
    result = _http_post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_TOKEN}', 'Content-Type': 'application/json'},
        data={'channel': channel, 'text': text},
    )
    print(f'[slack] channel={channel} ok={result.get("ok")} error={result.get("error")}')

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
                send_slack(SLACK_CH, r, is_team=True)
            elif user.get('slack_id'):
                send_slack(user['slack_id'], r, is_team=False)

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
