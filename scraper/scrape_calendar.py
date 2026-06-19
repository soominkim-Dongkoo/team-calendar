# -*- coding: utf-8 -*-
"""
다우오피스 캘린더 API → 전사일정 수집
GET /api/calendar/event?timeMin=...&timeMax=...&calendarIds[]=10
"""
import os, sys
from datetime import date, timedelta
from dotenv import load_dotenv
import requests
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

BASE         = 'https://po.dongkoo.co.kr'
DAOU_ID      = os.getenv('DAOU_ID')
DAOU_PW      = os.getenv('DAOU_PW')
OWNER        = 'system_calendar'
CALENDAR_ID  = 10   # 전사일정 (11은 휴일일정 → 제외)
DAYS_AHEAD   = 45

supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))


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


def to_db(ev):
    timed      = ev.get('timeType') == 'timed'
    start_date = ev['startTime'][:10]
    end_date   = ev['endTime'][:10]
    start_time = ev['startTime'][11:16] if timed else None
    end_time   = ev['endTime'][11:16]   if timed else None
    return {
        'title':      ev['summary'],
        'start_date': start_date,
        'end_date':   end_date,
        'start_time': start_time,
        'end_time':   end_time,
        'location':   ev.get('location') or None,
    }


def load_existing():
    today = date.today().isoformat()
    res = supabase.table('manual_events') \
        .select('id, start_date, title, start_time, end_time, meeting_room') \
        .eq('owner', OWNER).gte('start_date', today).execute()
    return {(r['start_date'], r['title']): r for r in (res.data or [])}


def changed(old, new):
    def n(t): return (t or '')[:5]
    return (n(old['start_time']) != n(new['start_time']) or
            n(old['end_time'])   != n(new['end_time'])   or
            (old.get('meeting_room') or '') != (new['location'] or ''))


def scrape():
    print('[1] 다우오피스 로그인...')
    session = login()
    print('    완료')

    print(f'[2] 캘린더 API 호출 (전사일정, {DAYS_AHEAD}일)...')
    events = fetch_events(session)
    print(f'    {len(events)}건 조회')

    print('[3] DB 기존 데이터 비교...')
    existing = load_existing()

    to_insert    = []
    to_delete_ids = []
    skip_count   = 0

    for ev in events:
        p   = to_db(ev)
        key = (p['start_date'], p['title'])

        if key not in existing:
            to_insert.append(p)
            print(f'    [신규] {p["start_date"]} {p["title"]}')
        elif changed(existing[key], p):
            to_delete_ids.append(existing[key]['id'])
            to_insert.append(p)
            print(f'    [변경] {p["start_date"]} {p["title"]}')
        else:
            skip_count += 1

    new_count = len(to_insert) - len(to_delete_ids)
    print(f'    신규 {new_count}건 | 변경 {len(to_delete_ids)}건 | 스킵 {skip_count}건')

    for rid in to_delete_ids:
        supabase.table('manual_events').delete().eq('id', rid).execute()

    if to_insert:
        records = [{
            'title':        p['title'],
            'event_type':   'company',
            'owner':        OWNER,
            'is_team':      True,
            'start_date':   p['start_date'],
            'end_date':     p['end_date'],
            'start_time':   p['start_time'],
            'end_time':     p['end_time'],
            'meeting_room': p['location'],
        } for p in to_insert]
        supabase.table('manual_events').insert(records).execute()

    print(f'완료: {len(to_insert)}건 저장')


if __name__ == '__main__':
    scrape()
