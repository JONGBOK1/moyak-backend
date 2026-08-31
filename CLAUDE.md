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
   - 답변에서 실제로 인용된 약품명만 출처/근거로 남기는 `_extract_cited`는 LLM이 긴 제품명의 띄어쓰기를 살짝 바꿔 쓰는 경우가 있어 공백 제거 후 비교한다.
7. **STEP 6 - API 서버**: FastAPI `/chat` 엔드포인트. 프론트(Flutter)와 JSON 스펙 맞추기
   - 요청: `POST /chat` `{"question": "string", "history": [{"role": "user"|"assistant", "content": "string"}, ...]}`
     - `history`: 이전 대화 턴(선택, 기본값 빈 배열). 서버는 세션을 저장하지 않는 완전 무상태(stateless) 방식이라, 프론트가 대화 기록을 들고 있다가 매 요청마다 함께 보낸다.
     - 후속 질문(예: "부작용은?")은 검색 전에 GPT-4o-mini로 독립형 질문("활명수의 부작용은?")으로 재작성한 뒤 검색한다(`rewrite_standalone_question`). 이 재작성은 검색에만 쓰이고, 최종 답변 생성에는 원래 질문 + history 전체가 그대로 전달된다.
   - 응답: `{"answer": "string", "sources": ["string", ...], "evidence": [{"item_name", "field", "field_label", "text"}, ...]}`
     - `evidence`: 답변이 실제로 인용한 원본 청크 목록(신뢰도 어필용 — "AI 요약"이 아니라 식약처 원문 그대로임을 사용자에게 보여주기 위해 추가)
   - 헬스체크: `GET /health` → `{"status": "ok"}`
   - 테스트용 페이지: `GET /` → 한국어 웹 UI (`src/api/static/chat_test.html`), 답변/출처/원문 근거를 브라우저에서 바로 확인 가능
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

## 8. 코딩 시 참고사항

- API 키·민감정보는 반드시 `.env`로 관리하고 `python-dotenv`로 로드한다.
- `data/` 폴더의 원본/정제 데이터는 git에 커밋하지 않는다 (`.gitignore` 처리).
- 노트북(`notebooks/`)에서 실험한 뒤, 확정된 로직만 `src/`의 함수로 옮긴다.
- 팀 전체 통신 규격은 JSON이며, 프론트-백엔드 API 스펙이 바뀌면 팀에 공유한다.
- 새 기능을 만들 때는 항상 이 문서의 "5. 개발 파이프라인" 순서와 "6. 안전/품질 요구사항"을 먼저 확인한다.
