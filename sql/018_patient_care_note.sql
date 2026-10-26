-- 환자 개별 참고사항 (한별 요청)
--
--   적용:  python scripts/apply_sql.py sql/018_patient_care_note.sql
--
-- 요청 내용 (2026-10-21 카톡)
--   "프로필쪽에서 개별참고사항 페이지기능을 추가해서 환자는 '~약물 알레르기 있습니다.
--    고혈압이 있습니다. 임신중입니다' 등 중요한 참고사항을 적을 수 있는 페이지를 만들고
--    약사랑 상담할때 그 정보가 상시 전달 되는 기능"
--
-- 이미 있던 것
--   `user_conditions` 로 알러지·지병·임신·수유를 **항목으로** 등록하면 약사 화면에
--   상시 전달되고, 약을 고를 때 임부금기·병용금기가 자동으로 걸린다.
--   환자용 API(`GET/POST/DELETE /me/conditions`)도 있다.
--
-- 모자랐던 것 — 이 파일이 채운다
--   1) **자유롭게 적을 칸이 없었다.** 네 가지 분류에 안 맞는 말("약을 삼키기 어렵습니다",
--      "지난달 수술했습니다")을 적을 데가 없다. 한별이 말한 '페이지'가 이쪽이다.
--   2) **항목 종류에 '기타'가 없었다.**
--   3) **고칠 수가 없었다.** 추가와 삭제만 되고 수정이 없어서, 오타 하나에 지우고 다시
--      넣어야 했다. 언제 고쳤는지도 안 남았다.
--
-- 왜 자유 칸과 항목을 **둘 다** 두나
--   자유 글만 받으면 **자동 판정이 안 된다.** "임신중입니다" 라고 적어도 기계는 그게
--   임부금기 대상인지 모른다. 반대로 항목만 받으면 분류에 없는 말을 못 적는다.
--   그래서 중요한 것(알러지·지병·임신·수유)은 항목으로 골라 자동 판정에 쓰고,
--   그 밖에 약사에게 하고 싶은 말은 자유 칸에 적게 한다.
--
-- 개인정보
--   건강정보는 민감정보라 `user_consents` 의 health_data 동의를 받은 사용자에게만
--   받는다(기존 정책 그대로). 탈퇴 시 기존 경로로 같이 지워진다.

-- 1) 사용자 단위 자유 참고사항
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS care_note varchar(500);

COMMENT ON COLUMN public.users.care_note IS
    '환자가 약사에게 전하고 싶은 자유 참고사항(선택, 500자). 상담을 열면 약사 화면에 '
    '그대로 보인다. 자동 판정에는 쓰이지 않는다 — 임부금기·병용금기 같은 판정은 '
    'user_conditions 의 항목으로만 한다.';

-- 2) 항목 종류에 '기타' 추가
ALTER TABLE public.user_conditions
    DROP CONSTRAINT IF EXISTS ck_condition_type;

ALTER TABLE public.user_conditions
    ADD CONSTRAINT ck_condition_type CHECK (
        condition_type IN ('allergy', 'disease', 'pregnancy', 'breastfeeding', 'other')
    );

COMMENT ON COLUMN public.user_conditions.condition_type IS
    'allergy(알러지) · disease(지병) · pregnancy(임신) · breastfeeding(수유) · other(기타). '
    '앞의 넷은 식약처 DUR 자동 판정에 쓰이고, other 는 표시만 한다.';

-- 3) 고친 시각
ALTER TABLE public.user_conditions
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

COMMENT ON COLUMN public.user_conditions.updated_at IS
    '마지막으로 고친 시각. 약사가 "언제 적은 정보인지" 를 보고 판단할 수 있어야 한다.';
