"""Durable DB queue. Run separately: python -m src.consult.worker.

Leases recover interrupted jobs. CAS completion prevents an expired worker from
overwriting a retry. Audio bytes are removed after success or terminal failure.
"""
import logging
import time
import uuid
from datetime import timedelta

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import SQLAlchemyError

from src.consult import ai
from src.consult.db import SessionLocal, init_db
from src.consult.models import ConsultationAudio, ConsultationSummary, _now
from src.consult.schemas import SummaryContent

logger = logging.getLogger(__name__)


def process_one(factory=SessionLocal) -> bool:
    for model in (ConsultationSummary, ConsultationAudio):
        with factory() as db:
            available = or_(model.status == "queued", and_(
                model.status == "processing", model.started_at < _now() - timedelta(minutes=5)))
            pk = model.consultation_id if model is ConsultationSummary else model.id
            row = db.query(model).filter(available).order_by(pk).first()
            if row is None:
                continue
            identity = row.consultation_id if model is ConsultationSummary else row.id
            lease = str(uuid.uuid4())
            claimed = db.execute(update(model).where(pk == identity, available).values(
                status="processing", lease=lease, started_at=_now(), error=None,
            ), execution_options={"synchronize_session": False}).rowcount
            db.commit()
            if not claimed:
                continue
            db.refresh(row)
            payload = row.source if model is ConsultationSummary else (row.filename, row.audio)
        try:
            if model is ConsultationSummary:
                draft = SummaryContent.model_validate(ai.summarize(payload)).model_dump()
                values = {"draft": draft, "status": "pending_review"}
            else:
                values = {"transcript": ai.transcribe(*payload), "audio": None, "status": "completed"}
        except Exception:
            # Provider exceptions can contain transcript/body content: never log them.
            logger.warning("Consultation AI job failed (%s)", model.__tablename__)
            values = {"status": "failed", "error": "ai_processing_failed"}
            if model is ConsultationAudio:
                values["audio"] = None
        with factory() as db:
            db.execute(update(model).where(pk == identity, model.lease == lease,
                       model.status == "processing").values(**values, lease=None))
            db.commit()
        return True
    return False


def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    try:
        while True:
            try:
                processed = process_one()
            except SQLAlchemyError:
                logger.warning("Consultation queue database unavailable; retrying")
                processed = False
            if not processed:
                time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
