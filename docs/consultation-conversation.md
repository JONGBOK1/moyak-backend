# 상담 채팅·음성 전사·AI 요약 연결 가이드

## 로그인 연결 전 로컬 시연

`python scripts/run_consult_demo.py` 실행 후 http://127.0.0.1:8001 을 연다.
이 서버는 `data/consult-demo.db`를 사용하며 기존 DB 및 Supabase에 연결하지 않는다.
시연용 토큰을 자동 발급하고 AI 작업도 자체 처리하므로 별도 worker가 필요하지 않다.
일반 API 서버에는 시연 토큰 발급 경로가 추가되지 않는다. 외부 IP와 다른 사이트의 요청은 거부한다.

새 영상상담 시작 → 약사 화면 열기 → 두 탭에서 각각 통화 참여 → 카메라·마이크 허용 →
영상 옆 채팅으로 메시지를 주고받는다. 영상은 왼쪽, 현재 역할의 채팅창은 오른쪽에 표시된다.
`DAILY_API_KEY`가 없으면 브라우저 WebRTC로 직접 연결하며, 별도의 외부 영상 API 키가 필요 없다.
이 모드는 로컬 PC의 두 탭 시연용이다. 시그널링은 이 서버의 인증된 WebSocket을 통해 전달하고
영상·음성은 브라우저끼리 직접 전송한다. 마이크/카메라 켜기·끄기와 나가기·재참여를 지원한다.
다른 기기/외부 네트워크 서비스에는 HTTPS, 공개 접속, STUN/TURN, 실제 인증을 별도로 구성해야 한다.
`DAILY_API_KEY`가 있으면 기존 Daily 영상방을 같은 레이아웃에 표시한다. 한 PC에서 두 탭으로 통화할 때는
스피커 울림에 주의하고, 시연 상대방 창도 영상과 채팅이 함께 보이는 앱 화면을 사용한다.
좁은 화면에서는 영상이 상단에 고정되어 채팅 중에도 보인다.
AI 요약은 양쪽 동의 후 채팅/음성 기록 → 상담 종료·AI 요약 생성 → 약사 초안 수정·공개 순서다.
예시 대화는 시연 도구 아래에 있으며 가상의 자료다. 전사와 요약은 실제 OpenAI API를 호출한다.
실제 상담 자료 대신 시연용 내용으로 테스트한다. API 오류는 화면에 실패로 표시하며 가짜 결과로 대체하지 않는다.
마이크 기록은 별도 버튼으로 시작하며 양측의 동의가 필요하다. WebRTC 화면은 커스텀 UI이며
Daily 경로를 선택한 경우에는 Daily 기본 UI를 사용한다.
시연 토큰은 탭의 sessionStorage에 보관해 새로고침 시 같은 상담을 복원한다.
상대방 탭은 같은 PC에서 열리며 시연 세션을 복사해 동일 상담에 참여한다. 공개 초대/실제 로그인 기능이 아니다.
기존 기록은 시연 DB에 남는다.

추후 Supabase PostgreSQL로 옮길 때는 정식 API 서버의 `DATABASE_URL`, PostgreSQL 드라이버,
테이블 마이그레이션 및 인증 연결을 적용한다. Supabase Auth 토큰 검증과 약사 역할 검증은 별도 구현이 필요하다.
연결 방식을 바꿔도 이 시연 실행 스크립트는 의도적으로 로컬 DB만 사용한다.

## 실행과 인증

1. `.env`에 `CONSULT_AUTH_SECRET`(32자 이상 무작위 값), `OPENAI_API_KEY`를 설정한다.
   모델 설정은 `.env.example`을 참고한다.
2. API 실행: `python -m uvicorn src.api.main:app`.
3. 별도 작업 프로세스 실행: `python -m src.consult.worker`.

API와 worker는 같은 영구 `DATABASE_URL`을 사용해야 한다. 초기화 시 새 테이블이 추가되며
기존 상담 데이터를 삭제하거나 컬럼을 변경하지 않는다.

로그인 서버 연동 지점은 `src.consult.auth.issue_token(subject, role)`이다.
**로그인과 약사 자격을 검증한 서버에서만** 호출한다. 역할은 `user` 또는 `pharmacist`이고
기본 만료는 1시간이다. 공개 토큰 발급 API는 제공하지 않는다. 비밀값을 앱에 넣으면 안 된다.
HTTP 요청은 `Authorization: Bearer <token>`을 사용한다.

현재 프로젝트에는 실제 로그인/약사 자격 검증 서버가 없다. 이 부분은 별도로 연결해야 한다.
`CONSULT_AUTH_SECRET` 설정 시 기존 `/consultations` 생성·목록·조회·승인에도 인증이 적용되므로
기존 데모 페이지도 토큰을 전달하도록 바꿔야 한다. 미설정 시 기존 데모는 유지되지만 새 API는
인증을 통과할 수 없다. 자판기 등 다른 기존 API의 인증을 이번 변경으로 대체하지 않는다.

## 연결 순서

`{id}`는 기존 `POST /consultations`로 생성한 상담 ID다.

| 순서 | API | 동작 |
|---|---|---|
| 1 | `POST /consultations/{id}/session/claim` | 인증된 약사가 대기 상담을 배정받는다. |
| 2 | `GET /consultations/{id}/session` | 배정 약사, 양측 동의 시각, 종료 여부 조회 |
| 3 | `POST /consultations/{id}/messages` | 직접 입력한 텍스트 전송 |
| 4 | `GET /consultations/{id}/messages?after=0&limit=100` | 저장 내역 조회 |
| 5 | `POST /consultations/{id}/session/consent` | 음성 기록 및 AI 처리 동의 후 양측이 각자 호출 |
| 6 | `POST /consultations/{id}/audio?client_id=UUID&start_ms=0` | 각자 자신의 음성 파일 업로드 |
| 7 | `GET /consultations/{id}/audio` | 업로드별 전사 상태 확인 |
| 8 | 기존 `POST /consultations/{id}/decision` | 담당 약사가 승인·거절 확정 |
| 9 | `POST /consultations/{id}/session/end` | 담당 약사가 종료. 양측 동의가 있으면 요약 등록 |
| 10 | `GET /consultations/{id}/summary` | 작업 상태와 결과 조회 |
| 11 | `GET /consultations/{id}/transcript` | 담당 약사가 전사 원문 검토 |
| 12 | `POST /consultations/{id}/summary/publish` | 담당 약사가 수정한 최종 요약 확정·공개 |

동의하지 않아도 채팅은 가능하다. 양측 동의가 없으면 종료 시 AI 작업을 만들지 않는다.
종료 API는 **우리 서버의 상담 상태**를 종료한다. Daily 통화를 끊는 SDK 호출은 화면에서 별도로 한다.

## 텍스트 채팅과 재접속

```json
{"client_id": "550e8400-e29b-41d4-a716-446655440000", "text": "복용 시간을 다시 알려주세요"}
```

`client_id`는 영문·숫자·`_`·`-`만 허용(1~80자)한다. 텍스트는 공백을 제외하고 1~4000자다.
동일 발신자의 동일 `client_id` 재전송은 같은 메시지를 반환한다. 내용이 다르면 409다.
숫자 `id`를 저장하고 재접속할 때 `after`로 전달한다. 종료 뒤에는 조회만 가능하다.

WebSocket 경로: `/consultations/{id}/messages/ws`.
첫 프레임(10초 이내): `{"token":"상담 토큰","after":0}`. 토큰은 URL에 넣지 않는다.
그 뒤에는 HTTP와 같은 메시지 JSON을 전송한다.

- `{"type":"ack","message":{...}}`: 메시지 저장 완료
- `{"type":"messages","items":[...]}`: 메시지 묶음. `id`로 중복 제거한다.
- `{"type":"error","detail":"..."}`: 입력 또는 상태 오류
- 인증·권한 실패/토큰 만료 시 소켓 종료 코드 1008

DB를 최대 0.5초 간격으로 읽으므로 여러 API 프로세스에서도 전달된다.
대규모 동시접속을 위한 Redis pub/sub는 아직 사용하지 않는다.

## 음성 입력

Daily 기본 iframe의 소리는 백엔드가 자동 수집할 수 없다. 프론트엔드의 커스텀 통화 화면에서
동의 이후 **자신의 마이크 트랙만** 녹음해 보내야 한다. 이 작업은 프론트엔드에 남아 있다.
혼합된 양측 음성을 한 참여자의 파일로 보내면 화자 표기가 틀리므로 그렇게 보내지 않는다.

- multipart가 아닌 **파일 원본 bytes**를 본문으로 보낸다.
- Content-Type: `audio/webm`, `audio/wav`, `audio/x-wav`, `audio/mpeg`, `audio/mp4`, `audio/x-m4a`.
- 파일당 20 MiB, 상담당 240개 파일. `start_ms`는 공유하는 통화 시작 기준 0~7,200,000ms.
- 각 파일은 독립적으로 디코딩 가능해야 한다. MediaRecorder timeslice 조각은 첫 파일 이후에
  헤더가 없을 수 있으므로, 녹음 stop/start로 완성 파일을 만들거나 완성된 전체 파일을 보낸다.
- 상태: `queued → processing → completed / failed`.
- 실패하면 같은 `client_id`로 원본을 재업로드한다. 실패 파일은 교체되어 재처리된다.
- 성공·실패 후 원음 bytes는 DB에서 제거한다. 전사 텍스트는 채팅과 분리해 보관한다.
- 양쪽 마지막 파일까지 업로드하고 전사 완료를 확인한 뒤 종료 API를 호출한다.
  누락 업로드는 서버가 탐지할 수 없어 요약에 제출 자료 범위의 한계를 명시한다.
- 미처리·실패 파일이 있으면 종료는 409다. 재처리한 후 종료한다.

발신자는 토큰에서 결정한다. 녹음 내용과 전사 정확도는 약사가 별도로 검토해야 한다.

## 요약과 약사 검토

상태: `not_requested`(미생성), `queued`, `processing`, `pending_review`, `published`, `failed`.
사용자에게는 `published` 전까지 `content: null`을 반환한다. 담당 약사는 초안을 볼 수 있다.
실패 시 담당 약사가 `POST /consultations/{id}/summary/retry`로 재시도한다.
작업은 DB에 남고, worker가 죽으면 5분의 작업 임대 기간 이후 다시 처리한다.

요약 입력은 종료 시 고정한 전사·채팅·확정 승인 정보다. 사전 챗봇 요약을 통화 내용으로 쓰지 않는다.
입력 한도는 JSON 기준 120,000자이며 초과 시 종료 API는 413, 자료가 없으면 409다.
AI에 추측 금지 지시를 주지만 정확성을 보장하지는 않으므로 약사 검토가 필수다.
최종 확정 body 예:

```json
{
  "symptoms": ["사용자가 호소한 증상"],
  "discussion": ["실제 상담에서 확인한 내용"],
  "medication_guidance": [],
  "precautions": [],
  "follow_up": [],
  "needs_verification": ["음성에서 명확하지 않은 항목"]
}
```

모든 필드가 필수이며 각 배열은 최대 30개, 항목은 1~2000자다. 없는 정보는 빈 배열로 보낸다.
AI 초안과 확정본, 검토 약사, 검토 시각을 따로 저장한다. 확정본은 재생성으로 덮어쓰지 않는다.
전사·채팅·요약의 보관 기간과 삭제 정책은 현재 자동화되어 있지 않다.

## 검증

### Flutter Web 로컬 연동

프론트 `E:\Moyak\moyak-front`의 상담하기 버튼은 앱 내부 상담 화면을 연다.
상담 화면, 메시지 목록, 입력창, 동의, 종료 및 요약 검토는 Flutter 위젯으로 구현한다.
Flutter가 REST API와 인증된 WebSocket을 직접 호출한다. Daily 영상 영역에만
iframe을 사용하며, 채팅 창을 열고 닫아도 영상 iframe은 유지된다.
Daily 키와 AI 키는 백엔드 `.env`에만 둔다.

프론트를 수정한 뒤 빌드:

```powershell
cd E:\Moyak\moyak-front
E:\flutter\bin\flutter.bat build web --base-href /app/
```

빌드 후 백엔드 시연 서버를 시작하거나 재시작:

```powershell
cd E:\Moyak\moyak-backend-master
.\venv\Scripts\python.exe scripts\run_consult_demo.py
```

`http://127.0.0.1:8001/app/`에서 로그인 화면을 지나 채팅 탭 → 상담하기를 누른다.
상담 시작 → 카메라/마이크 허용 → 약사 시연 화면 열기로 두 참여자를 시연한다.
약사 창도 Flutter 화면이며, 양측 동의와 대화 후 약사가 상담 종료를 누르면
AI 요약을 생성한다. 약사는 요약을 수정·공개하고 사용자는 확정된 요약만 조회한다.
현재 로그인/역할 선택은 시연용이고 데이터는 `data/consult-demo.db`에 저장된다.
Supabase 회원 인증 및 운영 데이터 저장은 아직 이 Flutter 흐름에 연결되지 않았다.
현재 Flutter 화면은 채팅 기반 요약을 제공한다. 음성 기록 버튼은 기존 `/` 웹 시연에만
있으며, Flutter 마이크 녹음 및 Daily 통화 전체 자동 녹음은 아직 연결하지 않았다.
모바일 네이티브 앱에는 안내 문구가 표시되며 이번 연동 대상은 PC 브라우저다.

2026-10-06 Flutter 연동 검증: 웹 빌드와 기존 위젯 테스트 통과. 실제 브라우저에서
Daily 영상방 표시, 영상 DOM을 유지한 채팅 열기/닫기, WebSocket 메시지 수신,
Flutter 입력 메시지의 서버 저장, 상담 종료 및 요약 작업 등록을 확인했다.
물리 카메라/마이크로 두 사람 간 통화는 별도 확인이 필요하다.
실제 요약 API는 `429 credit_balance_exhausted`를 반환했다. AI 계정 잔액을 충전한 후
약사 요약 화면에서 재시도해야 하며, 이번 Flutter 화면의 생성 완료/공개 흐름은
실제 AI 응답으로 끝까지 검증하지 못했다.

```powershell
.\venv\Scripts\python.exe -m pytest --import-mode=importlib -p no:cacheprovider tests/test_conversation.py tests/test_consult_service.py -q
```

`tests/src`에 예전 코드 사본이 있으므로 `--import-mode=importlib`으로 현재 `src`를 검증한다.
테스트는 외부 AI 호출을 모킹한다. 실제 Daily 통화 수집, OpenAI 계정의 모델 접근 및 전사 품질은
프론트 연결 후 별도 확인이 필요하다.

공식 문서: [OpenAI 음성 전사](https://developers.openai.com/api/docs/guides/speech-to-text),
[OpenAI 구조화 출력](https://developers.openai.com/api/docs/guides/structured-outputs).
