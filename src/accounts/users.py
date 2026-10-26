"""팀 Supabase의 `public.users` 테이블 접근 (프로필·역할).

공유 DB 구조는 바꾸지 않는다. 아이디는 별도 컬럼이 없어 `nickname`에, 내부 이메일은 `email`(UNIQUE)에 저장한다.
상담 모델과 메타데이터를 분리해 둔 이유: 기존 테스트가 임시 SQLite에 `users` 테이블을 직접 만들기 때문(충돌 방지).
로컬 SQLite에서는 init_db가 같은 이름의 테이블을 만들어 준다(auth.users 외래키는 Supabase에만 있음).
"""
from sqlalchemy import Boolean, Column, DateTime, MetaData, String, Table, insert, select, update, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.consult.models import UUIDString, _now

metadata = MetaData()

users = Table(
    "users", metadata,
    Column("id", UUIDString, primary_key=True),
    Column("email", String, unique=True),
    Column("nickname", String),
    Column("name", String(40)),
    Column("role", String, nullable=False, default="user"),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("last_login_at", DateTime),
    Column("created_at", DateTime, nullable=False, default=_now),
    Column("updated_at", DateTime, nullable=False, default=_now),
    Column("deleted_at", DateTime),
)


def create_local_table(engine: Engine) -> None:
    users.create(bind=engine, checkfirst=True)
    if engine.dialect.name == 'sqlite' and 'name' not in {c['name'] for c in inspect(engine).get_columns('users')}:
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE users ADD COLUMN name VARCHAR(40)'))


def find_by_email(db: Session, email: str):
    return db.execute(select(users).where(users.c.email == email)).mappings().first()


def find_by_id(db: Session, user_id: str):
    return db.execute(select(users).where(users.c.id == user_id)).mappings().first()


def add(db: Session, user_id: str, email: str, username: str, name: str | None = None) -> None:
    db.execute(insert(users).values(id=user_id, email=email, nickname=username, name=name, role="user"))


def set_name(db: Session, user_id: str, name: str) -> None:
    db.execute(update(users).where(users.c.id == user_id).values(name=name, updated_at=_now()))


def mark_login(db: Session, user_id: str) -> None:
    now = _now()
    db.execute(update(users).where(users.c.id == user_id).values(last_login_at=now, updated_at=now))



def mask_name(name: str | None) -> str | None:
    """키오스크 화면용 이름 가리기: 이종복 → 이*복, 김철 → 김*, 남궁민수 → 남**수 (공공장소 화면이라 실명을 그대로 띄우지 않음)."""
    name = "".join((name or "").split())
    if not name:
        return None
    if len(name) <= 2:
        return name[0] + "*" * (len(name) - 1)
    return name[0] + "*" * (len(name) - 2) + name[-1]


def display_name(db: Session, user_id: str | None) -> str | None:
    """사용자 ID로 가린 실명을 찾는다. 가입하지 않은 시연용 ID·이름 미입력·조회 실패면 None."""
    import uuid

    try:
        uuid.UUID(str(user_id))  # 팀 users.id는 UUID — 시연용 임의 ID로 조회하면 Postgres 타입 오류
        row = find_by_id(db, str(user_id))
    except Exception:
        db.rollback()
        return None
    return mask_name(row["name"]) if row else None
