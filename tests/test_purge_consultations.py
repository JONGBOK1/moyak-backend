"""취소·24시간 넘은 대기 상담 자동 삭제 (2026-10-23). 승인·거절된 상담과 샘플은 남긴다."""
from datetime import timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.consult import service
from src.consult.db import Base
from src.consult.models import ConsultationMessage, ConsultationRequest, ConsultationSession, _now


def _db(with_sample_column=False):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    if with_sample_column:  # 팀 Supabase처럼 is_sample 컬럼이 있는 경우
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE consultation_requests ADD COLUMN is_sample BOOLEAN NOT NULL DEFAULT 0"))
    return sessionmaker(bind=engine)()


def _add(db, cid, status, hours_ago):
    db.add(ConsultationRequest(id=cid, user_id="u", chat_summary="-", status=status,
                               created_at=_now() - timedelta(hours=hours_ago)))


def test_purges_cancelled_and_stale_pending_only():
    db = _db()
    _add(db, "cancelled-new", "cancelled", 1)
    _add(db, "pending-old", "pending", 25)
    _add(db, "pending-new", "pending", 2)
    _add(db, "approved-old", "approved", 48)
    _add(db, "rejected-old", "rejected", 48)
    db.add(ConsultationMessage(consultation_id="pending-old", sender_role="user", sender_id="u", content="hi"))
    db.add(ConsultationSession(consultation_id="pending-old", pharmacist_id="p"))
    db.commit()
    assert service.purge_stale_consultations(db) == 2
    left = {c.id for c in db.query(ConsultationRequest)}
    assert left == {"pending-new", "approved-old", "rejected-old"}
    assert db.query(ConsultationMessage).count() == 0 and db.query(ConsultationSession).count() == 0  # 딸린 기록도 삭제
    assert service.purge_stale_consultations(db) == 0


def test_keeps_team_sample_data():
    db = _db(with_sample_column=True)
    _add(db, "sample-old", "pending", 72)
    _add(db, "test-old", "pending", 72)
    db.commit()
    db.execute(text("UPDATE consultation_requests SET is_sample = 1 WHERE id = 'sample-old'"))
    db.commit()
    assert service.purge_stale_consultations(db) == 1
    assert {c.id for c in db.query(ConsultationRequest)} == {"sample-old"}
