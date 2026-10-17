"""팀 Supabase의 `public.users` 테이블 접근 (프로필·역할).

공유 DB 구조는 바꾸지 않는다. 아이디는 별도 컬럼이 없어 `nickname`에, 내부 이메일은 `email`(UNIQUE)에 저장한다.
상담 모델과 메타데이터를 분리해 둔 이유: 기존 테스트가 임시 SQLite에 `users` 테이블을 직접 만들기 때문(충돌 방지).
로컬 SQLite에서는 init_db가 같은 이름의 테이블을 만들어 준다(auth.users 외래키는 Supabase에만 있음).
"""
from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.consult.models import UUIDString, _now

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", UUIDString, primary_key=True),
    Column("email", String, unique=True),
    Column("nickname", String),
    Column("role", String, nullable=False, default="user"),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("last_login_at", DateTime),
    Column("created_at", DateTime, nullable=False, default=_now),
    Column("updated_at", DateTime, nullable=False, default=_now),
    Column("deleted_at", DateTime),
)


def create_local_table(engine: Engine) -> None:
    users.create(bind=engine, checkfirst=True)


def find_by_email(db: Session, email: str):
    return db.execute(select(users).where(users.c.email == email)).mappings().first()


def find_by_id(db: Session, user_id: str):
    return db.execute(select(users).where(users.c.id == user_id)).mappings().first()


def add(db: Session, user_id: str, email: str, username: str) -> None:
    db.execute(insert(users).values(id=user_id, email=email, nickname=username, role="user"))


def mark_login(db: Session, user_id: str) -> None:
    now = _now()
    db.execute(update(users).where(users.c.id == user_id).values(last_login_at=now, updated_at=now))
