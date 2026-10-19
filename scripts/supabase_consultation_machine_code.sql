-- 키오스크 재고 기반 처방을 위한 컬럼 2개 추가 (2026-10-19)
-- 1) consultation_requests.machine_code: 사용자가 앱에서 고른 키오스크 → 약사는 그 키오스크 재고(machine_inventory)에 있는 약만 승인
-- 2) approved_purchases.quantity: 약사가 처방한 수량 → 수령 시 키오스크 재고를 이만큼 차감, price는 수량 전체 결제 금액
-- 팀 Supabase 구조 변경이므로 한별 동의 후 SQL Editor에서 한 번 실행한다. 기존 데이터·다른 컬럼은 건드리지 않는다.
-- 둘 다 비어 있거나 기본값(1)이 있는 컬럼이라 기존 데이터·한별 서버 코드에는 영향 없음. 다시 실행해도 안전(IF NOT EXISTS).

ALTER TABLE public.consultation_requests
  ADD COLUMN IF NOT EXISTS machine_code varchar(40)
  REFERENCES public.vending_machines(code) ON DELETE SET NULL;

ALTER TABLE public.approved_purchases
  ADD COLUMN IF NOT EXISTS quantity integer NOT NULL DEFAULT 1 CHECK (quantity >= 1);

-- 확인
-- SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns
--  WHERE table_schema = 'public'
--    AND ((table_name = 'consultation_requests' AND column_name = 'machine_code')
--      OR (table_name = 'approved_purchases' AND column_name = 'quantity'));
