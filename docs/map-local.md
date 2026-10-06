# 현재 앱의 지도 실행

프론트 경로: `E:\Moyak\moyak-front`

```powershell
cd E:\Moyak\moyak-front
E:\flutter\bin\flutter.bat build web --base-href /app/
cd E:\Moyak\moyak-backend-master
.\venv\Scripts\python.exe scripts\run_consult_demo.py
```

`http://127.0.0.1:8001/app/`에서 로그인 버튼 → 지도 탭을 선택합니다.
실제 OpenStreetMap 배경 위에서 자판기를 선택하거나 이름·주소를 검색할 수 있습니다.
선택한 자판기의 주소, 운영 시간, 등록 품목 수, 재고 합계를 표시합니다.
새로고침 버튼으로 DB의 최신 상태를 다시 조회합니다. 재고는 자동 실시간 구독이 아닙니다.

시연 실행기는 백엔드 `.env`의 `DATABASE_URL`을 지도 조회용 `MAP_DATABASE_URL`로
전달합니다. 지도 API `/api/v1/map/machines`는 Supabase를 읽기 전용으로 조회하고,
상담 데이터는 별도 로컬 SQLite `data/consult-demo.db`를 사용합니다.
지도 연결이 상담의 Supabase 저장이나 실제 회원 로그인을 활성화하지는 않습니다.
DB 비밀번호는 프론트로 전달하지 않습니다.

지도는 위치가 등록된 운영 자판기만 최대 500곳 표시합니다. 위치 데이터가 없으면
가상의 마커를 만들지 않습니다. 현재 위치 권한, 길찾기, 개별 약품 재고 상세는
이번 지도 연결 범위에 포함되지 않습니다.

OpenStreetMap 출처 표기를 유지하며, 배경 지도에는 인터넷 연결이 필요합니다.
페이지가 이전 화면을 보여주면 Ctrl+F5로 새로고침하세요.
