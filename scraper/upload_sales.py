# -*- coding: utf-8 -*-
"""
DRM 보호된 엑셀 파일을 xlwings(Excel COM)로 읽어 Supabase sales_data에 업로드.
로컬 PC (Windows, Excel 설치 필요) 에서 실행.

사용법:
  python upload_sales.py              → 기본 경로(../Sales_2026.xlsx) 사용
  python upload_sales.py "C:\path\to\file.xlsx"  → 경로 직접 지정

의존성:
  pip install xlwings supabase python-dotenv
"""
import os
import re
import sys

import xlwings as xw
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def upload_sales(xlsx_path: str):
    print(f"파일 열기: {xlsx_path}")

    # Excel 앱을 통해 열기 → DRM 자동 해제
    app = xw.App(visible=False, add_book=False)
    try:
        wb  = app.books.open(xlsx_path)
        ws  = wb.sheets[0]
        rows = ws.used_range.value  # list of lists (헤더 포함)
    finally:
        app.quit()  # wb.close() 대신 app.quit()으로 저장 팝업 방지

    if not rows or len(rows) < 2:
        print("데이터 없음")
        return

    daily_sales   = {}
    daily_returns = {}

    # JS handleSalesUpload 와 동일한 컬럼 인덱스 (0-based)
    # row[5]  = F열 : 구분 (판매 / 반품)
    # row[6]  = G열 : 문서번호 (앞 8자리 = YYYYMMDD)
    # row[10] = K열 : 금액
    for row in rows[1:]:
        if not row or len(row) < 11:
            continue
        gubun  = row[5]
        doc_no = row[6]
        amount = row[10]

        if not doc_no or amount is None:
            continue

        ds = re.sub(r'\D', '', str(doc_no))[:8]
        if len(ds) != 8:
            continue

        date_str = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
        amt = abs(float(amount))

        if str(gubun).strip() == '반품':
            daily_returns[date_str] = daily_returns.get(date_str, 0) + amt
        else:
            daily_sales[date_str] = daily_sales.get(date_str, 0) + amt

    all_dates = set(daily_sales) | set(daily_returns)
    if not all_dates:
        print("처리할 데이터 없음")
        return

    to_upsert = [
        {
            "sale_date": d,
            "amount":    round(daily_sales.get(d, 0)),
            "returns":   round(daily_returns.get(d, 0)),
        }
        for d in sorted(all_dates)
    ]

    print(f"{len(to_upsert)}일 데이터 업로드 중...")
    supabase.table("sales_data").upsert(to_upsert, on_conflict="sale_date").execute()
    print(f"완료: {len(to_upsert)}일 업로드됨")
    for r in to_upsert:
        sales_억 = r['amount'] / 1e8
        ret_억   = r['returns'] / 1e8
        print(f"  {r['sale_date']}  판매 {sales_억:.2f}억  반품 {ret_억:.2f}억")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = os.path.join(os.path.dirname(__file__), '..', 'Sales_2026.xlsx')

    path = os.path.abspath(path)
    if not os.path.exists(path):
        print(f"파일 없음: {path}")
        sys.exit(1)

    upload_sales(path)
