# Supabase 연결 작업 상태

현재 실제 DB 연결 정보는 제공되지 않았으며 기존 18개 테이블 목록과 일부 컬럼만 확인했다.
의약품·재고·사용자·구매 전체 구조의 매핑 및 운영 DB 마이그레이션은 아직 완료되지 않았다.

## 준비된 코드

- PostgreSQL psycopg 드라이버, URL 정규화 및 기본 TLS 연결.
- 일반 서버는 PostgreSQL 시작 시 테이블을 자동 생성하지 않는다. SQLite 시연은 기존대로 동작한다.
- `scripts/inspect_database.py`: 읽기 전용 트랜잭션으로 컬럼·키·인덱스·RLS를 조사한다.
  레코드나 접속 비밀번호는 보고서에 포함하지 않는다.
- 의약품 조회 API: `/api/v1/drugs`, `/{item_seq}`, `/{item_seq}/ingredients`,
  `/{item_seq}/permissions`, `/{item_seq}/pills`. 기존 테이블을 읽으며 없으면 503이다.
  이 API는 일반 서버에 추가했으며 로컬 영상 시연 서버에는 의약품 테이블을 만들지 않는다.

## 연결 정보 설정

Supabase 프로젝트 → Connect → Session pooler에서 PostgreSQL 연결 문자열을 복사해
`.env`의 `DATABASE_URL`에 넣는다. `[YOUR-PASSWORD]`는 프로젝트 DB 비밀번호로 바꾼다.
Supabase 계정 로그인 비밀번호나 API 키가 아니다. 특수문자는 URL 인코딩한다.
DB 비밀번호를 모르면 프로젝트를 만든 팀원에게 확인한다. 재설정 전에는 기존 서비스 영향을 확인한다.

```powershell
.\venv\Scripts\python.exe scripts/inspect_database.py
```

기본 보고서는 `data/database-schema.json`이다. `.gitignore`로 제외되어 있다.
접속 정보 확보 전에 스키마만 공유할 경우 `docs/supabase-schema.sql`을 SQL Editor에서 실행하고
결과를 파일로 전달해도 된다. 이 SQL은 데이터를 변경하지 않는다.

## 실제 스키마 확인 후 남은 작업

1. `users`, `vending_machines`, `machine_inventory`, `purchases`의 실제 컬럼과 키를 매핑한다.
   현재 로컬 `vending_inventory`와 원격 `machine_inventory`는 이름만 바꿔서는 안 된다.
2. 의약품 상세 전체 컬럼, DUR 데이터 및 사용자와 상담 ID 관계를 검증한다.
3. 누락된 상담 세션·채팅·전사·요약 테이블의 마이그레이션을 작성하고 적용한다.
   기존 테이블, 데이터 및 RLS 정책은 확인 없이 변경하지 않는다.
4. 서버 권한과 사용자 인증을 맞추고 실제 DB로 조회·채팅·재고 처리 등을 검증한다.

참고: [Supabase 연결](https://supabase.com/docs/guides/database/connecting-to-postgres),
[psycopg 설치](https://www.psycopg.org/psycopg3/docs/basic/install.html).
