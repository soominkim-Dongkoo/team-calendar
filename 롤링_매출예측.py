# -*- coding: utf-8 -*-
"""
영업일 롤링 Nowcast + 보정  (일별 DB 자동 합산)
------------------------------------------------
매 영업일마다 그 달 월매출 추정을 갱신. 롤링 베이스 위에 3가지 보정을 얹는다.

  추정(N) = [ (1~N영업일 누적매출) / 누적비중 p(N) ]           # 롤링 베이스
          x 월구조(게이트) x [1+β_wd·(영업일수편차)] x [1+β_nx·(다음달휴가편차)]  # 보정

- p(N): 과거 완결월의 '1~N영업일 누적/월총액' 평균. N 커질수록 100%에 근접 → 추정이 실제에 수렴.
- 보정은 N 시점마다 재학습(월구조는 신뢰도 게이트, 영업일수·다음달휴가는 회귀). N 커질수록 자동으로 약해짐.
- 짧은 달(영업일 적은 달) 과대추정을 영업일수 보정(β_wd)이 잡아줌.
- 다음달휴가 = '다음달 첫 3평일(주말 제외) 중 휴일/휴가 수'. 다음달 초가 막히면 전달 과발주 → 이번달↑.
  (현재 데이터로는 휴가 사건이 드물어 기여 미미하나, 인과적으로 맞는 feature. 연도 쌓이면 강해짐.)
- 소스: ERP work8 의 DB_grouped(일별, 2020~) + Working Day(영업일/휴일). DRM -> xlwings. 로딩 수십 초.

사용법 (PowerShell):
    python 롤링_매출예측.py                    # 최신 월 롤링 추정 + 비중표
    python 롤링_매출예측.py --month 2026 5      # 특정 월
    python 롤링_매출예측.py --backtest          # N영업일 시점별 적중률 (롤링 vs 롤링+보정)
"""
import argparse
import io
import sys
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

WB_PATH = r"c:\Users\DKBIO\Desktop\경영기획팀\AI\Claude\매출추정\ERP_매출 추정_work8.xlsx"
DB_SHEET = "DB_grouped"      # 일별(연,월,영업일,매출금액)
WD_SHEET = "Working Day"     # 영업일/휴일 달력
AMT_COL = 14                 # DB_grouped 매출금액 컬럼(N)
START_N = 3                  # 추정 시작 영업일차 (기본 베이스가 3영업일 기준이라 3일차부터)
CORR_MIN_N = 5               # 월구조·다음달휴가 보정 시작 영업일차 (초반엔 노이즈라 base만)
WD_REF_N = 6                 # 3~4일차 영업일수 보정에 쓸 '안정 β_wd'를 학습할 기준 영업일차
PROP_YEARS = 3               # p(N)·보정 학습 기간: 최근 N년(비중이 최근 상승 추세라 최근값 추종)
APPLY_MONTHS = {1, 2, 5, 6}  # 월전용 비중 적용 달(초반 몰림 달). 이 달들은 p(N)을 해당월 전체이력으로.
EARLY_SQUISH = {1: 0.88, 2: 0.86}  # 3~4일차 초반 과대추정 억제 하향계수(월별). 5일차(CORR_MIN_N)부터 1.0 복귀.
SMOOTH_FROM_N = 4            # 이 영업일차부터 START_N~N 전체 raw 추정값 누적평균 (스파이크 자연 희석)
MIN_COMPLETE_WD = 15         # 완결월 판정
MAXN = 20                    # 누적비중/보정 학습 최대 영업일
DAMPING = 0.20               # 월구조 보정 강도 (0=고정만, 1=풀보정). 0.2로 완만화(초기 과보정 억제).
GATE_W = 0.25                # 월구조 신뢰 게이트 (w<GATE_W 이면 그 달 OFF)
NEXT_WEEKDAYS = 3            # 다음달휴가 판정: 다음달 첫 N평일(주말 제외) 중 휴일 수


def load_data(path=WB_PATH):
    """DB_grouped(일별 누적용) + Working Day(영업일수/다음달휴가)를 한 번에 읽는다."""
    import xlwings as xw
    app = xw.App(visible=False)
    try:
        wb = app.books.open(path, update_links=False, read_only=True)
        db = wb.sheets[DB_SHEET]
        n = db.used_range.last_cell.row
        yr = db.range((2, 1), (n, 1)).value
        mo = db.range((2, 2), (n, 2)).value
        wdd = db.range((2, 4), (n, 4)).value
        amt = db.range((2, AMT_COL), (n, AMT_COL)).value
        wdrows = wb.sheets[WD_SHEET].used_range.value
    finally:
        try:
            wb.close()
        except Exception:
            pass
        app.quit()

    # 일별 -> {(y,m): {영업일: 매출}}
    months = {}
    for y, m, w, a in zip(yr, mo, wdd, amt):
        if y is None or w is None or a is None:
            continue
        try:
            y, m, w = int(y), int(m), int(w)
        except (ValueError, TypeError):
            continue
        months.setdefault((y, m), {})
        months[(y, m)][w] = months[(y, m)].get(w, 0.0) + float(a)

    # 달력 -> 월별 총영업일수, '첫 3평일 중 휴일 수'
    month_wd = {}
    caldays = defaultdict(list)   # (y,m) -> [(캘린더일, 요일, 영업일번호)]
    for r in wdrows[1:]:
        if r[0] is None:
            continue
        y, m, weekday, w = int(r[0]), int(r[1]), r[3], r[4]
        if isinstance(w, (int, float)):
            month_wd[(y, m)] = max(month_wd.get((y, m), 0), int(w))
        try:
            d = int(str(r[2])[8:10])
        except (ValueError, TypeError):
            continue
        caldays[(y, m)].append((d, weekday, w))

    early = {}   # (y,m) -> 첫 NEXT_WEEKDAYS 평일(주말 제외) 중 휴일(비영업) 수
    for (y, m), days in caldays.items():
        days.sort()
        weekdays = [t for t in days if t[1] not in ("토요일", "일요일")]
        early[(y, m)] = sum(1 for t in weekdays[:NEXT_WEEKDAYS]
                            if not isinstance(t[2], (int, float)))
    return months, month_wd, early, caldays


def upload_to_supabase(months, month_wd, early, caldays):
    """forecast_sales_history / forecast_month_meta / forecast_date_wd 테이블에 upsert."""
    import os
    from dotenv import load_dotenv
    from supabase import create_client

    script_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(script_dir, "scraper", ".env"), override=True)
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_KEY 없음. scraper/.env 확인 필요.")
        return
    sb = create_client(url, key)
    CHUNK = 500

    # 1. forecast_sales_history
    rows = [
        {"year": int(y), "month": int(m), "business_day_num": int(w), "amount": float(a)}
        for (y, m), wd in months.items()
        for w, a in wd.items()
    ]
    for i in range(0, len(rows), CHUNK):
        sb.table("forecast_sales_history").upsert(rows[i:i + CHUNK]).execute()
    print(f"forecast_sales_history: {len(rows)}행 업로드")

    # 2. forecast_month_meta
    meta = [
        {"year": int(y), "month": int(m),
         "total_business_days": int(tw),
         "early_holidays": int(early.get((y, m), 0))}
        for (y, m), tw in month_wd.items()
    ]
    for i in range(0, len(meta), CHUNK):
        sb.table("forecast_month_meta").upsert(meta[i:i + CHUNK]).execute()
    print(f"forecast_month_meta: {len(meta)}행 업로드")

    # 3. forecast_date_wd (영업일인 날만)
    date_rows = []
    for (y, m), days in caldays.items():
        for d, _weekday, w in days:
            if isinstance(w, (int, float)):
                date_rows.append({
                    "sale_date": f"{int(y):04d}-{int(m):02d}-{int(d):02d}",
                    "business_day_num": int(w)
                })
    for i in range(0, len(date_rows), CHUNK):
        sb.table("forecast_date_wd").upsert(date_rows[i:i + CHUNK]).execute()
    print(f"forecast_date_wd: {len(date_rows)}행 업로드")
    print("업로드 완료.")


def load_data_from_supabase():
    """Supabase에서 데이터 로드.
    - 역사 학습 데이터: forecast_sales_history (ERP 기반, 영업일 번호 키)
    - 당해년도 실매출: sales_data (sales_uploader.exe가 올린 일별 데이터)
      → 평일(월~금)을 월별로 정렬해 영업일 번호 부여 (휴일은 금액 0으로 자연스럽게 반영)
    - month_wd / early: forecast_month_meta
    - caldays: forecast_date_wd (없으면 sales_data에서 재구성)
    """
    import os
    from datetime import datetime
    from collections import defaultdict
    from dotenv import load_dotenv
    from supabase import create_client

    script_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(script_dir, "scraper", ".env"), override=True)
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY 없음. scraper/.env 확인 필요.")
    sb = create_client(url, key)

    def fetch_all(table, cols, filters=None):
        rows, start = [], 0
        while True:
            q = sb.table(table).select(cols)
            if filters:
                for col, op, val in filters:
                    q = getattr(q, op)(col, val)
            res = q.range(start, start + 999).execute()
            rows.extend(res.data or [])
            if len(res.data or []) < 1000:
                break
            start += 1000
        return rows

    # 1. 역사 학습 데이터: forecast_sales_history
    months = {}
    for r in fetch_all("forecast_sales_history", "year,month,business_day_num,amount"):
        k = (int(r['year']), int(r['month']))
        months.setdefault(k, {})
        months[k][int(r['business_day_num'])] = float(r['amount'])

    # 2. sales_data → 당해년도 영업일별 매출 (forecast_sales_history에 없는 월 보완)
    #    평일(월~금)을 월별 정렬해 1, 2, 3... 번호 부여
    hist_keys = set(months.keys())
    sd_rows = fetch_all("sales_data", "sale_date,amount,returns")
    sd_by_month = defaultdict(list)   # (y,m) → [(date_str, net)]
    for r in sd_rows:
        dt = datetime.strptime(r['sale_date'], "%Y-%m-%d")
        if dt.weekday() >= 5:          # 주말 제외
            continue
        net = float(r.get('amount') or 0) - float(r.get('returns') or 0)
        sd_by_month[(dt.year, dt.month)].append((r['sale_date'], net))
    from datetime import date as _date
    cur_year = _date.today().year
    for k, entries in sd_by_month.items():
        if k in hist_keys and k[0] != cur_year:
            continue                   # 과거 연도는 forecast_sales_history 우선
        entries.sort()
        months[k] = {i + 1: net for i, (_, net) in enumerate(entries)}
        print(f"  sales_data {'갱신' if k in hist_keys else '보완'}: {k[0]}-{k[1]:02d} ({len(entries)}영업일)")

    # 3. month_wd + early
    month_wd, early = {}, {}
    for r in fetch_all("forecast_month_meta", "year,month,total_business_days,early_holidays"):
        k = (int(r['year']), int(r['month']))
        month_wd[k] = int(r['total_business_days'])
        early[k] = int(r['early_holidays'])

    # sales_data 보완 월은 month_wd가 없으면 평일 수로 추정
    for k, wd in months.items():
        if k not in month_wd and max(wd) > 0:
            month_wd[k] = max(wd)

    # 4. caldays: forecast_date_wd 우선, 없으면 sales_data에서 재구성
    caldays = defaultdict(list)
    try:
        for r in fetch_all("forecast_date_wd", "sale_date,business_day_num"):
            parts = r['sale_date'].split('-')
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            caldays[(y, m)].append((d, None, int(r['business_day_num'])))
        print(f"  forecast_date_wd 로드: {sum(len(v) for v in caldays.values())}행")
    except Exception:
        # forecast_date_wd 없으면 sales_data 평일 목록으로 재구성
        for k, entries in sd_by_month.items():
            for i, (ds, _) in enumerate(sorted(entries)):
                d = int(ds.split('-')[2])
                caldays[k].append((d, None, i + 1))
        print("  ⚠️  forecast_date_wd 없음 → sales_data 평일로 late_start 재구성")

    print(f"Supabase 로딩 완료: {min(months)[0]}~{max(months)[0]}, {len(months)}개월")
    return months, month_wd, early, caldays


def cum(wd, N):
    return sum(s for w, s in wd.items() if w <= N)


def total(wd):
    return sum(wd.values())


def compute_late_start(caldays):
    """월별 21일 이후 첫 영업일 번호 (이 시점부터 구간인식 p(N)으로 전환)."""
    result = {}
    for (y, m), days in caldays.items():
        for d, _weekday, w in sorted(days):
            if d >= 21 and isinstance(w, (int, float)):
                result[(y, m)] = int(w)
                break
    return result


def compute_zone_weights(train, month_wd, late_start, month=None):
    """4구간(~3일/~20일/~23일/~30일) 역사 비중 평균. month 지정 시 해당 월 전용."""
    import numpy as np
    rows = []
    for (y, m), v in train.items():
        if month is not None and m != month:
            continue
        tw = month_wd.get((y, m))
        ls = late_start.get((y, m))
        if not tw or not ls or ls <= 3 or total(v) == 0:
            continue
        z3_end = min(ls + 2, tw)
        t = total(v)
        w1 = cum(v, 3) / t
        w2 = (cum(v, ls - 1) - cum(v, 3)) / t
        w3 = (cum(v, z3_end) - cum(v, ls - 1)) / t
        w4 = 1.0 - w1 - w2 - w3
        rows.append([w1, w2, w3, w4])
    if len(rows) < 2:
        return [0.263, 0.325, 0.265, 0.146]  # 3년 평균 fallback
    return list(np.array(rows).mean(axis=0))


def zone_aware_prop(N, ls, tw, weights):
    """구간인식 p(N): ~3일/~20일/~23일/~30일 구간 내 위치로 누적비중 선형보간."""
    w1, w2, w3, w4 = weights
    z3_end = min(ls + 2, tw) if tw else ls + 2
    z2_size = max(ls - 4, 1)          # BD 4~(ls-1) 개수
    z3_size = max(z3_end - ls + 1, 1) # 최대 3
    z4_size = max(tw - z3_end, 1) if tw else 1

    if N <= 3:
        return (N / 3) * w1
    elif N < ls:  # ~20일 구간
        return w1 + ((N - 3) / z2_size) * w2
    elif N <= z3_end:  # ~23일 구간 (스파이크)
        return w1 + w2 + ((N - ls + 1) / z3_size) * w3
    else:  # ~30일 구간
        return w1 + w2 + w3 + ((N - z3_end) / z4_size) * w4


def next_nonwork(y, m, early):
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return early.get((ny, nm))


def complete_months(months, exclude=None):
    return {k: v for k, v in months.items() if max(v) >= MIN_COMPLETE_WD and k != exclude}


def recent_train(months, ref_year, exclude=None):
    """학습용: ref_year 직전 PROP_YEARS개 완결연도의 완결월 (최근값 추종)."""
    return {k: v for k, v in months.items()
            if ref_year - PROP_YEARS <= k[0] <= ref_year - 1
            and max(v) >= MIN_COMPLETE_WD and k != exclude}


def prop_at(train, N):
    import numpy as np
    vals = [cum(v, N) / total(v) for v in train.values() if total(v) > 0]
    return float(np.mean(vals)) if vals else float("nan")


def month_prop(months, ref_year, N, m):
    """월전용 비중: 해당 월의 전체 이력(ref_year 미만 완결월) 평균. 표본 없으면 None."""
    import numpy as np
    tr = [v for k, v in months.items()
          if k[1] == m and k[0] < ref_year and max(v) >= MIN_COMPLETE_WD]
    return float(np.mean([cum(v, N) / total(v) for v in tr])) if tr else None


def base_prop(months, ref_year, N, m, overall_pN):
    """대상 달(APPLY_MONTHS)은 월전용 비중, 그 외는 전체(최근 PROP_YEARS년) 비중."""
    if m in APPLY_MONTHS:
        mp = month_prop(months, ref_year, N, m)
        if mp is not None:
            return mp
    return overall_pN


def learn_corr(train, N, months, ref_year, overall_pN, month_wd, early):
    """N 시점의 보정 학습: 월구조(게이트) + β_wd + β_nx. 각 월은 자기 base 비중 사용."""
    import numpy as np
    recs = []  # (month, ideal_corr, total_wd, next_nonwork)
    for (y, m), v in train.items():
        tw = month_wd.get((y, m))
        nn = next_nonwork(y, m, early)
        if tw is None or nn is None or cum(v, N) <= 0:
            continue
        base = cum(v, N) / base_prop(months, ref_year, N, m, overall_pN)
        recs.append((m, total(v) / base, tw, nn))

    ms, mw, mn = {}, {}, {}
    for mm in range(1, 13):
        ic = np.array([r[1] for r in recs if r[0] == mm]) if any(r[0] == mm for r in recs) else np.array([1.0])
        mean = float(ic.mean())
        if len(ic) >= 2:
            se = ic.std(ddof=1) / np.sqrt(len(ic))
            sig = abs(mean - 1)
            w = sig ** 2 / (sig ** 2 + se ** 2) if (sig ** 2 + se ** 2) > 0 else 0.0
        else:
            w = 0.0
        ms[mm] = 1 + (DAMPING if w > GATE_W else 0.0) * (mean - 1)
        mw[mm] = np.mean([r[2] for r in recs if r[0] == mm]) if any(r[0] == mm for r in recs) else 20.0
        mn[mm] = np.mean([r[3] for r in recs if r[0] == mm]) if any(r[0] == mm for r in recs) else 2.0

    def slope(idx):
        xd, yd = [], []
        for mm in range(1, 13):
            ts = [r for r in recs if r[0] == mm]
            if len(ts) < 2:
                continue
            mx = np.mean([t[idx] for t in ts])
            mc = np.mean([t[1] for t in ts])
            for t in ts:
                xd.append(t[idx] - mx)
                yd.append(t[1] - mc)
        xd, yd = np.array(xd), np.array(yd)
        return float(np.sum(xd * yd) / np.sum(xd * xd)) if xd.size and np.sum(xd * xd) > 0 else 0.0

    return {"ms": ms, "mw": mw, "mn": mn, "bwd": slope(2), "bnx": slope(3)}


def correction(m, tw, nn, C):
    return C["ms"][m] * (1 + C["bwd"] * (tw - C["mw"][m])) * (1 + C["bnx"] * (nn - C["mn"][m]))


def early_squish(m, N):
    """1·2월 3~4일차(보정 전 구간)만 월별 gentle 하향. 5일차부터 1.0."""
    return EARLY_SQUISH.get(m, 1.0) if N < CORR_MIN_N else 1.0


def raw_estimate(v, months, month_wd, early, train, C_ref, year, month, N):
    """N영업일차 단일 추정: 비중 나눗셈 + (월전용비중/찌그림/보정). 스무딩 전 원값."""
    overall_pN = prop_at(train, N)
    base = cum(v, N) / base_prop(months, year, N, month, overall_pN)
    tw = month_wd.get((year, month))
    nn = next_nonwork(year, month, early)
    if tw is None or nn is None:
        return base * early_squish(month, N)
    if N >= CORR_MIN_N:
        C = learn_corr(train, N, months, year, overall_pN, month_wd, early)
        return base * correction(month, tw, nn, C)
    if month in APPLY_MONTHS:
        return base * early_squish(month, N)
    return base * correction(month, tw, nn, C_ref)          # 3~4일차 비월전용: 안정계수 보정


def smoothed_estimate(v, months, month_wd, early, late_start, train, C_ref, year, month, N,
                      zone_weights=None):
    """Zone 1~2: BD3~N 롤링평균으로 노이즈 억제.
    Zone 3+ (N>=ls): zone_aware_prop raw값만 사용 — 이미 스파이크 감안한 비중이므로 추가 희석 불필요."""
    import numpy as np
    ls = late_start.get((year, month))
    tw = month_wd.get((year, month))

    if tw and N >= tw:
        return float(cum(v, N))  # 마지막 영업일: 누적합 = 실매출

    if N < SMOOTH_FROM_N:
        return raw_estimate(v, months, month_wd, early, train, C_ref, year, month, N)

    if zone_weights is None:
        m_arg = month if month in APPLY_MONTHS else None
        zone_weights = compute_zone_weights(train, month_wd, late_start, m_arg)

    # Zone 3+: zone 2 최종 smoothed 추정값을 앵커로 두고 zone 3 롤링
    # → 진입 시 연속성 확보, zone 3가 쌓일수록 앵커 영향 희석
    if ls is not None and N >= ls:
        zone2_anchor = smoothed_estimate(
            v, months, month_wd, early, late_start, train, C_ref, year, month, ls - 1, zone_weights
        )
        estimates = [] if (zone2_anchor != zone2_anchor) else [zone2_anchor]
        for nn in range(ls, N + 1):
            pNN = zone_aware_prop(nn, ls, tw, zone_weights)
            est_nn = cum(v, nn) / pNN if pNN > 0 else float('nan')
            if not (est_nn != est_nn):
                estimates.append(est_nn)
        return float(np.mean(estimates)) if estimates else float('nan')

    # Zone 1~2: BD3~N 롤링평균
    estimates = []
    for nn in range(START_N, N + 1):
        est_nn = raw_estimate(v, months, month_wd, early, train, C_ref, year, month, nn)
        if not (est_nn != est_nn):  # NaN 제외
            estimates.append(est_nn)
    return float(np.mean(estimates)) if estimates else float('nan')


def nowcast(months, month_wd, early, late_start, year, month):
    target = months.get((year, month))
    if not target:
        print(f"[{year}년 {month}월] 데이터 없음.")
        return
    n_avail = max(target)
    is_complete = n_avail >= MIN_COMPLETE_WD
    train = recent_train(months, year, exclude=(year, month))
    tw = month_wd.get((year, month))
    nn = next_nonwork(year, month, early)

    print(f"\n=== {year}년 {month}월 롤링 추정 (현재 {n_avail}영업일차까지)"
          f"{'  [완결]' if is_complete else ''} ===")
    print(f"학습 완결월 {len(train)}개  |  이 달 총영업일 {tw}, 다음달월초휴가 {nn}")
    if n_avail < START_N:
        print(f"\n  아직 {n_avail}영업일차 — 기본 베이스가 {START_N}영업일 기준이라 {START_N}영업일차부터 추정합니다.")
        return
    ref_pN = prop_at(train, WD_REF_N)
    C_ref = learn_corr(train, WD_REF_N, months, year, ref_pN, month_wd, early)  # 안정 β_wd
    ls = late_start.get((year, month))
    m_arg = month if month in APPLY_MONTHS else None
    zw = compute_zone_weights(train, month_wd, late_start, m_arg)
    star = " (월전용비중)" if month in APPLY_MONTHS else ""
    sm = f" ({SMOOTH_FROM_N}일차~ 누적평균)"
    print(f"\n{'영업일':>6}{'누적매출':>11}{'p(N)효과':>9}{'롤링':>10}{'보정후'+sm:>10}")
    for N in range(START_N, min(n_avail, MAXN) + 1):
        overall_pN = prop_at(train, N)
        # 표시용 p(N): zone3+ 이면 구간인식, 이전이면 base_prop
        if ls is not None and N >= ls:
            pN_show = zone_aware_prop(N, ls, tw, zw)
            zone_mark = "z"
        else:
            pN_show = base_prop(months, year, N, month, overall_pN)
            zone_mark = " "
        base = cum(target, N) / (base_prop(months, year, N, month, overall_pN) or 1)
        est = smoothed_estimate(target, months, month_wd, early, late_start, train, C_ref, year, month, N, zw)
        mark = "  <- 현재" if N == n_avail and not is_complete else ""
        print(f"{N:>5}일{cum(target,N)/1e8:>9.1f}억{zone_mark}{pN_show*100:>5.0f}%{base/1e8:>8.1f}억{est/1e8:>8.1f}억{mark}")

    if is_complete:
        print(f"\n  실매출(완결) : {total(target)/1e8:.1f}억")
    else:
        base = cum(target, n_avail) / base_prop(months, year, n_avail, month, prop_at(train, n_avail))
        est = smoothed_estimate(target, months, month_wd, early, late_start, train, C_ref, year, month, n_avail, zw)
        print(f"\n  ▶ 현재({n_avail}영업일차) 추정: 롤링 {base/1e8:.1f}억  →  보정후 {est/1e8:.1f}억")
        print(f"    (영업일이 지날수록 갱신·정밀화)")


def backtest(months, month_wd, early, late_start):
    import numpy as np
    comp = complete_months(months)
    test_years = sorted({y for (y, m) in comp})[1:]
    print("=== N영업일 시점별 평균 적중률 (walk-forward) ===")
    print(f"{'N영업일':>7}{'p(N)':>7}{'롤링단독':>10}{'롤링+보정':>11}")
    for N in range(3, 23):
        hr, hc = [], []
        pref = float("nan")
        for ty in test_years:
            train = recent_train(months, ty)
            if len(train) < 12:
                continue
            overall_pN = prop_at(train, N)
            pref = overall_pN
            ref_pN = prop_at(train, WD_REF_N)
            C_ref = learn_corr(train, WD_REF_N, months, ty, ref_pN, month_wd, early)
            zw_cache = {}
            for (y, m), v in comp.items():
                if y != ty or max(v) < N:
                    continue
                tw = month_wd.get((y, m))
                nn = next_nonwork(y, m, early)
                if tw is None or nn is None:
                    continue
                m_arg = m if m in APPLY_MONTHS else None
                if m_arg not in zw_cache:
                    zw_cache[m_arg] = compute_zone_weights(train, month_wd, late_start, m_arg)
                act = total(v)
                base = cum(v, N) / base_prop(months, ty, N, m, overall_pN)
                est = smoothed_estimate(v, months, month_wd, early, late_start, train, C_ref, ty, m, N,
                                        zw_cache[m_arg])
                hr.append(1 - abs(base - act) / act)
                hc.append(1 - abs(est - act) / act)
        mark = "  <- 스위트스팟" if N in (6, 7, 8) else ""
        print(f"{N:>6}일{pref*100:>6.0f}%{np.mean(hr)*100:>9.1f}%{np.mean(hc)*100:>10.1f}%{mark}")


def print_proptable(months):
    ref = max(months)[0]
    p = {N: prop_at(recent_train(months, ref), N) for N in range(1, MAXN + 1)}
    print(f"=== 영업일별 누적비중 p(N)  (최근 {PROP_YEARS}년 기준) ===")
    for s in (1, 11):
        print("  " + "".join(f"{n:>4}일" for n in range(s, s + 10) if n <= MAXN))
        print("  " + "".join(f"{p[n]*100:>4.0f}%" for n in range(s, s + 10) if n <= MAXN))


def main():
    ap = argparse.ArgumentParser(description="영업일 롤링 nowcast + 보정")
    ap.add_argument("--month", nargs=2, type=int, metavar=("YEAR", "MONTH"))
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--upload", action="store_true", help="Supabase에 forecast 데이터 업로드")
    ap.add_argument("--from-supabase", action="store_true", help="로컬 엑셀 대신 Supabase에서 데이터 로드")
    args = ap.parse_args()

    if args.from_supabase:
        months, month_wd, early, caldays = load_data_from_supabase()
    else:
        print("데이터(DB_grouped + Working Day) 로딩 중... (수십 초) [xlwings]")
        months, month_wd, early, caldays = load_data()
    late_start = compute_late_start(caldays)
    print(f"로딩 완료: {min(months)[0]}~{max(months)[0]}, {len(months)}개월")

    if args.upload:
        upload_to_supabase(months, month_wd, early, caldays)
    elif args.backtest:
        backtest(months, month_wd, early, late_start)
    elif args.month:
        nowcast(months, month_wd, early, late_start, args.month[0], args.month[1])
    else:
        print_proptable(months)
        latest = max(months)
        nowcast(months, month_wd, early, late_start, latest[0], latest[1])


if __name__ == "__main__":
    main()
