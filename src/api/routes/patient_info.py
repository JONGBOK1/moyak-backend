"""약사가 상담 중에 보는 환자 정보와 안전 판정.

**왜 필요한가** — 상담 화면에 지금까지 뜨는 건 `user_id`(UUID)와 챗봇 대화 요약뿐이다.
환자가 임신 중인지, 무엇에 알러지가 있는지, 지금 무슨 약을 먹고 있는지가 안 보인다.
그 정보는 요약 **글 속에만** 있어서("머리가 3일째 아픔. 임신 12주.") 사람이 읽고
알아서 판단해야 하고, 챗봇을 거치지 않고 바로 온 상담에는 그 글조차 없다.

여기서 두 가지를 준다.
  1. 환자가 등록해 둔 참고사항 — 알러지·지병·임신·수유·기타 + 자유 메모 + 복용 중인 약
  2. 약을 고르면 **식약처 DUR 로 자동 판정** — 병용금기·임부금기·노인주의·연령금기

판정 로직은 `src/consult/patient_safety.py` 에 있다(선우 쪽 코드를 그대로 옮긴 것).

**이름은 가리고 참고사항은 안 가린다.** 약사 화면은 상담 중 화면 공유·녹화에 찍히므로
이름은 `김*빈` 으로 내보내지만(`src/privacy.py`), 알러지·지병은 약사가 판단에 써야 하는
정보라 그대로 보낸다.

신원은 이 저장소의 다른 상담 API 와 같은 방식이다 — `X-Moyak-User-Id` + `X-Moyak-Role`.
약사만, 그리고 **그 환자의 상담을 맡았거나 아직 아무도 안 맡은 대기 상담이 있을 때만** 볼 수 있다
(`/consultations/{id}/care-profile` 와 같은 규칙, 2026-10-26). 상담과 관계없는 환자의 건강 정보는 못 본다.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.consult.auth import Actor, current_actor
from src.consult.db import get_db
from src.consult.models import ConsultationRequest, ConsultationStatus
from src.consult.patient_safety import check_drug_for_user, year_age
from src.privacy import mask_name

router = APIRouter(prefix="/pharmacist/patients", tags=["pharmacist-patient"])


def _only_pharmacist(actor: Actor) -> None:
    if actor.role != "pharmacist":
        raise HTTPException(403, "약사만 환자 정보를 볼 수 있습니다.")


def _require_care_relation(db: Session, actor: Actor, user_id: str) -> None:
    """약사 + 이 환자와 상담 관계가 있어야 한다: 내가 맡은 상담, 또는 아직 담당이 없는 대기 상담."""
    _only_pharmacist(actor)
    rows = db.query(ConsultationRequest.status, ConsultationRequest.pharmacist_id).filter(
        ConsultationRequest.user_id == user_id,
        ConsultationRequest.status != ConsultationStatus.CANCELLED,
    ).all()
    if not any(pid == actor.id or (pid is None and status == ConsultationStatus.PENDING) for status, pid in rows):
        raise HTTPException(403, "이 환자의 상담을 맡은 약사만 볼 수 있습니다.")


@router.get("/{user_id}", summary="환자 참고사항 (알러지·지병·임신·복용약)")
def get_patient(user_id: str, actor: Actor = Depends(current_actor),
                db: Session = Depends(get_db)) -> dict:
    _require_care_relation(db, actor, user_id)
    row = db.execute(text("""
        SELECT id, name, nickname, care_note, birth_year, sex
          FROM users WHERE id = CAST(:u AS uuid)
    """), {"u": user_id}).mappings().first()
    if row is None:
        raise HTTPException(404, "해당 환자를 찾을 수 없습니다.")

    meds = db.execute(text("""
        SELECT item_seq, drug_name, started_on, dosage_note
          FROM user_medications WHERE user_id = CAST(:u AS uuid)
         ORDER BY started_on DESC NULLS LAST
    """), {"u": user_id}).mappings().all()
    conds = db.execute(text("""
        SELECT condition_type, label, note, updated_at
          FROM user_conditions WHERE user_id = CAST(:u AS uuid)
         ORDER BY condition_type, created_at
    """), {"u": user_id}).mappings().all()

    # 성분 정보가 있어야 병용금기를 대조할 수 있다. 없으면 그만큼 판정이 불완전하다.
    unusable = db.execute(text("""
        SELECT count(*) FROM user_medications m
         WHERE m.user_id = CAST(:u AS uuid)
           AND NOT EXISTS (SELECT 1 FROM drug_ingredients i WHERE i.item_seq = m.item_seq)
    """), {"u": user_id}).scalar() or 0

    from datetime import date
    birth = row["birth_year"]
    return {
        "user_id": str(row["id"]),
        "name": mask_name(row["name"]),          # 이름만 가린다
        "nickname": mask_name(row["nickname"]),
        "care_note": row["care_note"],           # 참고사항은 가리지 않는다
        "birth_year": birth,
        "age": year_age(birth),
        "sex": row["sex"],
        "medications": [dict(m) for m in meds],
        "conditions": [dict(c) for c in conds],
        "unusable_medication_count": unusable,
    }


@router.get("/{user_id}/safety/{item_seq}", summary="이 약, 이 환자가 먹어도 되나요?")
def get_patient_safety(user_id: str, item_seq: str, actor: Actor = Depends(current_actor),
                       db: Session = Depends(get_db)) -> dict:
    """약사가 처방할 약을 고르면 불러서 경고를 띄운다.

    `level` 은 danger / warning / info / unknown 넷이다.
    **`unknown` 을 '안전' 으로 보여주면 안 된다** — 성분 정보가 있는 약이 전체의 12% 뿐이라
    대조를 못 한 경우가 자주 나온다. 약사가 '확인됨' 으로 오해하면 위험하다.
    """
    _require_care_relation(db, actor, user_id)
    try:
        report = check_drug_for_user(db, user_id, item_seq)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return report.to_dict()
