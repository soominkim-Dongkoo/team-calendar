# -*- coding: utf-8 -*-
"""
다우오피스 캘린더 → 전사일정 스크래핑
실행: python scraper/scrape_calendar.py
"""
import os, sys, re
from datetime import date
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

URL   = os.getenv("DAOU_URL")
ID    = os.getenv("DAOU_ID")
PW    = os.getenv("DAOU_PW")
OWNER = "system_calendar"

supabase     = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
CALENDAR_URL = "https://po.dongkoo.co.kr/app/calendar"


def parse_date(text):
    m = re.match(r'(\d{1,2})\.(\d{2})', text.strip())
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    today = date.today()
    year  = today.year + (1 if month < today.month - 1 else 0)
    return f"{year}-{month:02d}-{day:02d}"


def parse_time(text):
    ampm = 'PM' if '오후' in text else 'AM'
    m = re.search(r'(\d{1,2}):(\d{2})', text)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if ampm == 'PM' and h < 12: h += 12
    if ampm == 'AM' and h == 12: h = 0
    return f"{h:02d}:{mn:02d}"


def parse_time_range(text):
    parts = text.split('~')
    return (parse_time(parts[0]) if parts else None,
            parse_time(parts[1]) if len(parts) > 1 else None)


def collect_list(page):
    """목록 뷰에서 이벤트 정보(날짜, 시간, 제목, 상세 URL) 수집."""
    events = []
    current_date = None
    rows = page.query_selector_all("table tr")

    for row in rows:
        cells = row.query_selector_all("th, td")
        if not cells:
            continue
        tags  = [c.evaluate("el => el.tagName") for c in cells]
        texts = [c.inner_text().strip() for c in cells]

        if len(cells) >= 7 or all(t == 'TH' for t in tags):
            continue

        # 날짜 TH + 이벤트 TD
        if tags[0] == 'TH' and len(texts) >= 3:
            d = parse_date(texts[0])
            if d:
                current_date = d
                start_t, end_t = parse_time_range(texts[1])
                title = texts[2]
                # 상세 링크 수집
                title_cell = cells[2] if len(cells) > 2 else cells[-2]
                href = title_cell.evaluate("el => el.querySelector('a')?.href || null")
                if title:
                    events.append({"date": current_date, "start_t": start_t,
                                   "end_t": end_t, "title": title, "href": href})

        # 같은 날 추가 이벤트 (날짜 TH 없음)
        elif tags[0] == 'TD' and current_date and len(texts) >= 2:
            start_t, end_t = parse_time_range(texts[0])
            title = texts[1]
            title_cell = cells[1] if len(cells) > 1 else cells[-2]
            href = title_cell.evaluate("el => el.querySelector('a')?.href || null")
            if title:
                events.append({"date": current_date, "start_t": start_t,
                               "end_t": end_t, "title": title, "href": href})

    return events


def get_location(page):
    """상세 페이지에서 장소 값 추출."""
    return page.evaluate(
        "document.querySelector('#form-field-location, input[name=\"location\"]')?.value?.trim() || null"
    )


def load_existing_keys():
    """이미 저장된 (start_date, title) 키 셋."""
    today = date.today().isoformat()
    res = supabase.table("manual_events").select("start_date, title")\
        .eq("owner", OWNER).gte("start_date", today).execute()
    return {(r["start_date"], r["title"]) for r in (res.data or [])}


def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("dialog", lambda d: d.dismiss())

        # 1. 로그인
        print("[1] 로그인...")
        page.goto(URL)
        page.wait_for_load_state("domcontentloaded")
        page.fill("#username", ID)
        page.fill("#password", PW)
        page.click("input[type='submit'], button[type='submit'], .btn_login")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        page.wait_for_timeout(2000)
        print("    완료")

        # 2. 캘린더 목록 뷰
        print("[2] 캘린더 목록 뷰 이동...")
        page.goto(CALENDAR_URL)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)

        is_checked = page.evaluate("document.getElementById('calendar_id_10')?.checked")
        if not is_checked:
            page.click('label[for="calendar_id_10"]')
            page.wait_for_timeout(1500)

        page.locator("li.last").click()
        page.wait_for_timeout(2000)

        for i in range(2):
            try:
                page.locator("span.txt", has_text="15일 더보기").click(timeout=5000)
                page.wait_for_timeout(2000)
                print(f"    더보기 {i+1}회")
            except Exception:
                break

        # 3. 목록 수집
        print("[3] 이벤트 목록 수집...")
        raw_events = collect_list(page)
        print(f"    총 {len(raw_events)}건")

        # 4. 중복 제거 (date + title)
        existing_keys = load_existing_keys()
        new_events = [e for e in raw_events
                      if (e["date"], e["title"]) not in existing_keys]
        print(f"    신규 {len(new_events)}건 (기존 {len(raw_events)-len(new_events)}건 스킵)")

        if not new_events:
            print("저장할 신규 일정 없음")
            browser.close()
            return

        # 5. 상세 진입 → 위치 수집
        print("[4] 상세 페이지 진입 (위치 수집)...")
        to_save = []
        for ev in new_events:
            location = None
            if ev["href"]:
                try:
                    page.goto(ev["href"])
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(1500)
                    location = get_location(page)
                    page.go_back()
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(800)
                except Exception as ex:
                    print(f"    ⚠️  상세 진입 실패 ({ev['title']}): {ex}")

            loc_str = f" | 위치: {location}" if location else ""
            print(f"    {ev['date']}  {ev['title']}{loc_str}")

            to_save.append({
                "title":        ev["title"],
                "event_type":   "company",
                "owner":        OWNER,
                "is_team":      True,
                "start_date":   ev["date"],
                "end_date":     ev["date"],
                "start_time":   ev["start_t"],
                "end_time":     ev["end_t"],
                "meeting_room": location,
            })

        # 6. Supabase 저장
        print("[5] Supabase 저장...")
        if to_save:
            supabase.table("manual_events").insert(to_save).execute()
        print(f"완료: {len(to_save)}건 저장")

        browser.close()


if __name__ == "__main__":
    scrape()
