# 지도 API 실행

지도 전용 서버는 Supabase를 조회만 하며 테이블 생성·변경을 수행하지 않습니다.

1. `pip install -r requirements.txt`
2. 서버의 `.env`에 `MAP_DATABASE_URL`을 Supabase Session pooler 연결 문자열로 설정합니다.
   비밀번호는 URL 인코딩하고 `.env`는 Git에 올리지 않습니다.
3. `python -m uvicorn src.api.map_app:app --host 127.0.0.1 --port 8002`
4. `http://127.0.0.1:8002/api/v1/map/machines`를 확인합니다.

`MAP_DATABASE_URL`이 없으면 `DATABASE_URL`을 사용합니다. 지도만 확인할 때는
기존 전체 앱의 DB 초기화를 피하도록 위의 `map_app` 진입점을 사용하세요.
배포 시 `MAP_ALLOWED_ORIGINS`에 프론트 주소를 쉼표로 구분해 지정합니다.
기본 허용 주소는 `http://localhost:8080,http://127.0.0.1:8080`입니다.

API는 운영 중이며 위치가 등록된 자판기를 최대 500개 반환합니다.
`vending_machines.location`의 PostGIS 좌표와 `machine_inventory.stock` 합계를 읽습니다.
개인정보·QR 토큰·DB 비밀번호는 응답에 포함하지 않습니다.
`items` 항목: `id`, `name`, `address`, `latitude`, `longitude`, `operating_hours`,
`item_count`, `stock_count`. `truncated: true`이면 조회 상한을 초과한 것입니다.
재고는 요청 시점의 값이며 자동 실시간 구독은 아닙니다.

검증: `python -m pytest tests/test_map_api.py --import-mode=importlib -q`.
