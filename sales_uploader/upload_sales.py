# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import json
import urllib.request

SUPABASE_URL = 'https://vfqqwnkfszlrrekfejry.supabase.co'
SUPABASE_KEY = 'sb_publishable_4OImNxzBy7jz236aI0TDkg_ye_vLU9L'

GUBUN_COL  = 5
DOCNO_COL  = 6
AMOUNT_COL = 10


def read_excel(filepath):
    import xlwings as xw
    app = xw.App(visible=False)
    try:
        wb = app.books.open(filepath)
        ws = wb.sheets[0]
        rows = ws.range('A1').expand().value
        return rows
    finally:
        try: wb.close()
        except: pass
        try: app.quit()
        except: pass


def parse_rows(rows):
    daily_sales = {}
    daily_returns = {}
    for row in rows[1:]:
        if not row or len(row) <= AMOUNT_COL:
            continue
        gubun  = row[GUBUN_COL]
        doc_no = row[DOCNO_COL]
        amount = row[AMOUNT_COL]
        if not doc_no or amount is None:
            continue
        ds = ''.join(c for c in str(doc_no) if c.isdigit())[:8]
        if len(ds) != 8:
            continue
        date_str = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
        amt = abs(float(amount))
        if str(gubun).strip() == '반품':
            daily_returns[date_str] = daily_returns.get(date_str, 0) + amt
        else:
            daily_sales[date_str] = daily_sales.get(date_str, 0) + amt

    all_dates = set(list(daily_sales.keys()) + list(daily_returns.keys()))
    return [
        {
            'sale_date': d,
            'amount':  round(daily_sales.get(d, 0)),
            'returns': round(daily_returns.get(d, 0)),
        }
        for d in sorted(all_dates)
    ]


def upload_to_supabase(records):
    url = f"{SUPABASE_URL}/rest/v1/sales_data"
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates',
    }
    body = json.dumps(records, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.status


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("매출 데이터 업로드")
        self.resizable(False, False)
        self.geometry("500x230")
        self._build()

    def _build(self):
        tk.Label(self, text="매출 엑셀 파일 (DRM 자동 해제)", font=("맑은 고딕", 11, "bold")).pack(anchor='w', padx=16, pady=(16, 6))

        row = tk.Frame(self)
        row.pack(fill='x', padx=16)
        self.path_var = tk.StringVar()
        tk.Entry(row, textvariable=self.path_var, state='readonly', width=48).pack(side='left', fill='x', expand=True)
        tk.Button(row, text="파일 찾기", command=self._browse).pack(side='left', padx=(8, 0))

        self.status_var = tk.StringVar(value="파일을 선택해주세요.")
        tk.Label(self, textvariable=self.status_var, fg='gray', font=("맑은 고딕", 9)).pack(anchor='w', padx=16, pady=(12, 4))

        self.bar = ttk.Progressbar(self, mode='indeterminate', length=468)
        self.bar.pack(padx=16)

        self.btn = tk.Button(
            self, text="업로드", font=("맑은 고딕", 11),
            bg="#4361ee", fg="white", activebackground="#3451d1", activeforeground="white",
            width=14, state='disabled', command=self._upload,
        )
        self.btn.pack(pady=14)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="엑셀 파일 선택",
            filetypes=[("Excel 파일", "*.xlsx *.xls"), ("모든 파일", "*.*")],
        )
        if path:
            self.path_var.set(path)
            self.btn['state'] = 'normal'
            self.status_var.set("업로드 버튼을 눌러 진행하세요.")

    def _upload(self):
        path = self.path_var.get()
        if not path:
            return
        self.btn['state'] = 'disabled'
        self.bar.start(12)
        self.status_var.set("Excel 열기 중... (DRM 해제 포함, 잠시 대기)")
        threading.Thread(target=self._run, args=(path,), daemon=True).start()

    def _run(self, path):
        try:
            rows = read_excel(path)
            n = len(rows) if rows else 0
            self.after(0, lambda: self.status_var.set(f"파싱 중... (총 {n}행)"))
            records = parse_rows(rows)
            if not records:
                self.after(0, lambda: self._done(False, "데이터를 찾을 수 없습니다. 파일 형식을 확인하세요."))
                return
            cnt = len(records)
            self.after(0, lambda: self.status_var.set(f"Supabase 업로드 중... ({cnt}일치 데이터)"))
            upload_to_supabase(records)
            self.after(0, lambda: self._done(True, f"{cnt}일치 매출 데이터 업로드 완료!"))
        except Exception as e:
            msg = str(e)
            self.after(0, lambda: self._done(False, f"오류: {msg}"))

    def _done(self, ok, msg):
        self.bar.stop()
        self.btn['state'] = 'normal'
        self.status_var.set(msg)
        if ok:
            messagebox.showinfo("완료", msg)
        else:
            messagebox.showerror("실패", msg)


if __name__ == '__main__':
    App().mainloop()
