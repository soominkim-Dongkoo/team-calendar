# -*- coding: utf-8 -*-
import os
import sys
import re
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
import httpx
from supabase import create_client, Client
from supabase.client import ClientOptions

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv()

DAOU_URL = os.getenv("DAOU_URL")
supabase  = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY"),
    options=ClientOptions(httpx_client=httpx.Client(http2=False)),
)


# ── DB 헬퍼 ─────────────────────────────────────────────────────────────────

def load_scrape_targets():
    """users 테이블에서 daou_folders가 설정된 계정 목록 반환. 비밀번호는 복호화해서 채움."""
    res = supabase.table("users").select("user_id,password,daou_folders").execute()
    targets = []
    for u in (res.data or []):
        folders = u.get("daou_folders") or []
        stored_pw = (u.get("password") or "").strip()
        if not folders or not stored_pw or stored_pw == "1234":
            continue
        pw_res = supabase.rpc("get_daou_password", {"p_user_id": u["user_id"]}).execute()
        real_pw = pw_res.data
        if not real_pw or real_pw == "1234":
            continue
        u["password"] = real_pw
        targets.append(u)
    return targets

def load_existing_doc_ids(owner_user_id):
    res = supabase.table("leave_records").select("doc_id").eq("owner_user_id", owner_user_id).execute()
    return {row["doc_id"] for row in (res.data or [])}

def load_processed_cancel_ids():
    res = supabase.table("cancel_records").select("doc_id").execute()
    return {row["doc_id"] for row in res.data}

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
        "owner_user_id": record["owner_user_id"],
    }).execute()

def save_cancel_record(doc_id, target_doc_id):
    supabase.table("cancel_records").upsert({
        "doc_id": doc_id,
        "target_doc_id": target_doc_id,
    }).execute()


# ── 파싱 헬퍼 ───────────────────────────────────────────────────────────────

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
    if "반반차" in leave_type:
        return leave_type
    if "반차" in leave_type:
        return leave_type
    if duration == "0.5일간":
        if start_hour is not None:
            return "반차(오전)" if start_hour < 12 else "반차(오후)"
        return "반차"
    if duration == "0.25일간":
        if start_hour is not None:
            if start_hour < 10:  return "반반차(1Q)"
            if start_hour < 12:  return "반반차(2Q)"
            if start_hour < 14:  return "반반차(3Q)"
            return "반반차(4Q)"
        return "반반차"
    return "종일"

def extract_target_doc_id(text):
    m = re.search(r'계원-\d{6}-\d+', text)
    return m.group(0) if m else None


# ── 폴더 스크래핑 ────────────────────────────────────────────────────────────

def scrape_folder(page, folder_id, filter_name, existing_doc_ids, owner_user_id):
    """단일 폴더에서 근태 레코드 수집. filter_name이 있으면 해당 이름만."""
    folder_base_url = f"https://po.dongkoo.co.kr/app/approval/deptfolder/draft/{folder_id}"
    new_count = 0

    page.goto(folder_base_url)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(3000)

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
        page.goto(folder_base_url)
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

    rows = page.query_selector_all("tr")
    targets = []
    for row in rows:
        form_el = row.query_selector("td:nth-child(3)")
        if form_el and "근태계신청" in form_el.inner_text():
            link_el = row.query_selector("td:nth-child(5) a")
            doc_el  = row.query_selector("td:nth-child(8)")
            if link_el:
                doc_id_list = doc_el.inner_text().strip() if doc_el else ""
                targets.append({"title": link_el.inner_text().strip(), "doc_id": doc_id_list})

    print(f"    근태계신청 {len(targets)}건 발견")

    for i, item in enumerate(targets):
        target_links = get_target_links()
        if len(target_links) < len(targets):
            redo_search()
            target_links = get_target_links()
        if i >= len(target_links):
            continue

        if item["doc_id"] and item["doc_id"] in existing_doc_ids:
            print(f"    스킵(기존): {item['doc_id']}")
            continue

        target_links[i].click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        detail = parse_leave_detail(page)
        doc_id = detail.get("doc_id", "")

        def go_back():
            try:
                page.click("button:has-text('목록'), a:has-text('목록')", timeout=2000)
            except Exception:
                page.go_back()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1000)

        if not doc_id or not detail.get("name") or not detail.get("leave_type"):
            print(f"    근태 문서 아님, 스킵: {item['title']}")
            go_back()
            continue

        if doc_id in existing_doc_ids:
            print(f"    스킵(기존): {doc_id}")
            go_back()
            continue

        # 이름 필터 적용
        if filter_name and detail.get("name") != filter_name:
            print(f"    이름 불일치, 스킵: {detail.get('name')} (필터: {filter_name})")
            go_back()
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
            "owner_user_id": owner_user_id,
        }
        upsert_record(doc_id, record)
        existing_doc_ids.add(doc_id)
        new_count += 1
        print(f"    수집: {doc_id} | {record['name']} | {record['leave_type']} | {record['start']} ~ {record['end']}")

        page.go_back()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(500)

    return new_count


def scrape_cancel_folder(page, folder_id, processed_cancel_ids):
    """단일 폴더에서 취소 문서 처리."""
    folder_base_url = f"https://po.dongkoo.co.kr/app/approval/deptfolder/draft/{folder_id}"
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
        page.goto(folder_base_url)
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

    print(f"    취소 문서 {len(cancel_targets)}건 발견")

    for i, item in enumerate(cancel_targets):
        doc_id = item["doc_id"]
        if doc_id in processed_cancel_ids:
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

        detail_text = ""
        for frame in page.frames:
            try:
                for row in frame.query_selector_all("tr"):
                    cells = row.query_selector_all("th, td")
                    texts = [c.inner_text().strip().replace("\xa0", " ") for c in cells]
                    if len(texts) >= 2 and "상세내용" in texts[0]:
                        detail_text = " ".join(texts[1:])
                        break
                if detail_text:
                    break
                frame_text = frame.inner_text("body")
                if "계원-" in frame_text:
                    detail_text = frame_text
                    break
            except Exception:
                continue

        target_doc_id = extract_target_doc_id(detail_text)
        if target_doc_id:
            supabase.table("leave_records").delete().eq("doc_id", target_doc_id).execute()
            print(f"    취소 처리: {doc_id} → 삭제 {target_doc_id}")
        else:
            print(f"    취소 문서 {doc_id}: 문서번호 미발견")

        save_cancel_record(doc_id, target_doc_id or "")
        processed_cancel_ids.add(doc_id)
        cancel_count += 1

        try:
            page.click("button:has-text('목록'), a:has-text('목록')", timeout=2000)
        except Exception:
            page.go_back()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1000)

    return cancel_count


# ── 유저별 스크래핑 ──────────────────────────────────────────────────────────

def scrape_user(user_id, password, folders, existing_doc_ids, processed_cancel_ids):
    new_count = 0
    cancel_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("dialog", lambda d: d.dismiss())

        # 로그인
        page.goto(DAOU_URL)
        page.wait_for_load_state("domcontentloaded")
        page.fill("#username", user_id)
        page.fill("#password", password)
        page.click("input[type='submit'], button[type='submit'], .btn_login")
        try:
            page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        except Exception:
            print(f"  [{user_id}] 로그인 실패 — 비밀번호 확인 필요")
            browser.close()
            return 0, 0
        page.wait_for_timeout(2000)
        print(f"  [{user_id}] 로그인 성공")

        for folder in folders:
            folder_id   = str(folder.get("folder_id", "")).strip()
            filter_name = (folder.get("name") or "").strip()
            if not folder_id:
                continue
            label = f"폴더 {folder_id}" + (f" (필터: {filter_name})" if filter_name else " (전체 인원)")
            print(f"  → {label}")

            n = scrape_folder(page, folder_id, filter_name, existing_doc_ids, user_id)
            c = scrape_cancel_folder(page, folder_id, processed_cancel_ids)
            new_count    += n
            cancel_count += c

        browser.close()

    return new_count, cancel_count


# ── 메인 ────────────────────────────────────────────────────────────────────

def scrape():
    targets = load_scrape_targets()
    if not targets:
        print("스크래핑 대상 없음: daou_folders가 설정되고 비밀번호가 초기화되지 않은 계정이 없습니다.")
        return

    print(f"스크래핑 대상 {len(targets)}명: {[t['user_id'] for t in targets]}")

    processed_cancel_ids = load_processed_cancel_ids()
    total_new = 0
    total_cancel = 0

    for user in targets:
        existing_doc_ids = load_existing_doc_ids(user["user_id"])
        print(f"\n{'='*50}")
        print(f"유저: {user['user_id']} ({len(user['daou_folders'])}개 폴더, 기존 {len(existing_doc_ids)}건)")
        n, c = scrape_user(
            user["user_id"],
            user["password"],
            user["daou_folders"],
            existing_doc_ids,
            processed_cancel_ids,
        )
        total_new    += n
        total_cancel += c

    print(f"\n{'='*50}")
    print(f"완료: 신규 {total_new}건 저장, 취소 {total_cancel}건 처리")


if __name__ == "__main__":
    scrape()
