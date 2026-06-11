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
import requests
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone

SB_URL      = os.environ.get('SUPABASE_URL', '')
SB_KEY      = os.environ.get('SUPABASE_SERVICE_KEY', '')
SLACK_TOKEN = os.environ.get('SLACK_BOT_TOKEN', '')
SLACK_CH    = os.environ.get('SLACK_TEAM_CHANNEL_ID', '')
CRON_SECRET = os.environ.get('CRON_SECRET', '')

_slack_id_cache = {}  # email → slack_user_id 캐시

def slack_id_from_email(email):
    """업무 이메일로 Slack 유저 ID 조회 (DM용)"""
    if email in _slack_id_cache:
        return _slack_id_cache[email]
    try:
        r = requests.get(
            'https://slack.com/api/users.lookupByEmail',
            headers={'Authorization': f'Bearer {SLACK_TOKEN}'},
            params={'email': email},
            timeout=10,
        )
        data = r.json()
        uid = data.get('user', {}).get('id') if data.get('ok') else None
        if uid:
            _slack_id_cache[email] = uid
        return uid
    except Exception:
        return None

def _sb_headers():
    return {
        'apikey': SB_KEY,
        'Authorization': f'Bearer {SB_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
    }

def sb_get(table, params):
    qs = urllib.parse.urlencode(params)
    r = requests.get(f'{SB_URL}/rest/v1/{table}?{qs}', headers=_sb_headers(), timeout=10)
    return r.json() if r.ok else []

def sb_patch(table, row_id, data):
    url = f'{SB_URL}/rest/v1/{table}?id=eq.{row_id}'
    requests.patch(url, headers=_sb_headers(), json=data, timeout=10)

def send_slack(channel, reminder, is_team):
    if not SLACK_TOKEN or not channel:
        return
    time_str = (reminder.get('start_time') or '')[:5] or '종일'
    label = '팀 일정' if is_team else '개인 일정'
    text = f'🔔 *[{label} 알림]* {reminder["title"]}\n📅 {reminder["start_date"]} {time_str}'
    requests.post(
        'https://slack.com/api/chat.postMessage',
        headers={'Authorization': f'Bearer {SLACK_TOKEN}', 'Content-Type': 'application/json'},
        json={'channel': channel, 'text': text},
        timeout=10,
    )

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        # 인증
        if self.headers.get('x-cron-secret', '') != CRON_SECRET:
            self._send(401, {'error': 'unauthorized'})
            return

        now = datetime.now(timezone.utc)
        ago = now - timedelta(minutes=2)  # 최대 2분 지연 허용

        # 발송 대상 조회: reminder_at 범위 내 + 미발송
        reminders = sb_get('manual_events', [
            ('select', '*'),
            ('reminder_at', f'gte.{ago.isoformat()}'),
            ('reminder_at', f'lte.{now.isoformat()}'),
            ('reminder_sent_at', 'is.null'),
        ])

        sent_titles = []
        for r in (reminders or []):
            owner = r.get('owner', '')
            users = sb_get('users', [('select', 'user_id,name,email'), ('user_id', f'eq.{owner}')])
            user = users[0] if users else {}

            # Slack
            if r.get('is_team'):
                # 팀 이벤트 → 팀 채널
                send_slack(SLACK_CH, r, is_team=True)
            elif user.get('email'):
                # 개인 이벤트 → 이메일로 Slack 유저 ID 조회 후 DM
                uid = slack_id_from_email(user['email'])
                if uid:
                    send_slack(uid, r, is_team=False)

            # 발송 완료 기록
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
