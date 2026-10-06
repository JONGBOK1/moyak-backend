# 지도 API

자판기 위치/재고를 지도 화면에 보여주기 위한 읽기 전용 API. (원작: 한별 — Supabase 조회 부분)

## 데이터 출처 (응답 `source`)

- `supabase`: `.env`에 `MAP_DATABASE_URL`(Supabase Session pooler 연결 문자열, 비밀번호는 URL 인코딩)이 있으면
  Supabase의 `vending_machines.location`(PostGIS 좌표)과 `machine_inventory.stock`을 **읽기 전용 트랜잭션**으로 조회한다.
  테이블 생성·변경은 하지 않는다.
- `local`: 없으면 메인 서버 DB를 쓴다. 서버 시작 시 `DEMO_MAP_CENTER`("위도,경도", 기본 서울시청) 주변에
  시연용 가상 자판기 M001~M005를 배치하고(`src/consult/map_seed.py`), 키오스크 의약외품 재고(`products`)를 그대로 보여준다.
  M001은 키오스크 시연 자판기와 같은 ID라 앱 → 키오스크 흐름이 이어진다. 이미 좌표가 있는 자판기는 덮어쓰지 않는다.

## 엔드포인트

- `GET /api/v1/map/machines?lat=&lng=` — 운영 중이고 위치가 있는 자판기 최대 500개.
  `lat`/`lng`(내 위치)를 주면 가까운 순 정렬 + `distance_m`. 둘 중 하나만 주면 422.
  `items` 항목: `id`, `name`, `address`, `latitude`, `longitude`, `operating_hours`, `item_count`, `stock_count`, `distance_m`.
  `truncated: true`이면 조회 상한을 초과한 것.
- `GET /api/v1/map/machines/{id}?lat=&lng=` — 자판기 하나 + `items`(품목별 `item_seq`, `item_name`, `stock`, `price`, `category`).
  Supabase 재고는 품목기준코드만 있어 `src/api/drug_index.csv`로 약품명을 붙이고, 가격/분류는 `null`. 없거나 비활성이면 404.

개인정보·QR 토큰·DB 비밀번호는 응답에 포함하지 않는다. 재고는 요청 시점 값이며 실시간 구독은 아니다.

## 실행

- 메인 서버에 포함됨: `uvicorn src.api.main:app` → `/api/v1/map/...`
- 지도만 단독 실행(메인 DB 초기화 없이): `python -m uvicorn src.api.map_app:app --port 8002`
  (CORS 허용 주소는 `MAP_ALLOWED_ORIGINS`, 쉼표 구분, 기본 `http://localhost:8080,http://127.0.0.1:8080`)

검증: `python -m pytest tests/test_map_api.py -q`
