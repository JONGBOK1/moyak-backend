# 일반 서버 통합 — 2026-10-09

기준: 팀원 `master`의 `86e718c`. 사용자 웹·약사·키오스크를 로컬의 상담 기능과 통합했다. Flutter는 수정하지 않았다. GitHub 푸시·Render 배포·Supabase DDL은 수행하지 않았다.

## 실행

```powershell
.\venv\Scripts\python.exe scripts/run_unified_local.py --local-map
```

이 명령은 **`src.api.main:app` 일반 서버**를 로컬 시연 설정으로 실행한다. 서버는 로컬에서만 운영하며 Render 배포는 하지 않는다. `uvicorn`을 직접 실행하면 `.env`의 Supabase `DATABASE_URL`을 거래 DB로 써서 시작이 중단되므로 반드시 이 스크립트를 쓴다.

**Flutter 개발 시**에는 `--flutter`를 붙인다. `0.0.0.0`으로 열고 "이 PC 전용" 경계를 끄며 `localhost` 임의 포트(Flutter 웹)를 CORS·WebSocket에 허용한다. 실행 시 크롬/에뮬레이터(`10.0.2.2`)/실제 폰(LAN IP) 접속 주소를 출력한다. 인증이 없으므로 같은 와이파이에서만 쓴다. 포트는 `--port 8001`처럼 바꿀 수 있다.

```powershell
.\venv\Scripts\python.exe scripts/run_unified_local.py --flutter
```

사용자 앱 화면은 Flutter로 대체하므로 팀 HTML 사용자 화면(`static/app`)은 가져오지 않았다. `/`는 `/chat-test`로 이동한다.

- 약사: http://127.0.0.1:8000/pharmacist
- 키오스크: http://127.0.0.1:8000/kiosk/start/
- QR: http://127.0.0.1:8000/scan
- API 문서: http://127.0.0.1:8000/docs

상담·구매는 별도 `data/unified-local.db`에 저장한다. `--local-map`은 지도와 키오스크가 같은 로컬 M001 등의 자판기·재고를 사용하도록 한다. 옵션을 빼면 지도는 `.env`의 Supabase를 읽는다. Supabase UUID 자판기와 로컬 M001은 다르며 두 저장소의 재고는 자동 동기화되지 않는다. 약품 검색은 두 경우 모두 Supabase를 사용한다. 기존 8001 Flutter 시연 스크립트도 유지했다.

## 인증 (팀 방식 — 요청의 ID·역할 신뢰, 2026-10-09 변경)

Flutter 앱이 배포 서버에서 바로 쓸 수 있도록, 실제 로그인 연동 전까지는 **요청이 보낸 사용자 ID·역할을 그대로 신뢰**한다(팀 `master`와 같은 방식). 누구나 다른 ID나 약사 역할을 주장할 수 있으므로 실서비스 전 반드시 로그인 토큰으로 교체해야 한다.

신원 전달 방법 (우선순위 순):

1. `Authorization: Bearer <토큰>` — 기존 자체 서명 토큰. 로그인 서버가 붙으면 이것만 받도록 바꾼다.
2. `X-Moyak-User-Id: <ID>` + `X-Moyak-Role: user|pharmacist` 헤더
3. 팀 규격 바디 값 — `POST /consultations`의 `user_id`, `POST .../messages`의 `sender_id`/`sender_role`, `POST .../decision`의 `pharmacist_id`
4. WebSocket(`/consultations/{cid}/messages/ws`)은 첫 프레임 `{"sender_id", "sender_role", "after"}` (또는 `{"token"}`)

| 엔드포인트 | 신원 없이 호출 | 신원을 보내면 |
|---|---|---|
| 팀 규격: `POST/GET /consultations`, `GET /consultations/{id}`, `/cancel`, `/decision`, `/presence`, `GET /messages` | 허용 (팀과 동일) | 본인·담당 약사인지 검사 |
| `POST /messages` | 바디의 `sender_id`/`sender_role` 필수 | 헤더와 바디가 다르면 403 |
| 새 기능: `/session*`, `/audio`, `/transcript`, `/summary*`, `POST /end` | 401 | 참여자·역할 검사 |

- 약사가 아직 배정되지 않은 상담에 약사가 메시지를 보내면 그 약사에게 자동 배정한다. 배정 전 사용자 메시지는 409.
- WebSocket은 Origin 헤더가 없거나(Flutter 모바일), 같은 출처이거나, `CORS_ORIGINS`에 등록된 출처(Flutter 웹)만 허용한다.
- `MOYAK_LOCAL_IDENTITY=1`은 이제 신원 처리에는 영향이 없고, "이 PC에서만 접근" 시연 경계(미들웨어)로만 쓴다.

키오스크 라우트(`kiosk.py`, `shop.py`)는 이번 변경에서 건드리지 않았다. 결제·배출은 모의 처리이며 PG·배출기 연동은 별도다.

## 상담 시연

1. 사용자 웹에서 상담을 요청한다. 사전 챗봇은 기존 `chat_summary`로 전달된다.
2. 약사 화면 하단의 해당 상담 **음성 기록·AI 요약** 패널에서 `상담 연결`을 누른다.
3. 채팅은 WebSocket으로 수신한다. API는 `content`/`text`를 모두 받고 둘 다 반환한다. 재전송은 동일한 `client_id`를 사용해 중복을 방지한다.
4. 사용자는 영상 입장 후 패널을, 약사는 대시보드 패널을 사용한다. 각자 직접 동의한 뒤 마이크 녹음·업로드가 가능하다.
5. 각 기기의 마이크만 기록하며 Daily 통화 전체를 서버에서 녹화하지 않는다. 전사 실패는 같은 화면에서 재전송하거나 원본 파일을 선택해 복구한다. 미업로드 녹음은 페이지 이동 시 보존되지 않는다.
6. 약사가 승인/거절하고 전사가 끝나면 상담을 종료한다. 백그라운드 작업이 **사전 챗봇+전사+채팅+승인 결과**로 요약한다. 사전 챗봇은 약사 확정 안내와 구분한다.
7. 약사가 초안을 수정·공개한다. 기존 상담 조회 API도 공개된 요약만 반환한다. 사용자 화면의 통화 종료는 화면 이탈이며 서버 종료·공개 권한은 약사에게 있다.

팀원 CSS·화면 디자인은 유지하고 공통 `moyak_unified.js`와 필요한 이벤트를 연결했다. 새 기능을 위해 동의·녹음·검토 패널을 추가했다. 약사 패널은 대기 카드가 승인으로 사라져도 유지된다.

Daily 입장 알림은 약사 배정과 방 인원 조회를 사용한다. 사용자 입장 전 안내를 위한 추정이며 Daily 참여자의 역할을 인증하는 기능은 아니다. 상담 데이터 권한은 별도로 검사한다.

## 지도와 약품

지도 설정·목록·상세 API는 자판기 코드·거리·재고 합계·품목별 재고를 제공한다. `KAKAO_JS_KEY`와 `DEMO_MAP_CENTER`를 설정할 수 있다. 키가 없으면 팀원 페이지의 대체 지도 표시를 사용한다.

Supabase는 PostGIS·`machine_inventory`를 읽기 전용으로 조회하고 약품명은 `drugs` 우선, `drug_permissions` 보완으로 조회한다. CSV를 읽지 않는다. 로컬은 `vending_inventory`와 `products`를 합치며 같은 기기·품목이면 실제 키오스크의 `products`를 우선해 중복 합산을 막는다. 약사 자동완성의 기존 4,757개 범위도 유지한다.

## 배포 전 DB 작업

PostgreSQL 시작 시 READ ONLY 구조 검사만 한다. DDL과 시연 데이터 생성을 실행하지 않는다. 누락 테이블·컬럼과 ID 자료형이 다르면 시작을 중단한다. 전체 FK·인덱스·업무 제약을 검증하는 완전한 마이그레이션 도구는 아니다.

현재 Supabase 대조 결과:

- `vending_machines.id`: UUID, 현재 거래 ORM은 문자열 ID
- `vending_inventory`: 없음 (`machine_inventory`와의 거래 매핑 결정 필요)
- `products.id`, `kiosk_orders.id`, `kiosk_order_items.id/order_id/product_id`: UUID와 문자열 ORM 차이

**현재 설정 그대로 Render에서 Supabase 거래까지 운영할 수는 없다.** 기존 UUID를 강제로 변경하지 않고 거래 ORM·FK를 실제 DB에 맞추는 매핑 작업이 필요하다. 이번에는 공유 DB를 변경하지 않았다. 로그인·기기 인증과 DB 매핑을 완료한 뒤 `uvicorn src.api.main:app --host 0.0.0.0 --port $PORT`로 배포한다. 배포에서 `MOYAK_LOCAL_IDENTITY`, `MOYAK_LOCAL_DEMO`는 사용하지 않는다.

AI worker는 일반 서버 lifespan에 포함되며 기존 별도 worker 실행도 지원한다. 실제 전사·요약은 OpenAI 크레딧이 필요하다. 챗봇 리소스는 첫 요청 시 초기화한다.

## 검증

`tests/test_unified_app.py`: 웹 제공, 인증 경계, 기존 메시지 규격·중복 방지·WebSocket, 음성 업로드→전사→사전 챗봇 포함 요약→약사 공개, QR→결제→수령, 지도 중복 합산 방지, 읽기 전용 스키마 검사. AI는 모의 처리한다. 실제 Supabase 지도 목록·상세도 읽기 전용으로 정상 응답을 확인했다. 물리 마이크·두 기기 통화·실제 결제/배출은 별도 검증 대상이다.
