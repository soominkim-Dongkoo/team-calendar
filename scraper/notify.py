# -*- coding: utf-8 -*-
import os
import sys
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

from supabase.client import ClientOptions
supabase = create_client(
    os.getenv('SUPABASE_URL'),
    os.getenv('SUPABASE_KEY'),
    options=ClientOptions(headers={"x-app-token": "dkbio-cal-2026"}),
)

GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_APP_PW = os.getenv('GMAIL_APP_PW')

USER_MAP = {
    '20130013': {'name': '황유준', 'email': 'destiny3730@dongkoo.co.kr'},
    '20230005': {'name': '김수민', 'email': 'soomin.kim@dongkoo.co.kr'},
    '20250049': {'name': '김경수', 'email': 'kyeongsoo.kim@dongkoo.co.kr'},
}
TEAM_EMAILS = [u['email'] for u in USER_MAP.values()]

DAY_LABELS = {'monday':'월','tuesday':'화','wednesday':'수','thursday':'목','friday':'금','saturday':'토','sunday':'일'}

WEEKDAY_KO = ['월','화','수','목','금','토','일']


def format_date(date_str, time_str=None):
    try:
        d = datetime.strptime(date_str, '%Y-%m-%d')
        dow = WEEKDAY_KO[d.weekday()]
        base = f"{d.month}월 {d.day}일 ({dow})"
        if time_str:
            base += f" {time_str[:5]}"
        return base
    except Exception:
        return date_str


def build_email_body(event):
    start = format_date(event['start_date'], event.get('start_time'))
    end_date = event['end_date']
    end_time = event.get('end_time')
    if end_date != event['start_date']:
        end_str = f" ~ {format_date(end_date, end_time)}"
    elif end_time:
        end_str = f" ~ {end_time[:5]}"
    else:
        end_str = ''

    room = event.get('meeting_room') or ''
    desc = event.get('description') or ''
    owner_info = USER_MAP.get(event.get('owner', ''), {})
    owner_name = owner_info.get('name', '')

    rows = [
        ('일정', f"<strong>{event['title']}</strong>"),
        ('일시', f"{start}{end_str}"),
    ]
    if room:
        rows.append(('장소', room))
    if owner_name and not event.get('is_team'):
        rows.append(('등록자', owner_name))
    if desc:
        rows.append(('내용', desc.replace('\n', '<br>')))

    table_rows = ''.join(
        f'<tr><td style="padding:6px 12px 6px 0;color:#718096;white-space:nowrap;vertical-align:top;">{k}</td>'
        f'<td style="padding:6px 0;">{v}</td></tr>'
        for k, v in rows
    )

    return f"""
<div style="font-family:'Apple SD Gothic Neo',sans-serif;max-width:480px;margin:0 auto;padding:24px;">
  <div style="font-size:13px;color:#4361ee;font-weight:700;margin-bottom:8px;">경영기획팀 캘린더</div>
  <div style="font-size:18px;font-weight:700;color:#1a202c;margin-bottom:20px;">📅 일정 알림</div>
  <table style="width:100%;border-collapse:collapse;font-size:14px;color:#2d3748;">
    {table_rows}
  </table>
</div>
"""


def send_email(to_addrs, subject, html_body):
    msg = MIMEMultipart('alternative')
    msg['From'] = f'경영기획팀 캘린더 <{GMAIL_USER}>'
    msg['To'] = ', '.join(to_addrs)
    msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PW)
        server.sendmail(GMAIL_USER, to_addrs, msg.as_string())


def notify():
    now = datetime.now(timezone.utc).isoformat()
    res = supabase.table('manual_events') \
        .select('*') \
        .lte('reminder_at', now) \
        .is_('notified_at', 'null') \
        .execute()

    events = res.data or []
    print(f"알림 대상: {len(events)}건")

    for ev in events:
        try:
            if ev.get('is_team'):
                recipients = TEAM_EMAILS
            else:
                owner = USER_MAP.get(ev.get('owner', ''))
                if not owner:
                    print(f"  owner 매핑 없음, 스킵: {ev['id']} owner={ev.get('owner')}")
                    supabase.table('manual_events').update({'notified_at': now}).eq('id', ev['id']).execute()
                    continue
                recipients = [owner['email']]

            subject = f"[캘린더 알림] {ev['title']}"
            body = build_email_body(ev)
            send_email(recipients, subject, body)
            supabase.table('manual_events').update({'notified_at': now}).eq('id', ev['id']).execute()
            print(f"  발송 완료: {ev['title']} → {recipients}")
        except Exception as e:
            print(f"  발송 실패: {ev.get('title')} / {e}")

    print("완료")


if __name__ == '__main__':
    notify()
