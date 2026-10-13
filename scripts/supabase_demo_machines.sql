-- 동양미래대학교 주변 시연용 자판기 + 재고를 Supabase에 넣는 SQL.
-- Supabase 대시보드 > SQL Editor에서 한 번 실행한다 (지도 API 서버는 읽기 전용이라 직접 쓰지 않음).
-- 2026-10-06 실제 스키마 확인 후 작성:
--   vending_machines(id uuid PK, code UNIQUE NOT NULL, name, address, location geography, operating_hours, is_active, ...)
--   machine_inventory(machine_id uuid FK, item_seq, stock >= 0, price, slot_code, PK(machine_id, item_seq))
-- 여러 번 실행해도 중복으로 들어가지 않는다 (code / (machine_id, item_seq) 기준 ON CONFLICT).

BEGIN;

-- 1) 기존 VM-001 "동양미래대학교 학생회관"은 경도가 126.8495로 학교에서 서쪽 약 1.5km 떨어져 있어 실제 위치로 보정
--    (OpenStreetMap 기준 동양미래대학교 37.5011, 126.8670). 한별님과 확인 후 실행할 것.
UPDATE public.vending_machines
   SET location = ST_SetSRID(ST_MakePoint(126.8670, 37.5011), 4326)::geography, updated_at = now()
 WHERE code = 'VM-001';

-- 2) 학교 주변 실제 장소에 자판기 4대 추가
INSERT INTO public.vending_machines (code, name, address, location, operating_hours, is_active) VALUES
  ('VM-006', '고척스카이돔 1층', '서울 구로구 경인로 430',
   ST_SetSRID(ST_MakePoint(126.8671, 37.4982), 4326)::geography, '24시간', true),
  ('VM-007', '구일역 1번출구', '서울 구로구 구일로 133',
   ST_SetSRID(ST_MakePoint(126.8709, 37.4964), 4326)::geography, '05:30-24:00', true),
  ('VM-008', '개봉역 북부광장', '서울 구로구 개봉동 개봉역',
   ST_SetSRID(ST_MakePoint(126.8587, 37.4952), 4326)::geography, '24시간', true),
  ('VM-009', '구로구청 민원실 앞', '서울 구로구 가마산로 구로구청',
   ST_SetSRID(ST_MakePoint(126.8876, 37.4947), 4326)::geography, '09:00-18:00', true)
ON CONFLICT (code) DO NOTHING;

-- 3) 재고 — item_seq는 실제 e약은요 품목기준코드 (src/api/drug_index.csv, 지도 상세에 약품명으로 표시)
--    202106092 타이레놀정500밀리그람 / 196800036 판콜에이내복액 / 198700405 베아제정 / 199801026 훼스탈플러스정
--    197900277 게보린정 / 197700120 부루펜정200밀리그램 / 200903973 마데카솔케어연고 / 199400883 겔포스엠현탁액
--    VM-009(구로구청)는 전 품목 품절 시연용
INSERT INTO public.machine_inventory (machine_id, item_seq, stock, price)
SELECT m.id, v.item_seq, v.stock, v.price
  FROM (VALUES
    ('VM-001', '202106092', 12, 3500), ('VM-001', '196800036', 20, 1500), ('VM-001', '198700405', 8, 4000),
    ('VM-001', '200903973', 6, 6000),
    ('VM-006', '202106092', 5, 3500), ('VM-006', '197900277', 10, 3000), ('VM-006', '199400883', 7, 5500),
    ('VM-007', '202106092', 15, 3500), ('VM-007', '199801026', 9, 4500), ('VM-007', '197700120', 4, 3000),
    ('VM-007', '196800036', 11, 1500),
    ('VM-008', '202106092', 1, 3500), ('VM-008', '198700405', 2, 4000),
    ('VM-009', '202106092', 0, 3500), ('VM-009', '196800036', 0, 1500)
  ) AS v(code, item_seq, stock, price)
  JOIN public.vending_machines m ON m.code = v.code
ON CONFLICT (machine_id, item_seq) DO NOTHING;

COMMIT;
