# 약품 검색의 Supabase 전환

## 적용 범위

- `/drugs/search`: 약사 자동완성
- `/api/v1/drugs`: 검색·페이지 구분
- `/api/v1/drugs/{item_seq}`: 상세 조회
- 관련 성분·허가·낱알 조회도 같은 카탈로그 연결을 사용한다.

`CATALOG_DATABASE_URL`을 우선하고 없으면 `DATABASE_URL`을 사용한다. 로컬 시연 실행 스크립트는 `.env`의 원래 DB 주소를 카탈로그 연결로 보존한다. 상담 시연용 SQLite와 분리된다. PostgreSQL 카탈로그 요청은 READ ONLY 트랜잭션으로 실행하며 DB 연결 실패 시 503을 반환한다. CSV로 대체하지 않는다.

## 검색 범위

`drugs`의 약품을 우선한다. 이전 검색 CSV에 있었지만 `drugs`에 없는 9개 코드는 `src/drugs/legacy_permission_ids.json`에 보존하고 `drug_permissions`에서 보완한다. JSON은 검색 범위 설정이며 약품명·제조사·설명 데이터는 담지 않는다. 중복 코드는 한 번만 반환하고 허가 테이블의 나머지 품목은 추가하지 않는다. 향후 `drugs`에 추가된 품목은 자동으로 검색 대상이 된다.

2026-10-07 실제 Supabase 읽기 전용 대조 결과:

- 기존 CSV 고유 코드: 4,757개
- DB 검색 고유 코드: 4,757개
- 누락·추가·중복: 각각 0개
- 허가 테이블 보완 9개: 코드 검색 및 상세 약품명 확인 완료

약품명·제조사는 Supabase 값을 사용한다. 기존 CSV와 표기가 달라도 덮어쓰거나 CSV 값을 우선하지 않는다. 검색 대상이라는 사실은 판매·처방 가능 여부를 보증하지 않으며 기존 승인 흐름과 별개다.

## CSV 정리

서비스용 `src/api/drug_index.csv`는 제거한 상태로 유지한다. 기존 파일은 `data/scenario-integration-backup/drug_index.csv`에 복구하여 보존했으며 Git에서 제외했다. 서비스는 이 백업에 접근하지 않는다.

`eyakeunyo_clean.csv`를 만드는 정제 및 읽는 청킹 코드는 Pinecone 데이터 준비용이므로 유지한다. 이번 변경은 약품 검색의 CSV 의존성 제거이며 상담·채팅·구매 SQLite 또는 챗봇 Pinecone 저장소 전환을 포함하지 않는다.

검증: `python -m pytest tests/test_drug_catalog.py tests/test_map_api.py tests/test_database_connection.py --import-mode=importlib -p no:cacheprovider -q`
