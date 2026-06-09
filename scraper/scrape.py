# -*- coding: utf-8 -*-
import os
import sys
import re
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

URL = os.getenv("DAOU_URL")
ID  = os.getenv("DAOU_ID")
PW  = os.getenv("DAOU_PW")

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

APPROVAL_URL = "https://po.dongkoo.co.kr/app/approval/deptfolder/draft/83?page=0&offset=500&property=draftedAt&direction=desc&searchtype=&keyword=&fromDate=&toDate=&duration=all"
FOLDER_BASE_URL = "https://po.dongkoo.co.kr/app/approval/deptfolder/draft/83"


def load_existing_doc_ids():
    # doc_url이 없는 기존 레코드도 재처리하기 위해 url 있는 것만 스킵
    res = supabase.table("leave_records").select("doc_id, doc_url").execute()
    return {row["doc_id"] for row in res.data if row.get("doc_url")}


def load_processed_cancel_ids():
    res = supabase.table("cancel_records").select("doc_id").execute()
    return {row["doc_id"] for row in res.data}


def save_cancel_record(doc_id, target_doc_id):
    supabase.table("cancel_records").upsert({
        "doc_id": doc_id,
        "target_doc_id": target_doc_id,
    }).execute()


def extract_target_doc_id(text):
    """상세내용 텍스트에서 휴가 문서번호 패턴 추출 (예: 계원-202606-00017)"""
    m = re.search(r'[가-힣]+-\d{6}-\d+', text)
    return m.group(0) if m else None


def upsert_record(doc_id, record):
    supabase.table("leave_records").upsert({
        "doc_id":     doc_id,
        "name":       record["name"],
        "leave_type": record["leave_type"],
        "label":      record["label"],
        "day_type":   record["day_type"],
        "start_date": record["start"],
        "end_date":   record["end"],
        "duration":   record["duration"],
        "status":     record["status"],
        "doc_url":    record["doc_url"],
    }).execute()


def parse_leave_detail(page):
    data = {}
    rows = page.query_selector_all("tr")
    for row in rows:
        cells = row.query_selector_all("th, td")
        texts = [c.inner_text().strip().replace("\xa0", " ") for c in cells]
        if len(texts) < 2:
            continue
        key = texts[0].replace(" ", "")
        if key == "문서번호":
            data["doc_id"] = texts[1]
        elif key == "신청자":
            data["name"] = texts[1]
        elif key == "근태구분":
            data["leave_type"] = texts[1]
        elif key == "근태기간":
            data["period_raw"] = " ".join(texts[1:])
    return data


def parse_period(period_raw):
    # "2026-06-04(목) 08시 30분 ~ 2026-06-04(목) 17시 30분 1일간" 형태 파싱
    dates = re.findall(r"(\d{4}-\d{2}-\d{2})", period_raw)
    duration = ""
    m = re.search(r"(\S+간)", period_raw)
    if m:
        duration = m.group(1)
    start_hour = None
    h = re.search(r"(\d{2})시\s*\d{2}분", period_raw)
    if h:
        start_hour = int(h.group(1))
    return {
        "start": dates[0] if len(dates) > 0 else "",
        "end":   dates[1] if len(dates) > 1 else (dates[0] if dates else ""),
        "duration": duration,
        "start_hour": start_hour,
    }


LABEL_MAP = {
    "연차":       "연차",
    "체력단련휴가": "연차",
    "현지출근":   "현지출근",
    "외근":       "외근",
}

def get_label(leave_type):
    return LABEL_MAP.get(leave_type, leave_type)


def get_day_type(leave_type, duration, start_hour):
    # leave_type에 반차/반반차 직접 명시된 경우
    if "반반차" in leave_type:
        return leave_type  # 반반차(1Q) ~ 반반차(4Q)
    if "반차" in leave_type:
        return leave_type  # 반차(오전), 반차(오후)
    # 기본 휴가 유형(연차, 체력단련휴가 등) + duration으로 판단
    if duration == "0.5일간":
        if start_hour is not None:
            return "반차(오전)" if start_hour < 12 else "반차(오후)"
        return "반차"
    if duration == "0.25일간":
        if start_hour is not None:
            if start_hour < 10:   return "반반차(1Q)"
            if start_hour < 12:   return "반반차(2Q)"
            if start_hour < 14:   return "반반차(3Q)"
            return "반반차(4Q)"
        return "반반차"
    return "종일"


def scrape():
    data = load_existing_doc_ids()
    new_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("dialog", lambda d: d.dismiss())

        # 로그인
        page.goto(URL)
        page.wait_for_load_state("domcontentloaded")
        page.fill("#username", ID)
        page.fill("#password", PW)
        page.click("input[type='submit'], button[type='submit'], .btn_login")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        page.wait_for_timeout(2000)
        print("로그인 성공")

        # 기안 완료함 이동 후 검색 UI로 전체기간 + 결재양식 검색
        page.goto(FOLDER_BASE_URL)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)

        # 기간: 전체기간, 검색유형: 결재양식(formName), 키워드 입력 후 버튼 클릭
        try:
            page.select_option("select[name='duration'], #duration", "all")
        except Exception:
            pass
        try:
            page.select_option("#searchtype", "formName")
        except Exception:
            page.select_option("select[name='searchtype']", "formName")
        page.fill("#keyword, input[name='keyword']", "근태계신청_본사(ERP연동)")
        try:
            page.click("button:has-text('검색')")
        except Exception:
            page.press("#keyword, input[name='keyword']", "Enter")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(3000)
        search_result_url = page.url
        print(f"검색 후 URL: {search_result_url}")

        # 목록 행 수집
        rows = page.query_selector_all("tr")
        print(f"전체 tr 수: {len(rows)}개")
        targets = []
        for row in rows:
            form_el = row.query_selector("td:nth-child(3)")
            if form_el and "근태계신청" in form_el.inner_text():
                link_el = row.query_selector("td:nth-child(5) a")
                doc_el  = row.query_selector("td:nth-child(8)")
                if link_el:
                    doc_id_list = doc_el.inner_text().strip() if doc_el else ""
                    targets.append({"title": link_el.inner_text().strip(), "doc_id": doc_id_list})

        print(f"근태계신청 필터 결과: {len(targets)}건")

        def get_target_links():
            rows = page.query_selector_all("tr")
            links = []
            for row in rows:
                form_el = row.query_selector("td:nth-child(3)")
                if form_el and "근태계신청" in form_el.inner_text():
                    link_el = row.query_selector("td:nth-child(5) a")
                    if link_el:
                        links.append(link_el)
            return links

        def redo_search():
            page.goto(FOLDER_BASE_URL)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)
            try:
                page.select_option("select[name='duration'], #duration", "all")
            except Exception:
                pass
            try:
                page.select_option("#searchtype", "formName")
            except Exception:
                page.select_option("select[name='searchtype']", "formName")
            page.fill("#keyword, input[name='keyword']", "근태계신청_본사(ERP연동)")
            try:
                page.click("button:has-text('검색')")
            except Exception:
                page.press("#keyword, input[name='keyword']", "Enter")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)

        for i, item in enumerate(targets):
            # 검색 결과 페이지에서 링크 재조회, 검색 상태 잃었으면 재검색
            target_links = get_target_links()
            if len(target_links) < len(targets):
                redo_search()
                target_links = get_target_links()

            if i >= len(target_links):
                continue

            # 목록에서 읽은 doc_id로 사전 스킵
            if item["doc_id"] and item["doc_id"] in data:
                print(f"  이미 처리됨 (DB), 스킵: {item['doc_id']}")
                continue

            target_links[i].click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)

            detail = parse_leave_detail(page)
            doc_id = detail.get("doc_id", "")

            def go_back_to_list():
                # 상세 페이지의 목록 버튼 클릭 시도 (SPA 상태 유지)
                try:
                    page.click("button:has-text('목록'), a:has-text('목록')", timeout=2000)
                except Exception:
                    page.go_back()
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1000)

            if not doc_id:
                print(f"  문서번호 없음, 스킵: {item['title']}")
                go_back_to_list()
                continue

            # 근태 데이터 없는 문서 스킵
            if not detail.get("name") or not detail.get("leave_type"):
                print(f"  근태 문서 아님, 스킵: {doc_id}")
                go_back_to_list()
                continue

            if doc_id in data:
                print(f"  이미 처리됨 (DB), 스킵: {doc_id}")
                go_back_to_list()
                continue

            doc_url = page.url
            period = parse_period(detail.get("period_raw", ""))
            leave_type = detail.get("leave_type", "")
            day_type = get_day_type(leave_type, period["duration"], period["start_hour"])
            record = {
                "name":       detail.get("name", ""),
                "leave_type": leave_type,
                "label":      get_label(leave_type),
                "day_type":   day_type,
                "start":      period["start"],
                "end":        period["end"],
                "duration":   period["duration"],
                "status":     "승인",
                "doc_url":    doc_url,
            }
            upsert_record(doc_id, record)
            data.add(doc_id)
            new_count += 1
            print(f"  수집 완료: {doc_id} | {record['name']} | {record['leave_type']} | {record['start']} ~ {record['end']} | {detail.get('period_raw', '')}")

            page.go_back()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(500)

        # ── 취소 문서 처리 ──────────────────────────────────
        processed_cancel_ids = load_processed_cancel_ids()
        cancel_count = 0

        def get_cancel_links():
            rows = page.query_selector_all("tr")
            links = []
            for row in rows:
                form_el = row.query_selector("td:nth-child(3)")
                if form_el and "ERP Data 변경 요청서" in form_el.inner_text():
                    link_el = row.query_selector("td:nth-child(5) a")
                    if link_el:
                        links.append(link_el)
            return links

        def redo_cancel_search():
            page.goto(FOLDER_BASE_URL)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)
            try:
                page.select_option("select[name='duration'], #duration", "all")
            except Exception:
                pass
            try:
                page.select_option("#searchtype", "formName")
            except Exception:
                page.select_option("select[name='searchtype']", "formName")
            page.fill("#keyword, input[name='keyword']", "ERP Data 변경 요청서")
            try:
                page.click("button:has-text('검색')")
            except Exception:
                page.press("#keyword, input[name='keyword']", "Enter")
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(2000)

        redo_cancel_search()

        cancel_rows = page.query_selector_all("tr")
        cancel_targets = []
        for row in cancel_rows:
            form_el = row.query_selector("td:nth-child(3)")
            if form_el and "ERP Data 변경 요청서" in form_el.inner_text():
                doc_el = row.query_selector("td:nth-child(8)")
                if doc_el:
                    cancel_targets.append({"doc_id": doc_el.inner_text().strip()})

        print(f"취소 문서: {len(cancel_targets)}건 발견")

        for i, item in enumerate(cancel_targets):
            doc_id = item["doc_id"]
            if doc_id in processed_cancel_ids:
                print(f"  취소 이미 처리됨, 스킵: {doc_id}")
                continue

            cancel_links = get_cancel_links()
            if len(cancel_links) < len(cancel_targets):
                redo_cancel_search()
                cancel_links = get_cancel_links()
            if i >= len(cancel_links):
                continue

            cancel_links[i].click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1500)

            # 상세내용에서 원본 문서번호 추출 (여러 방식 시도)
            detail_text = ""
            for row in page.query_selector_all("tr"):
                cells = row.query_selector_all("th, td")
                texts = [c.inner_text().strip().replace("\xa0", " ") for c in cells]
                if len(texts) >= 2 and "상세내용" in texts[0]:
                    detail_text = " ".join(texts[1:])
                    break
            if not detail_text:
                # 테이블 구조가 다를 경우 전체 텍스트에서 탐색
                detail_text = page.inner_text("body")

            target_doc_id = extract_target_doc_id(detail_text)
            if target_doc_id:
                supabase.table("leave_records").delete().eq("doc_id", target_doc_id).execute()
                print(f"  취소 처리: {doc_id} → 삭제 대상 {target_doc_id}")
            else:
                print(f"  취소 문서 {doc_id}: 문서번호 미발견")

            save_cancel_record(doc_id, target_doc_id or "")
            processed_cancel_ids.add(doc_id)
            cancel_count += 1

            try:
                page.click("button:has-text('목록'), a:has-text('목록')", timeout=2000)
            except Exception:
                page.go_back()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1000)
        # ────────────────────────────────────────────────────

        browser.close()

    print(f"\n완료: 신규 {new_count}건 저장, 취소 {cancel_count}건 처리")


if __name__ == "__main__":
    scrape()
