# -*- coding: utf-8 -*-
import sys
import os

# PyInstaller --onefile: DLL 검색 경로 등록
if getattr(sys, 'frozen', False):
    base = sys._MEIPASS
    os.environ['PATH'] = base + os.pathsep + os.environ.get('PATH', '')
    if hasattr(os, 'add_dll_directory'):
        os.add_dll_directory(base)
        for _p in sys.path:
            if _p and os.path.isdir(_p):
                try: os.add_dll_directory(_p)
                except: pass

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import json
import urllib.request

SUPABASE_URL = 'https://vfqqwnkfszlrrekfejry.supabase.co'
SUPABASE_KEY = 'sb_publishable_4OImNxzBy7jz236aI0TDkg_ye_vLU9L'
VERCEL_BASE  = 'https://team-calendar-ten.vercel.app'

PRODUCT_COL = 1
GUBUN_COL   = 5
DOCNO_COL   = 6
AMOUNT_COL  = 10

BG      = '#f1f5f9'
SURFACE = '#ffffff'
PRIMARY = '#4361ee'
TEXT    = '#1e293b'
MUTED   = '#94a3b8'
BORDER  = '#e2e8f0'
SUCCESS = '#16a34a'
ERROR   = '#dc2626'


def read_excel(filepath):
    import win32com.client
    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible = False
    xl.DisplayAlerts = False
    try:
        wb = xl.Workbooks.Open(filepath, UpdateLinks=0, ReadOnly=True)
        ws = wb.Worksheets(1)
        data = ws.UsedRange.Value
        wb.Close(False)
        if data is None:
            return []
        if not isinstance(data[0], tuple):
            return [[v] for v in data]
        return [list(r) for r in data]
    finally:
        try: xl.Quit()
        except: pass


def parse_rows(rows):
    daily_sales = {}
    daily_returns = {}
    product_sales = {}
    product_returns = {}
    for row in rows[1:]:
        if not row or len(row) <= AMOUNT_COL:
            continue
        product = str(row[PRODUCT_COL] or '').strip() or '기타'
        gubun   = row[GUBUN_COL]
        doc_no  = row[DOCNO_COL]
        amount  = row[AMOUNT_COL]
        if not doc_no or amount is None:
            continue
        ds = ''.join(c for c in str(doc_no) if c.isdigit())[:8]
        if len(ds) != 8:
            continue
        date_str = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
        amt = abs(float(amount))
        key = (date_str, product)
        if str(gubun).strip() == '반품':
            daily_returns[date_str] = daily_returns.get(date_str, 0) + amt
            product_returns[key] = product_returns.get(key, 0) + amt
        else:
            daily_sales[date_str] = daily_sales.get(date_str, 0) + amt
            product_sales[key] = product_sales.get(key, 0) + amt
    all_dates = set(list(daily_sales.keys()) + list(daily_returns.keys()))
    daily = [
        {'sale_date': d, 'amount': round(daily_sales.get(d, 0)), 'returns': round(daily_returns.get(d, 0))}
        for d in sorted(all_dates)
    ]
    all_keys = set(list(product_sales.keys()) + list(product_returns.keys()))
    detail = [
        {'sale_date': k[0], 'product': k[1],
         'amount': round(product_sales.get(k, 0)), 'returns': round(product_returns.get(k, 0))}
        for k in all_keys
    ]
    return daily, detail


def upload_to_supabase(records, detail):
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'x-app-token': 'dkbio-cal-2026',
    }
    dates = [r['sale_date'] for r in records]
    min_date, max_date = min(dates), max(dates)
    date_range = f"?sale_date=gte.{min_date}&sale_date=lte.{max_date}"

    def delete(table):
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/{table}{date_range}",
            headers=headers, method='DELETE')
        urllib.request.urlopen(req, timeout=30)

    def insert(table, rows):
        body = json.dumps(rows, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/{table}",
            data=body, headers={**headers, 'Prefer': 'return=minimal'}, method='POST')
        urllib.request.urlopen(req, timeout=30)

    delete('sales_data')
    insert('sales_data', records)
    if detail:
        delete('sales_detail')
        insert('sales_detail', detail)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("매출 데이터 업로드")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._full_path = None
        w, h = 500, 240
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self._apply_style()
        self._build()

    def _apply_style(self):
        ttk.Style(self).theme_use('clam')

    def _build(self):
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill='both', expand=True, padx=20, pady=20)

        card = tk.Frame(outer, bg=SURFACE,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill='both', expand=True)

        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill='both', expand=True, padx=24, pady=20)

        tk.Label(inner, text="엑셀 파일", font=("맑은 고딕", 8, "bold"),
                 bg=SURFACE, fg=MUTED).pack(anchor='w', pady=(0, 5))

        file_row = tk.Frame(inner, bg=SURFACE)
        file_row.pack(fill='x', pady=(0, 14))

        entry_frame = tk.Frame(file_row, bg=BORDER)
        entry_frame.pack(side='left', fill='x', expand=True, padx=(0, 8))

        self.path_var = tk.StringVar(value="파일을 선택해주세요...")
        self._path_entry = tk.Entry(
            entry_frame, textvariable=self.path_var, state='readonly',
            font=("맑은 고딕", 9), bg=SURFACE, fg=MUTED,
            relief='flat', bd=0, readonlybackground=SURFACE,
        )
        self._path_entry.pack(fill='x', padx=10, pady=7)

        tk.Button(file_row, text="파일 찾기", font=("맑은 고딕", 9),
                  bg=PRIMARY, fg='white', activebackground='#3451d1', activeforeground='white',
                  relief='flat', bd=0, padx=14, pady=6, cursor='hand2',
                  command=self._browse).pack(side='right')

        self.status_var = tk.StringVar(value="파일을 선택하면 업로드 버튼이 활성화됩니다.")
        self.status_lbl = tk.Label(inner, textvariable=self.status_var,
                                    font=("맑은 고딕", 9), bg=SURFACE, fg=MUTED, anchor='w')
        self.status_lbl.pack(fill='x', pady=(0, 16))

        self.btn = tk.Button(
            inner, text="업로드", font=("맑은 고딕", 11, "bold"),
            bg='#cbd5e1', fg='white', activebackground=PRIMARY, activeforeground='white',
            relief='flat', bd=0, pady=10, cursor='hand2',
            state='disabled', command=self._upload,
        )
        self.btn.pack(fill='x')

    def _browse(self):
        path = filedialog.askopenfilename(
            title="엑셀 파일 선택",
            filetypes=[("Excel 파일", "*.xlsx *.xls"), ("모든 파일", "*.*")],
        )
        if path:
            self._full_path = path
            self.path_var.set(path)
            self._path_entry.configure(fg=TEXT)
            self._path_entry.xview_moveto(1)
            self.btn.configure(state='normal', bg=PRIMARY)
            self._set_status("업로드 버튼을 눌러 진행하세요.", MUTED)

    def _upload(self):
        if not self._full_path:
            return
        self.btn.configure(state='disabled', bg='#cbd5e1')
        self._set_status("파일 열기 중...", MUTED)
        self.update()
        try:
            self._set_status("데이터 읽는 중...", MUTED)
            self.update()
            rows = read_excel(self._full_path)
            n = len(rows) if rows else 0
            self._set_status(f"데이터 파싱 중... (총 {n}행)", MUTED)
            self.update()
            records, detail = parse_rows(rows)
            if not records:
                self._done(False, "데이터를 찾을 수 없습니다. 파일 형식을 확인하세요.")
                return
            cnt = len(records)
            self._set_status(f"업로드 중... ({cnt}일치)", MUTED)
            self.update()
            from datetime import date as _date
            today_iso = _date.today().isoformat()
            today_recs = [r for r in records if r['sale_date'] == today_iso]

            # 업로드 전 오늘 기존 값 조회
            prev_net = None
            if today_recs:
                try:
                    _h = {'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}', 'x-app-token': 'dkbio-cal-2026'}
                    _url = f"{SUPABASE_URL}/rest/v1/sales_data?sale_date=eq.{today_iso}&select=amount,returns"
                    with urllib.request.urlopen(urllib.request.Request(_url, headers=_h), timeout=10) as _r:
                        _existing = json.loads(_r.read().decode())
                    prev_net = sum((x.get('amount') or 0) - (x.get('returns') or 0) for x in _existing) if _existing else None
                except Exception:
                    pass

            upload_to_supabase(records, detail)

            # 오늘 값이 실제 변경됐을 때만 알림 발송
            if today_recs:
                new_net = sum(r['amount'] - (r.get('returns') or 0) for r in today_recs)
                if prev_net != new_net:
                    try:
                        req = urllib.request.Request(
                            f'{VERCEL_BASE}/api/notify-sales', method='POST',
                            headers={'Content-Type': 'application/json'},
                            data=b'{}',
                        )
                        urllib.request.urlopen(req, timeout=10)
                    except Exception:
                        pass
            self._done(True, f"{cnt}일치 매출 데이터가 업로드되었습니다.")
        except Exception as e:
            self._done(False, f"오류: {e}")

    def _done(self, ok, msg):
        self.btn.configure(state='normal', bg=PRIMARY)
        self._set_status(msg, SUCCESS if ok else ERROR)
        if ok:
            messagebox.showinfo("완료", msg)
        else:
            messagebox.showerror("실패", msg)

    def _set_status(self, msg, color=MUTED):
        self.status_var.set(msg)
        self.status_lbl.configure(fg=color)


if __name__ == '__main__':
    App().mainloop()
