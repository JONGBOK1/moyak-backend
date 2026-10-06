# 지도 API

자판기 위치/재고를 지도 화면에 보여주기 위한 읽기 전용 API. (원작: 한별 — Supabase 조회 부분)

## 데이터 출처 (응답 `source`)

- `supabase`: `.env`에 `MAP_DATABASE_URL`(Supabase Session pooler 연결 문자열, 비밀번호는 URL 인코딩)이 있으면
  Supabase의 `vending_machines.location`(PostGIS 좌표)과 `machine_inventory.stock`을 **읽기 전용 트랜잭션**으로 조회한다.
  테이블 생성·변경은 하지 않는다.
- `local`: 없으면 메인 서버 DB를 쓴다. 서버 시작 시 **동양미래대학교 주변 실제 장소**(교내, 고척스카이돔, 구일역,
  개봉역, 구로구청)에 시연용 가상 자판기 M001~M005를 배치하고(`src/consult/map_seed.py`), 키오스크 의약외품 재고(`products`)를
  그대로 보여준다. M001은 키오스크 시연 자판기와 같은 ID라 앱 → 키오스크 흐름이 이어진다. 운영자가 고친 위치는 덮어쓰지 않는다.

### Supabase에 시연용 자판기 넣기

`scripts/supabase_demo_machines.sql`을 Supabase SQL Editor에서 실행한다 (2026-10-06 실제 스키마 확인 후 작성).
- 기존 VM-001(동양미래대학교 학생회관)의 잘못된 경도(126.8495 → 126.8670)를 보정
- 학교 주변에 VM-006~VM-009(고척스카이돔, 구일역, 개봉역, 구로구청) 추가
- 재고/가격 추가 (실제 e약은요 품목기준코드, VM-009는 품절 시연용). 여러 번 실행해도 중복되지 않음

`MAP_DATABASE_URL`은 지도 전용이다. **`DATABASE_URL`에 Supabase를 넣으면 안 된다** — 메인 서버가 시작 시
같은 이름의 `vending_machines` 테이블에 컬럼/행을 추가하려다 실패한다(스키마가 다름).

## 엔드포인트

- `GET /api/v1/map/config` — 지도 화면 초기화용: `kakao_js_key`(`.env`의 `KAKAO_JS_KEY`, 공개 키·도메인 제한), `default_center`(기본 동양미래대학교)

- `GET /api/v1/map/machines?lat=&lng=` — 운영 중이고 위치가 있는 자판기 최대 500개 (`code`: Supabase 자판기 코드 VM-001 등, 로컬은 id와 동일).
  `lat`/`lng`(내 위치)를 주면 가까운 순 정렬 + `distance_m`. 둘 중 하나만 주면 422.
  `items` 항목: `id`, `name`, `address`, `latitude`, `longitude`, `operating_hours`, `item_count`, `stock_count`, `distance_m`.
  `truncated: true`이면 조회 상한을 초과한 것.
- `GET /api/v1/map/machines/{id}?lat=&lng=` — 자판기 하나 + `items`(품목별 `item_seq`, `item_name`, `stock`, `price`, `category`).
  Supabase 재고는 품목기준코드만 있어 `src/api/drug_index.csv`로 약품명을 붙이고, 가격/분류는 `null`. 없거나 비활성이면 404.

개인정보·QR 토큰·DB 비밀번호는 응답에 포함하지 않는다. 재고는 요청 시점 값이며 실시간 구독은 아니다.

## 앱 화면

- `/app/map-screen/` — 카카오 지도 + 가까운 자판기 목록(거리·도보 시간·재고 상태) → 자판기 상세(품목별 재고, 길찾기) →
  "이 자판기 선택" 시 `localStorage.moyak_selected_machine`에 저장하고 `/app/main-home-connected/`로 이동.
  위치 권한이 없거나 가장 가까운 자판기가 5km 이상이면 동양미래대학교 주변을 보여준다. `?machine=ID`로 상세 바로 열기.
- `KAKAO_JS_KEY`가 없으면 지도 자리에 안내 문구만 나오고 목록/선택은 그대로 동작한다.
- 홈: 자판기를 선택했으면 "연결됨" 홈(선택한 자판기 이름 표시, "상품 구매하기" → 그 자판기 재고), 아니면 "연결 안 됨" 홈("기기 재탐색" → 지도).

## 실행

- 메인 서버에 포함됨: `uvicorn src.api.main:app` → `/api/v1/map/...`
- 지도만 단독 실행(메인 DB 초기화 없이): `python -m uvicorn src.api.map_app:app --port 8002`
  (CORS 허용 주소는 `MAP_ALLOWED_ORIGINS`, 쉼표 구분, 기본 `http://localhost:8080,http://127.0.0.1:8080`)

검증: `python -m pytest tests/test_map_api.py -q`
