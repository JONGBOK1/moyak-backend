# MOYAK - RAG 챗봇 '모약이' 개발 가이드 (Claude Code 프로젝트 컨텍스트)

이 문서는 Claude Code가 이 프로젝트에서 작업할 때 항상 참고해야 하는 배경 정보입니다.
새로운 세션을 시작할 때마다 이 문서를 먼저 읽고, 아래 원칙과 구조를 따라 코드를 작성해주세요.

## 1. 프로젝트 개요

MOYAK은 약 자판기 연동 앱입니다. 그중 나(사용자)는 **RAG 기반 챗봇 '모약이' 개발**을 담당하고 있습니다.

'모약이'는 e-약은요(식품의약품안전처 공공데이터) 기반 RAG 챗봇으로, 향후 사진(약 봉투/알약)으로 질문하는
멀티모달 기능까지 확장할 예정입니다. **현재는 멀티모달 이전 단계, 텍스트 기반 RAG 파이프라인 완성에 집중**합니다.

### 왜 RAG인가
일반 LLM은 근거 없이 약 정보를 지어낼 위험(할루시네이션)이 있습니다. 검색된 공식 자료 안에서만 답변하게 해서
**신뢰성과 출처 확보**가 이 프로젝트의 핵심 목표입니다. 주 사용자층이 노년층이라 답변은 쉽고 명확해야 합니다.

## 2. 기술 스택

- 데이터 소스: 식약처 e약은요 공공데이터 API
- 정제: Python + Pandas
- 임베딩: OpenAI Embeddings (`text-embedding-3-small`)
- 벡터 DB: Pinecone
- 체인 프레임워크: LangChain
- LLM: GPT-4o (temperature 0.2로 고정 — 보수적이고 일관된 답변 유도)
- API 서버: FastAPI
- 컨테이너: Docker / Docker Compose (팀 공통 개발 환경)
- 버전관리: Git + GitHub (GitHub Actions CI 예정)
- (추후 확장) 멀티모달: GPT-4o Vision / 위치검색: PostGIS / 자판기 통신: MQTT

## 3. 데이터 소스 상세 (e약은요)

- 데이터셋명: 식품의약품안전처_의약품개요정보(e약은요)
- 요청 주소: `http://apis.data.go.kr/1471000/DrbEasyDrugInfoService/getDrbEasyDrugList`
- 인증키: **이미 발급 완료**. `.env`의 `EYAK_SERVICE_KEY`로만 관리하고, 코드나 이 문서에 절대 하드코딩하지 않는다.
- 주요 요청 파라미터: `serviceKey`, `pageNo`, `numOfRows`, `entpName`(업체명), `itemName`(제품명), `type=json`
- 주요 응답 필드 (2026-08-08 실제 응답으로 검증 완료, CLAUDE.md 최신화됨):
  - `itemName`(제품명), `entpName`(업체명), `itemSeq`(품목기준코드)
  - `efcyQesitm`(효능), `useMethodQesitm`(사용법), `atpnWarnQesitm`(경고), `atpnQesitm`(주의사항)
  - `intrcQesitm`(상호작용), `seQesitm`(부작용), `depositMethodQesitm`(보관법)
  - `atpnWarnQesitm`(경고)은 최초 설계 문서엔 없었으나 실제 응답에 존재해 STEP 2 청킹부터 별도 필드로 포함시킴
- 개발계정 트래픽: 하루 10,000건. **초기 수집 후 `data/raw/`에 로컬 저장해서 재사용**하고, 매번 API를 다시 호출하지 않는다.

## 4. 폴더 구조

```
moyak-backend/
├── .env                        # API 키 (git에 올리지 않음)
├── .env.example                 # 키 값 비운 템플릿
├── .gitignore
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
│
├── data/
│   ├── raw/                    # e약은요 원본 수집본
│   └── processed/               # 정제 완료본
│
├── notebooks/                   # 실험용 (프로덕션 코드로 확정 전 단계)
│   ├── 01_data_collection.ipynb
│   ├── 02_cleaning_exploration.ipynb
│   ├── 03_chunking_experiments.ipynb
│   └── 04_rag_chain_test.ipynb
│
├── src/
│   ├── config.py                # 환경변수 로드, 상수
│   ├── ingestion/
│   │   ├── fetch_eyakeunyo.py   # STEP 0: API 수집
│   │   └── clean.py             # STEP 1: 정제
│   ├── indexing/
│   │   ├── chunking.py          # STEP 2: 청킹
│   │   ├── embedding.py         # STEP 3: 임베딩
│   │   └── pinecone_index.py    # STEP 4: 인덱싱
│   ├── rag/
│   │   ├── prompts.py           # 프롬프트 템플릿
│   │   └── chain.py             # STEP 5: RAG 체인
│   └── api/
│       ├── main.py              # FastAPI 진입점
│       └── routes/chat.py
│
├── tests/
│   ├── test_cleaning.py
│   └── test_chunking.py
│
└── scripts/
    └── reindex_all.py            # 전체 재인덱싱용 1회성 스크립트
```

## 5. 개발 파이프라인 (반드시 이 순서로 진행)

1. **STEP 0 - 데이터 수집**: e약은요 API를 페이지네이션으로 전체 호출 → `data/raw/`에 JSON 저장
2. **STEP 1 - 정제**: Pandas로 HTML 태그 제거, 결측치/중복(`itemSeq` 기준) 처리 → `data/processed/`에 CSV 저장
3. **STEP 2 - 청킹**: 필드별로 청크 생성 (효능/사용법/경고/주의사항/상호작용/부작용/보관법 각각 별도 청크).
   메타데이터에 `item_seq`, `item_name`, `company`, `field` 포함
4. **STEP 3 - 임베딩**: `text-embedding-3-small`로 청크 텍스트 임베딩, 배치로 처리
5. **STEP 4 - 인덱싱**: Pinecone 인덱스(dimension 1536, metric cosine)에 upsert
6. **STEP 5 - RAG 체인**: LangChain으로 검색+생성 체인 구성. 질문을 세 유형으로 분기 처리한다(`src/rag/chain.py`).
   - **specific(특정 약 질문)**: 필드 혼합 `top_k=5` 검색 후 프롬프트 컨텍스트로 삽입 (기존 방식)
   - **symptom(증상 기반 추천)**: ① GPT-4o-mini로 질문을 임상 키워드로 변환(`build_symptom_query`) → ② 효능(`field=efficacy`) 필드만 Pinecone 메타데이터 필터링해 후보 약 최대 3개 선정 → ③ 후보별 전체 필드(효능/사용법/주의사항 등)를 `item_seq` 필터로 모아 컨텍스트 구성 → ④ 추천 이유+복용법+주의사항을 포함하도록 별도 프롬프트(`RECOMMEND_SYSTEM_PROMPT`)로 생성. 후보가 실제로 증상과 무관하면 추천하지 않고 거부하도록 강제.
   - **interaction(병용/상호작용 확인)**: ① GPT-4o-mini로 질문에 언급된 약 이름을 추출(`extract_drug_names`, 최대 3개) → ② 각 약 이름을 실제 등록 품목에 매칭해 전체 필드를 `item_seq` 필터로 모음(`_resolve_drug_docs`) → ③ 전용 프롬프트(`INTERACTION_SYSTEM_PROMPT`)로 답변 생성. **가장 중요한 규칙**: 자료에 특정 조합에 대한 언급이 없다고 "안전하다"고 결론 내리지 않고, "확인되지 않음 + 약사 상담"으로 답하도록 강제한다(e약은요 상호작용 데이터는 완전한 약물-약물 매트릭스가 아니라 각 약이 자체적으로 명시한 일반 문구이기 때문).
   - 질문 유형 분류는 `classify_intent`(GPT-4o-mini)가 담당하며, 대화 후속 질문 재작성(`rewrite_standalone_question`) 이후에 실행된다.
   - **특수 대상자(임산부/소아/고령자) 안전 필터**: 유형 분류와 별개로 `detect_population`(GPT-4o-mini)이 질문에서 임산부/소아/고령자 언급을 감지한다. specific 질문에서 감지되면, 일반 top_k 검색 대신 `extract_drug_names`+`_resolve_drug_docs`(interaction과 동일한 방식)로 정확한 약의 전체 필드(주의사항/경고 포함)를 확실히 가져온다 — "임산부가 먹어도 돼?" 같은 수식어가 top_k 검색을 엉뚱한 약으로 새게 만드는 문제를 막기 위함. symptom 질문에서 감지되면 `RECOMMEND_SYSTEM_PROMPT`의 규칙에 따라 후보 중 해당 대상자 금기 약을 제외하고, 안전한 후보가 없으면 추천하지 않는다.
   - **식약처 공식 데이터 보강 (`src/rag/drug_facts.py`, `DRUG_DB_ENRICH=1`일 때만)**: 본문 검색은 그대로 Pinecone(e약은요)이고, Supabase의 `drug_permissions`(허가정보 42,962건: 전문/일반·주성분, e약은요에 없는 약도 이름으로 검색), `dur_warnings`(임부금기·노인주의·특정연령대금기·효능군중복·분할주의), `dur_conflicts`(병용금기 성분쌍)를 읽기 전용으로 조회해 `[식약처 공식 데이터]` 블록으로 컨텍스트에 덧붙인다.
     - 병용금기는 LLM 판단이 아니라 목록 일치로 결정적으로 판정(DUR 성분코드 우선, 없으면 염·수화물을 뗀 성분명 **완전 일치**만 — 부분 일치는 잘못된 금기 경고를 만들 수 있어 사용 안 함). 목록에 없으면 "확인되지 않음(안전하다는 뜻 아님)"으로 명시.
     - 병용/특수대상자 질문은 허가정보에서 정확한 제품을 먼저 찾아(`resolve_name_seqs`) Pinecone을 그 품목으로 필터링 — 이름이 비슷한 다른 제품(예: 원펜정→일펜정)을 고르던 문제 해결.
     - **허가정보 의미 검색**: `scripts/index_permissions.py`로 허가 '정상'이면서 e약은요에 없는 30,495개 품목(주로 전문의약품)을 Pinecone 같은 인덱스의 **별도 namespace `permissions`**에 임베딩(제품명·전문/일반·약효분류·주성분·성상·보관법, 분류 빈 품목은 같은 주성분 품목 분류로 보완, 2026-10-09 업로드 완료). specific(정보) 질문에서만 top 4를 `[식약처 의약품 허가정보 검색 결과]`로 덧붙이고 증상 추천에는 쓰지 않는다(전문의약품 추천 방지). e약은요 자료가 있으면 그걸 우선, 전문의약품은 처방 필요만 안내하고 복용법은 안내하지 않도록 프롬프트로 강제. 기본 namespace(e약은요)는 그대로라 기존 검색 결과에 영향 없음. `DRUG_PERMISSION_SEARCH=0`으로 이것만 끌 수 있음.
     - 근거(`evidence`)에 `field: "dur"`(라벨 "식약처 허가·DUR") 항목이 추가될 수 있음. 꺼져 있거나 DB 조회 실패 시 기존 동작과 100% 동일. 2026-10-09 실제 질문 검증 + `check_rag_quality.py` 전체 통과(켜진 상태). 중간점검(10/13) 시연부터 사용하기로 결정(2026-10-09) — 로컬 `.env`에 켜짐, Render는 환경변수 `DRUG_DB_ENRICH=1` 추가 필요. 켜면 답변당 약 +1.5초(측정: 4.8→6.3초). 앱 근거 화면은 `dur` 근거를 주의 박스로 표시하고 출처에 '의약품 허가정보·DUR'을 덧붙인다.
   - 답변에서 실제로 인용된 약품명만 출처/근거로 남기는 `_extract_cited`는 LLM이 긴 제품명의 띄어쓰기를 살짝 바꿔 쓰는 경우가 있어 공백 제거 후 비교한다.
7. **STEP 6 - API 서버**: FastAPI `/chat` 엔드포인트. 프론트(Flutter)와 JSON 스펙 맞추기
   - 요청: `POST /chat` `{"question": "string", "history": [{"role": "user"|"assistant", "content": "string"}, ...]}`
     - `history`: 이전 대화 턴(선택, 기본값 빈 배열). 서버는 세션을 저장하지 않는 완전 무상태(stateless) 방식이라, 프론트가 대화 기록을 들고 있다가 매 요청마다 함께 보낸다.
     - 후속 질문(예: "부작용은?")은 검색 전에 GPT-4o-mini로 독립형 질문("활명수의 부작용은?")으로 재작성한 뒤 검색한다(`rewrite_standalone_question`). 이 재작성은 검색에만 쓰이고, 최종 답변 생성에는 원래 질문 + history 전체가 그대로 전달된다.
   - 응답: `{"answer": "string", "sources": ["string", ...], "evidence": [{"item_name", "field", "field_label", "text"}, ...]}`
     - `evidence`: 답변이 실제로 인용한 원본 청크 목록(신뢰도 어필용 — "AI 요약"이 아니라 식약처 원문 그대로임을 사용자에게 보여주기 위해 추가)
   - 헬스체크: `GET /health` → `{"status": "ok"}`
   - 루트: `GET /` → 앱 첫 화면 `/app/splash/`로 리다이렉트 (서버 주소만 입력해도 앱 전체 흐름을 볼 수 있게)
   - 테스트용 페이지: `GET /chat-test` → 한국어 웹 UI (`src/api/static/chat_test.html`), 답변/출처/원문 근거를 브라우저에서 바로 확인 가능
   - **요청량 제한**: `/chat`은 IP당 15회/분, 200회/일로 제한(`slowapi`, `src/api/limiter.py`). 초과 시 `429` + `{"detail": "요청이 너무 많습니다..."}`. 실제 비용이 드는 엔드포인트라 남용 방지용. Render처럼 프록시 뒤에 배포할 때는 uvicorn에 `--proxy-headers`를 켜야 진짜 클라이언트 IP로 카운트된다(안 켜면 전부 같은 IP로 잡혀 프록시 하나가 전체 사용자의 한도를 공유하게 됨).
   - (이 스펙이 바뀌면 반드시 이 문서와 팀에 공유할 것)
8. **STEP 7 - 테스트**: 정제/청킹 로직 유닛 테스트(`pytest`, API 호출 없음), RAG 답변 품질 셀프 체크(`python tests/check_rag_quality.py`, 실제 API 호출·소액 비용 발생·수동 실행 전용이라 pytest 자동 수집 대상 아님)

## 6. 안전/품질 요구사항 (프롬프트 설계 시 반드시 반영)

- **근거 기반 답변 강제**: 검색된 자료 안의 내용만 사용. 없으면 "제공된 자료에서 확인되지 않습니다. 약사와 상담하시는 것을 권장드립니다"라고 답한다.
- **출처 명시**: 답변마다 참고한 약품명을 항상 표시한다.
- **temperature 0.2 고정**: 보수적이고 일관된 답변 유도.
- **위험 질문 가드레일**: 복용량, 병용금기 등 위험할 수 있는 질문에는 반드시 약사 상담을 권유하는 문구를 포함한다.
- **쉬운 말 사용**: 주 사용자층이 노년층이므로 짧고 명확한 문장으로 답변한다.

## 7. 현재 진행 상황

- [x] e약은요 API 키 발급 완료
- [x] 폴더 구조 설계 완료
- [x] STEP 0: 데이터 수집 스크립트 작성 및 실행 (4,774건 `data/raw/eyakeunyo_raw.json` 저장 완료)
- [x] STEP 1: 정제 파이프라인 (중복 17건 제거, 4,757건 `data/processed/eyakeunyo_clean.csv` 저장 완료)
- [x] STEP 2: 청킹 (필드 7종 x 품목별, 27,956개 청크 `data/processed/chunks.jsonl` 저장 완료)
- [x] STEP 3: 임베딩 (27,956개 청크 전부 임베딩 완료, `data/processed/embeddings.jsonl`)
- [x] STEP 4: 인덱싱 (Pinecone 인덱스 `moyak-eyakeunyo`에 27,956개 벡터 upsert 완료, dimension 1536 / cosine)
- [x] STEP 5: RAG 체인 (`prompts.py`/`chain.py` 작성, top_k=5 검색 + GPT-4o 생성, 근거기반/출처/가드레일 규칙 실제 질문으로 검증 완료)
- [x] 대화 히스토리 지원 (`history` 파라미터, 무상태 서버 + 후속질문 재작성으로 대명사/생략 주어 해결, 실제 멀티턴 시나리오로 검증 완료)
- [x] 증상 기반 약 추천 (efficacy 필드 우선 검색 → 후보 최대 3개 전체 정보 취합 → 추천이유+복용법+주의사항 응답, 실제 질문으로 검증 완료 — 로드맵 Phase 3)
- [x] 병용/상호작용 안전성 확인 (질문에서 약 이름 추출 → 각 약 전체 정보 취합 → "명시 안 됨 = 안전"으로 오판하지 않도록 강제, 단일약/두약 언급 시나리오 실제 검증 완료 — 로드맵 Phase 2)
- [x] 특수 대상자(임산부/소아/고령자) 필터링 (질문에서 대상자 감지 → specific은 정확한 약 재검색, symptom은 금기 후보 제외/안전 후보 없으면 추천 보류, 실제 시나리오 검증 완료 — 로드맵 Phase 4)
- [x] STEP 6: FastAPI 서버 (`/chat`, `/health` 작성 완료, 실제 서버 기동 후 curl로 검증 완료)
- [x] STEP 7: 테스트 (`tests/test_cleaning.py`, `tests/test_chunking.py` 유닛 테스트 9건 통과 / `tests/check_rag_quality.py` 셀프 체크 통과 — 안전 문구 규칙 자동 검증 + 어투는 수동 확인용)
- [x] `/chat` 요청량 제한 (IP당 15회/분·200회/일, `slowapi`, `src/api/limiter.py`)
- [x] 약사 상담 + 자판기 QR 연동 프로토타입 (챗봇 상담→화상상담→약사 승인→QR 로그인→수령까지의 흐름을 최소 기능으로 구현, `src/consult/`. 유닛 테스트 21건 + 실제 서버 end-to-end 검증 완료 — 규제샌드박스 신청용 데모 목적)
- [x] 지도 백엔드 (`/api/v1/map/machines`, `/api/v1/map/machines/{id}`) — 한별의 Supabase(PostGIS) 읽기 전용 조회를 통합하고, Supabase 미설정 시 메인 DB의 시연용 가상 자판기(M001~M005, `DEMO_MAP_CENTER` 주변)로 동작. 내 위치 기준 거리 정렬 + 자판기별 재고. 상세는 `docs/map-api.md` + 앱 지도 화면 `/app/map-screen/`(카카오 지도, `KAKAO_JS_KEY` 필요) — 가상 자판기는 동양미래대학교 주변 실제 장소, Supabase용 시드 SQL `scripts/supabase_demo_machines.sql` (2026-10-07 한별 동의 후 실행 완료: VM-001 좌표 보정, VM-006~009 추가, 재고 15건 — Render도 `MAP_DATABASE_URL`로 Supabase 조회 중)
- [x] 상담 결과 실시간 반영 (챗봇/상담 페이지가 `GET /consultations/{id}`를 3초 간격 폴링 → 승인 시 처방 약/QR 안내, 거절 시 사유 자동 표시), 이미 대기 중인 상담이 있으면 재사용해 중복 생성 방지 (`GET /consultations?status=pending&user_id=`)

## 8. 코딩 시 참고사항

- API 키·민감정보는 반드시 `.env`로 관리하고 `python-dotenv`로 로드한다.
- `data/` 폴더의 원본/정제 데이터는 git에 커밋하지 않는다 (`.gitignore` 처리).
- 노트북(`notebooks/`)에서 실험한 뒤, 확정된 로직만 `src/`의 함수로 옮긴다.
- 팀 전체 통신 규격은 JSON이며, 프론트-백엔드 API 스펙이 바뀌면 팀에 공유한다.
- 새 기능을 만들 때는 항상 이 문서의 "5. 개발 파이프라인" 순서와 "6. 안전/품질 요구사항"을 먼저 확인한다.

## 9. 약사 상담 + 자판기 QR 연동 (프로토타입, `src/consult/`)

**배경**: 자판기에서 의약품을 파는 건 약사법상 엄격히 규제되는 영역이라, "AI가 직접 판매를 승인하지 않고 반드시 약사가 최종 승인한다"는 흐름을 실제로 작동하는 코드로 보여주는 게 목적이다(규제샌드박스 신청 근거 자료용). 실제 결제/실제 판매 기능은 아직 없다.

**흐름**: 챗봇 상담(`/chat`) → (사용자는 챗봇 화면의 "약사와 상담하기" 버튼만 누름, 약품 코드를 몰라도 됨) → **실제 화상 상담방(Daily.co) 생성 및 연결** → **약사가 대화 내용을 보고 처방할 약을 직접 정해서 승인/거절** → 승인 시 "승인된 구매 건" 생성(유효시간 있음) → 사용자가 자판기 QR을 앱으로 스캔 → 대기 중인 승인 건이 있으면 수령 확정 → 자판기 개방(모의).

- `POST /consultations` — 상담 요청 생성. 요청 바디는 `user_id`만 필수이고 `chat_summary`는 선택이다(`requested_drug_*`도 선택) — 약품 지정은 사용자가 아니라 약사가 승인 시점에 한다. `chat_summary`를 안 보내면 서버가 `DIRECT_REQUEST_SUMMARY`(고정 문구: "직접 상담 요청 (사전 챗봇 대화 없음 — 화상으로 바로 문진 필요)")로 채운다. 생성 시 `src/consult/video.py`가 Daily.co REST API로 화상 상담방을 만들고 `room_url`을 응답에 포함한다(생성 실패해도 상담 요청 자체는 계속 진행 — 화상은 부가 기능).
  - **진입점 두 곳**이 같은 API를 쓴다(회의 결과: 챗봇을 강제로 거치게 하지 않기 위해 분리):
    - `GET /chat-test` (`chat_test.html`) "🩺 약사와 상담하기" 버튼 — 지금까지의 챗봇 대화(`history`)를 통째로 `chat_summary`로 만들어 전송
    - `GET /consult` (`consult_direct.html`) — 네비게이션바/메뉴에서 바로 진입하는 용도. 텍스트 입력 없이 버튼 하나로 즉시 상담 요청(= `chat_summary` 생략) → 바로 화상 화면으로 전환
    - 앱(`/app/...`) 모든 화면 하단 가운데 "상담하기" 버튼(🩺) — 챗봇 대화 없이 `/app/pharmacist-waiting/?direct=1`로 이동해 `chat_summary` 없이 상담 생성(또는 대기 중 상담 재사용) 후 바로 화상 입장 화면(`/app/video-consult-entry/`)으로 전환. 챗봇 대화 중 나오는 "약사에게 바로 상담하기" 버튼은 기존처럼 대화 내용을 함께 전달
  - 두 진입점 다 응답의 `room_url`을 `<iframe>`으로 바로 띄운다(노트북/폰 카메라 권한 요청됨). 사용자 식별은 아직 실제 인증이 없어 브라우저 `localStorage`에 저장한 임시 ID를 쓴다(`moyak_user_id`, 두 페이지가 같은 키를 써서 동일 브라우저면 ID가 이어진다).
  - **상담 생성 전에 항상 `GET /consultations?status=pending&user_id=`로 먼저 조회**해서, 이미 대기 중인 상담이 있으면 새로 만들지 않고 그걸 재사용한다(중복 상담 방지 — 사용자가 버튼을 여러 번 누르거나 페이지를 새로고침해도 상담이 중복 생성되지 않음).
  - 상담 생성/재사용 이후 두 페이지 모두 **3초 간격으로 `GET /consultations/{id}`를 폴링**해서 상태 변화를 실시간 반영한다: `approved`가 되면 처방된 약 이름(`approved_drug_name`)과 "자판기에서 QR 스캔" 안내 배너를, `rejected`가 되면 약사가 입력한 `decision_reason`(없으면 기본 안내 문구)을 화면에 표시하고 폴링을 멈춘다.
  - 약사 쪽 진입점: `/consult-demo`의 대기 목록에 "🎥 화상 상담 입장" 링크로 같은 `room_url`을 연다 — 사용자와 약사가 **같은 방**에 들어와야 화상이 연결된다. `chat_summary`가 기본 문구 그대로면 "챗봇 대화 없이 바로 온 상담"임을 약사가 바로 알 수 있다.
- `GET /consultations?status=pending&user_id=` — 대기 목록 (약사 대시보드용으로는 `status`만, 특정 사용자의 중복 상담 확인용으로는 `user_id`도 같이 필터링)
- `GET /consultations/{id}` — 단건 조회. 챗봇/상담 페이지가 자신이 만든 상담의 상태를 폴링할 때 쓴다. 없는 id면 404.
- `POST /consultations/{id}/decision` — 약사 승인/거절. **약사가 `drug_item_seq`/`drug_item_name`을 직접 입력해 "처방"하며(요청에 없었어도 됨)**, 승인 시에만 `ApprovedPurchase` 생성(기본 60분 유효). 응답에 `approved_purchase_id`/`approved_drug_name`을 포함해 무엇이 승인됐는지 바로 확인 가능.
  - `price`(원, 선택, 0 이상): 약사가 승인 시 안내하는 판매가(e약은요엔 가격 데이터가 없어 약사가 입력). 약사 콘솔(`/pharmacist`)에서는 필수 입력. 가격이 있는 승인 건은 키오스크에서 **결제해야 수령 가능**하고, 가격이 없는 건(가격 도입 전/데모 페이지 승인)은 기존처럼 결제 없이 수령.
- **화상 상담 중 채팅 + 종료 요약**:
  - `GET /consultations/{id}/messages` / `POST /consultations/{id}/messages` `{"sender_role": "user"|"pharmacist", "sender_id", "content"}` — 사용자 앱 화상 화면(`/app/video-consult-entry/`)의 "💬 채팅"과 약사 콘솔(`/pharmacist`) 카드의 채팅창이 3초 간격 폴링으로 주고받는다. 사용자는 본인 상담에만 보낼 수 있고(`sender_id == user_id`), 종료된 상담에는 보낼 수 없다(409). IP당 60회/분 제한.
  - `POST /consultations/{id}/end` — 사용자/약사 누구든 "상담 종료"를 누르면 GPT-4o-mini(`src/consult/summary.py`)가 [상담 전 챗봇 대화 + 상담 중 채팅 + 처방 결과]를 요약해 `summary`/`ended_at`에 저장(한 번만 생성, 재호출 시 기존 요약 반환). 요약할 대화가 없으면 LLM을 부르지 않고 고정 문구를 반환. 실제 비용이 드는 호출이라 IP당 10회/분·100회/일 제한. 응답(`ConsultationResponse`)에 `summary`/`ended_at` 필드 추가.
  - 요약 프롬프트는 대화에 없는 약 정보(복용량 등)를 새로 만들지 못하게 강제한다. **화상 통화의 음성 내용은 서버가 받지 않아 요약에 포함되지 않는다**(요약 끝에 이 안내 문구가 붙음) — 음성까지 요약하려면 Daily.co 녹음/전사(유료, 별도 설정) 연동이 필요.
- `POST /vending/purchases/pay` `{"machine_id", "purchase_ids": [...]}` — 키오스크 승인 약(cart2) 결제(모의). 자판기에 QR 로그인한 본인의 대기 중 승인 건만 결제 가능하며, 결제 완료 시 `paid_at` 기록 + **자판기 로그인 자동 해제**(welcome 화면 안내 문구와 일치). 응답 `{"total_amount", "purchases"}`. 승인 건 응답(`PurchaseResponse`)에 `price`/`paid_at` 필드 추가.
- `POST /vending/machines/{machine_id}/rotate-qr` — 자판기가 주기적으로 새 QR 토큰 발급(기본 60초 유효) → 화면에 QR로 표시
- `POST /vending/scan` — 앱이 QR 스캔 결과 전송 → 그 사용자의 대기 중인 승인 건 확인
- `POST /vending/dispense` — 수령 확정 → 자판기 개방(모의) + 기록
- `GET /consult-demo` — 세 패널(사용자 앱/약사 대시보드/자판기 화면)로 위 흐름을 브라우저에서 눈으로 확인할 수 있는 데모 페이지. 실제 QR 이미지도 렌더링됨(`qrcode` CDN 라이브러리).

**중요한 설계 원칙**: AI(챗봇)는 이 흐름 어디에도 "승인" 권한이 없고, 사용자도 약품을 직접 지정하지 않는다. 어떤 약을 줄지는 항상 약사가 상담 내용을 보고 승인 시점에 정하며(마치 처방하듯), `pharmacist_id`가 명시적으로 호출하는 `/consultations/{id}/decision`에서만 이 결정이 발생한다. 모든 승인/수령 기록이 DB에 남는다(감사 로그 역할).

**아직 스텁인 부분** (실제 서비스 전 반드시 교체 필요):
- **사용자/약사 인증 없음**: `user_id`/`pharmacist_id`를 요청 바디로 그대로 받는다. 실제 인증 시스템이 붙으면 그 값으로 교체.
- **화상상담**: Daily.co 프리빌트 iframe으로 연결됨(`DAILY_API_KEY`/`DAILY_SUBDOMAIN` 필요). 다만 자체 디자인의 커스텀 통화 UI는 아니고 Daily 기본 UI 그대로 노출됨 — Figma 디자인과 통일하려면 Daily의 JS/Flutter SDK로 직접 UI를 짜야 함.
- **자판기 하드웨어 없음**: MQTT로 실제 자판기에 개방 신호를 보내는 부분은 아직 없고, `/vending/dispense`가 성공 응답만 준다.
- **DB는 SQLite** (`data/moyak.db`, `.env`의 `DATABASE_URL`로 교체 가능). **Render 무료 인스턴스는 디스크가 휘발성이라 재배포/재시작 시 초기화된다** — 실제 서비스로 갈 땐 Postgres 같은 영속 DB로 바꿔야 한다.
