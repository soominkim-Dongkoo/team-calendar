# -*- coding: utf-8 -*-
"""
다우오피스 캘린더 API → 전사일정 수집
GET /api/calendar/event?timeMin=...&timeMax=...&calendarIds[]=10
"""
import os, sys
from datetime import date, timedelta
from dotenv import load_dotenv
import requests
import httpx
from supabase import create_client
from supabase.client import ClientOptions

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

BASE         = 'https://po.dongkoo.co.kr'
DAOU_ID      = os.getenv('DAOU_ID')
DAOU_PW      = os.getenv('DAOU_PW')
OWNER        = 'system_calendar'
CALENDAR_ID  = 10   # 전사일정 (11은 휴일일정 → 제외)
DAYS_AHEAD   = 45

supabase = create_client(
    os.getenv('SUPABASE_URL'),
    os.getenv('SUPABASE_KEY'),
    options=ClientOptions(httpx_client=httpx.Client(http2=False)),
)


FIXED_USER_ID = '20230005'  # 김수민


def get_credential():
    pw_res = supabase.rpc('get_daou_password', {'p_user_id': FIXED_USER_ID}).execute()
    pw = pw_res.data
    if not pw or pw == '1234':
        raise RuntimeError(f'{FIXED_USER_ID} 비밀번호를 찾을 수 없습니다.')
    return FIXED_USER_ID, pw


def login():
    uid, pw = get_credential()
    s = requests.Session()
    s.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    r = s.post(f'{BASE}/api/login', json={'username': uid, 'password': pw})
    if r.status_code != 200:
        raise RuntimeError(f'로그인 실패 ({r.status_code})')
    return s


def fetch_events(session):
    today    = date.today()
    time_min = f"{today.isoformat()}T00:00:00.000+09:00"
    time_max = f"{(today + timedelta(days=DAYS_AHEAD)).isoformat()}T23:59:59.999+09:00"

    r = session.get(
        f'{BASE}/api/calendar/event',
        params=[
            ('timeMin', time_min),
            ('timeMax', time_max),
            ('includingAttendees', 'true'),
            ('calendarIds[]', CALENDAR_ID),
        ],
        headers={'Accept': 'application/json, text/javascript, */*; q=0.01',
                 'X-Requested-With': 'XMLHttpRequest'},
    )
    data = r.json()
    if data.get('code') != '200':
        raise RuntimeError(f'API 오류: {data}')
    return [e for e in data.get('data', []) if e.get('calendarId') == CALENDAR_ID]



def scrape():
    print('[1] 다우오피스 로그인...')
    session = login()
    print('    완료')

    print(f'[2] 캘린더 API 호출 (전사일정, 오늘~{DAYS_AHEAD}일)...')
    events = fetch_events(session)
    print(f'    {len(events)}건 조회')

    # 오늘 이후 기존 데이터만 삭제 → 과거 데이터는 보존
    today = date.today().isoformat()
    print('[3] 오늘 이후 기존 데이터 삭제...')
    supabase.table('manual_events').delete() \
        .eq('owner', OWNER).gte('start_date', today).execute()

    if not events:
        print('저장할 일정 없음')
        return

    records = [{
        'title':        ev['summary'],
        'event_type':   'company',
        'owner':        OWNER,
        'is_team':      True,
        'start_date':   ev['startTime'][:10],
        'end_date':     ev['endTime'][:10],
        'start_time':   ev['startTime'][11:16] if ev.get('timeType') == 'timed' else None,
        'end_time':     ev['endTime'][11:16]   if ev.get('timeType') == 'timed' else None,
        'meeting_room': ev.get('location') or None,
    } for ev in events]

    supabase.table('manual_events').insert(records).execute()
    print(f'완료: {len(records)}건 저장')


if __name__ == '__main__':
    scrape()
