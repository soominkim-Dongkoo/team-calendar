-- 외근·출장 실제 시간 저장용 컬럼
-- scrape.py 가 하루짜리 외근·출장에 한해 기안 근태기간의 시각을 저장한다.
-- 그 외 근태(연차·반차 등)는 비워두고, 캘린더가 day_type 고정 시간표로 표시한다.
--
-- 반드시 scrape.py 수정본 배포 "전에" 실행할 것
-- (컬럼 없이 새 코드가 돌면 upsert 가 실패해 스크래퍼가 멈춤)

alter table leave_records add column if not exists start_time time;
alter table leave_records add column if not exists end_time   time;
