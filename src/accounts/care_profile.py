"""Existing Supabase profile tables; never creates or migrates shared tables."""
from datetime import date, datetime, timezone
from uuid import UUID, uuid4
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import MetaData, Table, select, update, delete


class ConditionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    id: UUID | None = None
    condition_type: Literal['allergy', 'disease', 'pregnancy', 'breastfeeding', 'other']
    label: str = Field(min_length=1, max_length=200)
    note: str | None = Field(None, max_length=1000)


class MedicationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    id: UUID | None = None
    drug_name: str = Field(min_length=1, max_length=200)
    dosage_note: str | None = Field(None, max_length=1000)


class CareInput(BaseModel):
    care_note: str | None = Field(None, max_length=500)
    revision: datetime
    conditions: list[ConditionInput] = Field(default_factory=list, max_length=30)
    medications: list[MedicationInput] = Field(default_factory=list, max_length=30)


def tables(db):
    from sqlalchemy import inspect
    names = ('users', 'user_conditions', 'user_medications')
    if not set(names) <= set(inspect(db.connection()).get_table_names()):
        raise HTTPException(503, '상담 참고사항 DB 구성을 확인해주세요.')
    meta = MetaData()
    result = [Table(n, meta, autoload_with=db.connection(), resolve_fks=False) for n in names]
    if not {'care_note', 'updated_at'} <= set(result[0].c.keys()):
        raise HTTPException(503, '상담 참고사항 DB 구성을 확인해주세요.')
    return result


def ident(column, value):
    try:
        return UUID(str(value)) if getattr(column.type, 'as_uuid', False) else str(value)
    except ValueError:
        raise HTTPException(404, '사용자를 찾을 수 없습니다.') from None


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def read(db, user_id):
    users, conditions, medications = tables(db)
    user = db.execute(select(users).where(users.c.id == ident(users.c.id, user_id))).mappings().first()
    if not user or not user['is_active'] or user['deleted_at'] is not None:
        raise HTTPException(404, '사용자를 찾을 수 없습니다.')
    cs = db.execute(select(conditions).where(conditions.c.user_id == ident(conditions.c.user_id, user_id))
                    .order_by(conditions.c.created_at, conditions.c.id)).mappings().all()
    ms = db.execute(select(medications).where(medications.c.user_id == ident(medications.c.user_id, user_id),
                    medications.c.ended_on.is_(None)).order_by(medications.c.created_at, medications.c.id)).mappings().all()
    return {'care_note': user['care_note'], 'revision': utc(user['updated_at']).isoformat(),
            'conditions': [{k: str(c[k]) if k == 'id' else c[k] for k in
                            ('id', 'condition_type', 'label', 'note', 'ingr_code')} for c in cs],
            'medications': [{k: str(m[k]) if k == 'id' else m[k] for k in
                            ('id', 'drug_name', 'dosage_note', 'source', 'item_seq')} for m in ms]}


def save(db, user_id, payload: CareInput):
    users, conditions, medications = tables(db)
    uid = ident(users.c.id, user_id)
    user = db.execute(select(users).where(users.c.id == uid).with_for_update()).mappings().first()
    if not user or not user['is_active'] or user['deleted_at'] is not None:
        raise HTTPException(404, '사용자를 찾을 수 없습니다.')
    if utc(user['updated_at']) != utc(payload.revision):
        raise HTTPException(409, '다른 화면에서 수정됐어요. 다시 불러온 뒤 저장해주세요.')
    now = datetime.now(timezone.utc)
    try:
        for table, entries, kind in ((conditions, payload.conditions, 'condition'),
                                      (medications, payload.medications, 'medication')):
            own = table.c.user_id == ident(table.c.user_id, user_id)
            query = select(table).where(own)
            if kind == 'medication':
                query = query.where(table.c.source == 'manual', table.c.ended_on.is_(None))
            existing = {str(row['id']): row for row in db.execute(query).mappings()}
            keep = set()
            for entry in entries:
                key = str(entry.id) if entry.id else str(uuid4())
                if key in keep or (entry.id and key not in existing):
                    raise HTTPException(422, '수정할 참고사항 항목을 다시 확인해주세요.')
                keep.add(key)
                values = entry.model_dump(exclude={'id'})
                values['updated_at'] = now
                old = existing.get(key)
                if old:
                    # A typed label is not an ingredient/product identifier.
                    if kind == 'condition' and (old['label'] != entry.label or old['condition_type'] != entry.condition_type):
                        values['ingr_code'] = None
                    if kind == 'medication' and old['drug_name'] != entry.drug_name:
                        values['item_seq'] = None
                    db.execute(update(table).where(own, table.c.id == ident(table.c.id, key)).values(**values))
                else:
                    values.update(id=ident(table.c.id, key), user_id=ident(table.c.user_id, user_id), created_at=now)
                    if kind == 'medication': values['source'] = 'manual'
                    db.execute(table.insert().values(**values))
            removed = set(existing) - keep
            if removed:
                where = (own, table.c.id.in_([ident(table.c.id, k) for k in removed]))
                if kind == 'condition': db.execute(delete(table).where(*where))
                else: db.execute(update(table).where(*where).values(ended_on=date.today(), updated_at=now))
        db.execute(update(users).where(users.c.id == uid).values(care_note=(payload.care_note or '').strip() or None, updated_at=now))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return read(db, user_id)
