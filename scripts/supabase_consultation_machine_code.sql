-- 상담에 "사용자가 고른 키오스크"를 저장하는 컬럼 추가 (2026-10-19)
-- 목적: 약사가 환자가 고른 키오스크 재고(machine_inventory)에 있는 약만 승인하도록 한다.
-- 팀 Supabase 구조 변경이므로 한별 동의 후 SQL Editor에서 한 번 실행한다. 기존 데이터·다른 컬럼은 건드리지 않는다.
-- 비어 있어도 되는 컬럼이라 기존 상담·한별 서버 코드에는 영향 없음. 다시 실행해도 안전(IF NOT EXISTS).

ALTER TABLE public.consultation_requests
  ADD COLUMN IF NOT EXISTS machine_code varchar(40)
  REFERENCES public.vending_machines(code) ON DELETE SET NULL;

-- 확인
-- SELECT column_name, data_type, is_nullable FROM information_schema.columns
--  WHERE table_schema = 'public' AND table_name = 'consultation_requests' AND column_name = 'machine_code';
