# 경영기획팀 Team Calendar

DongKoo Bio&Pharma 경영기획팀 일정 공유 캘린더 프로젝트.

## 프로젝트 구조

```
team-calendar/
├── index.html          # 캘린더 UI (Pretendard, 다크모드, Team/Personal 뷰)
├── scraper/
│   ├── scrape.py       # 다우오피스 스크래퍼 (Playwright)
│   ├── leave_data.json # 스크래핑된 휴가 데이터 (로컬 캐시)
│   └── .env            # 크레덴셜 (DAOU_ID, DAOU_PW, DAOU_URL, SUPABASE_URL, SUPABASE_KEY)
```

## 스택

- **스크래퍼**: Python + Playwright (로컬 PC에서 Windows Task Scheduler로 1시간마다 실행)
- **DB**: Supabase (PostgreSQL, 무료)
- **프론트**: HTML/CSS/JS → 추후 Vercel 배포
- **알림**: 이메일 (미결정)

## DB 스키마

```sql
-- 스크래퍼가 저장하는 휴가 데이터
create table leave_records (
  doc_id     text primary key,  -- 문서번호 (계원-202606-00017)
  name       text not null,     -- 신청자
  leave_type text not null,     -- 근태구분 (ERP 원본값)
  label      text not null,     -- 캘린더 표시 텍스트
  day_type   text not null,     -- 종일 / 반차(오전) / 반차(오후) / 반반차(1Q~4Q)
  start_date date not null,
  end_date   date not null,
  duration   text,
  status     text default '승인',
  created_at timestamptz default now()
);

-- 수동 등록 일정
create table manual_events (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  description text,
  event_type  text not null,   -- meeting / work / personal
  owner       text not null,
  start_date  date not null,
  end_date    date not null,
  start_time  time,
  end_time    time,
  is_team     boolean default true,
  reminder_at timestamptz,
  created_at  timestamptz default now()
);
```

## 캘린더 표시 규칙

### 색상
| 구분 | 색상 |
|---|---|
| 휴가 전체 (연차/체력단련/향군/현지출근/외근) | #fc8181 |
| 회의 | #63b3ed |
| 업무 | #68d391 |
| 개인일정 | #b794f4 |

### 표시 텍스트
- `day_type`이 "종일" → `label` 만 표시 (예: "연차")
- 그 외 → `label + " " + day_type` (예: "연차 반차(오전)", "향군 반반차(4Q)")

### 블록 크기
- 반차/반반차 구분 없이 항상 꽉 채움 (텍스트로만 구분)

### label 매핑 (leave_type → label)
| ERP 값 | 표시 |
|---|---|
| 연차, 체력단련휴가 | 연차 |
| 현지출근 | 현지출근 |
| 외근 | 외근 |
| 향군 | 향군 |
| 반차(오전), 반차(오후) | 그대로 |
| 반반차(1Q~4Q) | 그대로 |

## 스크래퍼 핵심 로직

- **대상**: 다우오피스 `https://po.dongkoo.co.kr/` → 기안 완료함 폴더 83
- **검색**: 결재양식 = "근태계신청_본사(ERP연동)", 전체기간
- **중복방지**: 목록의 td:8(문서번호)로 사전 스킵, 상세 진입 최소화
- **day_type 도출**: leave_type에 반차/반반차 명시 → 그대로 / 기본형 + duration → 시작시각으로 판단

## 팀원
- 4명: 김수민, 황유준, 김경수, 정힘찬

## 진행 상태
- [x] 캘린더 UI 디자인 (index.html)
- [x] 스크래퍼 완성 (scrape.py) - 20건 수집
- [ ] Supabase 연동 (현재 진행 중)
- [ ] 웹앱 Vercel 배포
- [ ] 알림 기능
