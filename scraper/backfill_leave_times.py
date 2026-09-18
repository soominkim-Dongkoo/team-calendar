# -*- coding: utf-8 -*-
"""
외근·출장 실제 시간 1회성 보정 (오늘 이후 · 하루짜리 · 시간 미입력분)

스크래퍼는 이미 저장된 문서번호를 건너뛰므로, 시간 저장 기능 이전에 들어온
외근·출장은 start_time/end_time 이 비어 있다. 해당 기안을 다시 열어 채운다.

선행:   leave_times_columns.sql 실행
사용법: python backfill_leave_times.py --dry-run   # 읽기만, DB 변경 없음
        python backfill_leave_times.py             # 실제 반영
"""
import sys
from collections import defaultdict
from datetime import date
from playwright.sync_api import sync_playwright
from scrape import supabase, supabase_admin, DAOU_URL, TIMED_TYPES, parse_leave_detail, parse_period

DRY = "--dry-run" in sys.argv


def main():
    rows = supabase.table("leave_records").select("*") \
        .in_("leave_type", list(TIMED_TYPES)).gte("start_date", date.today().isoformat()) \
        .execute().data or []

    # 문서당 한 번만 열도록, 처음 만난 수집 계정에 배정
    by_owner, seen = defaultdict(list), set()
    for r in rows:
        if r["start_date"] != r["end_date"] or r.get("start_time") or r["doc_id"] in seen:
            continue
        seen.add(r["doc_id"])
        by_owner[r["owner_user_id"]].append(r)

    total = sum(len(v) for v in by_owner.values())
    print(f"보정 대상 {total}건 ({'dry-run' if DRY else '반영'})")

    done = 0
    with sync_playwright() as p:
        for uid, docs in by_owner.items():
            pw = supabase_admin.rpc("get_daou_password", {"p_user_id": uid}).execute().data
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("dialog", lambda d: d.dismiss())
            page.goto(DAOU_URL)
            page.wait_for_load_state("domcontentloaded")
            page.fill("#username", uid)
            page.fill("#password", pw)
            page.click("input[type='submit'], button[type='submit'], .btn_login")
            try:
                page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
            except Exception:
                print(f"  [{uid}] 로그인 실패 — {len(docs)}건 스킵")
                browser.close()
                continue
            page.wait_for_timeout(1500)
            if "passwordChange" in page.url:
                print(f"  [{uid}] 비밀번호 변경 요구 화면 — {len(docs)}건 스킵")
                browser.close()
                continue

            for r in docs:
                page.goto(r["doc_url"])
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1500)
                detail = parse_leave_detail(page)
                if detail.get("doc_id") != r["doc_id"]:
                    print(f"  {r['doc_id']}: 문서 열기 실패 — 스킵")
                    continue
                per = parse_period(detail.get("period_raw", ""))
                if not per["start_time"] or not per["end_time"]:
                    print(f"  {r['doc_id']}: 시간 없음 ({detail.get('period_raw')!r}) — 스킵")
                    continue
                print(f"  {r['doc_id']} {r['name']} {r['leave_type']} {r['start_date']} "
                      f"→ {per['start_time']} ~ {per['end_time']}")
                if not DRY:
                    supabase.table("leave_records").update({
                        "start_time": per["start_time"],
                        "end_time":   per["end_time"],
                    }).eq("doc_id", r["doc_id"]).execute()   # 같은 문서의 모든 수집분
                done += 1
            browser.close()

    print(f"완료: {done}/{total}건{' (dry-run, DB 변경 없음)' if DRY else ''}")


if __name__ == "__main__":
    main()
