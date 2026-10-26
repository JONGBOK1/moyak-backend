-- 키오스크 재고 기반 처방 + 처방 수량 (컬럼 2개)
--
--   적용:  python scripts/apply_sql.py sql/017_kiosk_stock_prescription.sql
--
-- 출처
--   종복이 공유한 "MOYAK 10/19 수정 사항" 문서의 DB 변경 요청.
--   같은 내용이 팀 저장소 `dev` 브랜치의 scripts/supabase_consultation_machine_code.sql
--   에도 있다. 글자 그대로 맞춰 두 곳이 어긋나지 않게 했다.
--
-- 왜 필요한가
--   카톡에서 지적된 문제 — **자판기에 없는 약을 약사가 처방할 수 있었다.**
--   약 검색 대상 4,748개인데 자판기에 실제 재고가 있는 건 몇십 종뿐이라,
--   승인받고 자판기 앞에 가도 그 약이 없는 일이 생긴다.
--   사용자가 고른 키오스크를 상담에 적어 두면, 약사는 그 키오스크 재고에 있는
--   약만 고를 수 있다.
--
--   1) consultation_requests.machine_code  사용자가 앱에서 고른 키오스크
--   2) approved_purchases.quantity         약사가 처방한 수량
--      수령 시 재고를 이만큼 차감하고, price 는 수량 전체 금액(총액)이다.
--
-- 안전한가
--   둘 다 **비어 있어도 되거나 기본값이 있는** 컬럼이라 기존 19건(상담)·5건(승인)에
--   영향이 없다. 한별 서버 코드도 이 컬럼을 모르면 그냥 안 쓴다.
--   IF NOT EXISTS 라 다시 실행해도 안전하다.
--   외래키가 걸리는 vending_machines.code 에는 UNIQUE 제약이 이미 있다(확인함).
--
-- 순서 주의
--   **이 SQL 을 먼저 실행하고 서버를 master 에 올려야 한다.** 반대로 하면
--   상담·승인 조회가 없는 컬럼을 찾아 깨진다.

ALTER TABLE public.consultation_requests
  ADD COLUMN IF NOT EXISTS machine_code varchar(40)
  REFERENCES public.vending_machines(code) ON DELETE SET NULL;

ALTER TABLE public.approved_purchases
  ADD COLUMN IF NOT EXISTS quantity integer NOT NULL DEFAULT 1 CHECK (quantity >= 1);

COMMENT ON COLUMN public.consultation_requests.machine_code IS
    '사용자가 고른 키오스크 코드(VM-001 등). 약사는 이 키오스크 재고에 있는 약만 승인한다. 비어 있으면 키오스크를 고르지 않은 상담.';

COMMENT ON COLUMN public.approved_purchases.quantity IS
    '약사가 처방한 수량(1 이상). 수령 시 키오스크 재고를 이만큼 차감하고, price 는 수량 전체 금액이다.';
