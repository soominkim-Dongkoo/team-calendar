# -*- coding: utf-8 -*-
"""1회성 백필: 2026-01-01 ~ 어제 전사일정 수집"""
import os, sys
from datetime import date, timedelta
from dotenv import load_dotenv
import requests
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

BASE        = 'https://po.dongkoo.co.kr'
OWNER       = 'system_calendar'
CALENDAR_ID = 10
FIXED_USER_ID = '20230005'

from supabase.client import ClientOptions
supabase = create_client(
    os.getenv('SUPABASE_URL'),
    os.getenv('SUPABASE_KEY'),
    options=ClientOptions(headers={"x-app-token": "dkbio-cal-2026"}),
)


def get_credential():
    pw_res = supabase.rpc('get_daou_password', {'p_user_id': FIXED_USER_ID}).execute()
    pw = pw_res.data
    if not pw or pw == '1234':
        raise RuntimeError('비밀번호를 찾을 수 없습니다.')
    return FIXED_USER_ID, pw


def login():
    uid, pw = get_credential()
    s = requests.Session()
    s.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    r = s.post(f'{BASE}/api/login', json={'username': uid, 'password': pw})
    if r.status_code != 200:
        raise RuntimeError(f'로그인 실패 ({r.status_code})')
    print(f'    [{uid}] 로그인 성공')
    return s


def fetch_events(session, time_min, time_max):
    r = session.get(
        f'{BASE}/api/calendar/event',
        params=[
            ('timeMin', time_min),
            ('timeMax', time_max),
            ('includingAttendees', 'true'),
            ('calendarIds[]', CALENDAR_ID),
        ],
        headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
    )
    data = r.json()
    if data.get('code') != '200':
        raise RuntimeError(f'API 오류: {data}')
    return [e for e in data.get('data', []) if e.get('calendarId') == CALENDAR_ID]


def load_all_existing():
    res = supabase.table('manual_events') \
        .select('start_date, title') \
        .eq('owner', OWNER).execute()
    return {(r['start_date'], r['title']) for r in (res.data or [])}


def to_record(ev):
    timed = ev.get('timeType') == 'timed'
    return {
        'title':        ev['summary'],
        'event_type':   'company',
        'owner':        OWNER,
        'is_team':      True,
        'start_date':   ev['startTime'][:10],
        'end_date':     ev['endTime'][:10],
        'start_time':   ev['startTime'][11:16] if timed else None,
        'end_time':     ev['endTime'][11:16]   if timed else None,
        'meeting_room': ev.get('location') or None,
    }


def main():
    time_min = '2026-01-01T00:00:00.000+09:00'
    time_max = f"{(date.today() - timedelta(days=1)).isoformat()}T23:59:59.999+09:00"

    print('[1] 로그인...')
    session = login()

    print(f'[2] API 호출: {time_min[:10]} ~ {time_max[:10]}')
    events = fetch_events(session, time_min, time_max)
    print(f'    {len(events)}건 조회')

    print('[3] 기존 데이터 확인...')
    existing = load_all_existing()

    to_insert = []
    for ev in events:
        rec = to_record(ev)
        key = (rec['start_date'], rec['title'])
        if key not in existing:
            to_insert.append(rec)
            print(f'    [신규] {rec["start_date"]} {rec["title"]}')
        else:
            print(f'    [스킵] {rec["start_date"]} {rec["title"]}')

    print(f'\n총 {len(to_insert)}건 삽입 예정')
    if not to_insert:
        print('삽입할 데이터 없음')
        return

    confirm = input('진행할까요? (y/n): ').strip().lower()
    if confirm != 'y':
        print('취소')
        return

    supabase.table('manual_events').insert(to_insert).execute()
    print(f'완료: {len(to_insert)}건 저장')


if __name__ == '__main__':
    main()
