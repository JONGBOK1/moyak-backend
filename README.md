# MOYAK 백엔드 — RAG 챗봇 '모약이'

MOYAK은 약 자판기와 연동되는 앱입니다. **'모약이'** 는 그중 의약품 정보를 안내하는 RAG(검색 증강 생성) 기반 챗봇으로, 식품의약품안전처의 공공데이터인 **e약은요**를 근거로만 답변합니다.

일반 LLM은 근거 없이 약 정보를 지어낼 위험(할루시네이션)이 있습니다. 모약이는 검색된 공식 자료 안에서만 답변하고, 답변마다 참고한 약품명과 원문 근거를 함께 제공해 **신뢰성과 출처**를 최우선으로 합니다. 주 사용자층이 노년층인 만큼 답변은 쉽고 명확하게 구성됩니다.

현재는 사진(약 봉투/알약) 기반 멀티모달 질문 이전 단계로, **텍스트 기반 RAG 파이프라인**이 완성된 상태입니다.

## 주요 특징

- **근거 기반 답변**: 검색된 자료에 없는 내용은 답하지 않고, 자료가 없으면 약사 상담을 권유합니다.
- **원문 근거 노출**: 답변이 AI의 요약일 뿐 아니라, 실제로 인용한 식약처 원문 텍스트를 그대로 확인할 수 있습니다.
- **위험 질문 가드레일**: 복용량·병용금기 등 위험할 수 있는 질문에는 약사 상담 권유 문구가 자동으로 붙습니다.
- **모호한 질문 처리**: 약품명 없이 질문하면 추측해서 답하지 않고 되묻습니다.
- **대화 맥락 유지**: "그거 복용법은?" 같은 후속 질문도 이전 대화를 참고해 정확히 검색합니다.
- **증상 기반 추천**: "배 아플 때 뭐 먹어?"처럼 증상만 말해도, 효능 데이터에서 후보를 찾아 추천 이유·복용법·주의사항을 함께 안내합니다.
- **병용 안전성 확인**: "타이레놀이랑 감기약 같이 먹어도 돼?"처럼 물으면 각 약의 상호작용/주의사항을 확인해 안내합니다. 자료에 명시된 내용이 없으면 "안전하다"고 단정하지 않고 약사 상담을 권유합니다.
- **특수 대상자 안전 필터**: 임산부/소아/고령자가 언급되면 해당 대상자에게 금기인 약은 추천 후보에서 제외하고, 안전한 후보가 없으면 억지로 추천하지 않습니다.

## 기술 스택

| 영역 | 기술 |
|---|---|
| 데이터 소스 | 식약처 e약은요 공공데이터 API |
| 전처리 | Python + Pandas |
| 임베딩 | OpenAI `text-embedding-3-small` |
| 벡터 DB | Pinecone (serverless, dimension 1536, cosine) |
| 체인 프레임워크 | LangChain |
| LLM | GPT-4o (temperature 0.2) |
| API 서버 | FastAPI |
| 버전 관리 | Git + GitHub |

## 데이터 파이프라인

```
STEP 0 수집 → STEP 1 정제 → STEP 2 청킹 → STEP 3 임베딩 → STEP 4 인덱싱(Pinecone)
                                                                    │
                                        STEP 6 API 서버 ← STEP 5 RAG 체인
                                                │
                                          STEP 7 테스트
```

| STEP | 내용 | 결과 |
|---|---|---|
| 0 | e약은요 API 전체 수집 | 4,774건 |
| 1 | HTML 제거, 중복/결측 처리 | 4,757건 |
| 2 | 필드별(효능/사용법/경고/주의사항/상호작용/부작용/보관법) 청킹 | 27,956개 청크 |
| 3 | OpenAI 임베딩 | 27,956개 벡터 |
| 4 | Pinecone upsert | 인덱스 `moyak-eyakeunyo` |
| 5 | 검색(top_k=5) + GPT-4o 생성 | `src/rag/chain.py` |
| 6 | FastAPI `/chat` 서버 | `src/api/main.py` |
| 7 | 유닛 테스트 + 품질 셀프 체크 | `tests/` |

## 시작하기

### 1. 환경 준비

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

`.env.example`을 복사해 `.env`를 만들고 키를 채워주세요.

```
EYAK_SERVICE_KEY=   # 식약처 e약은요 공공데이터 API 키
OPENAI_API_KEY=     # OpenAI API 키 (임베딩, GPT-4o)
PINECONE_API_KEY=   # Pinecone API 키
```

### 2. 데이터 파이프라인 실행 (최초 1회)

```bash
python src/ingestion/fetch_eyakeunyo.py   # STEP 0: 데이터 수집
python src/ingestion/clean.py             # STEP 1: 정제
python src/indexing/chunking.py           # STEP 2: 청킹
python src/indexing/embedding.py          # STEP 3: 임베딩 (비용 발생, 확인 프롬프트 있음)
python src/indexing/pinecone_index.py     # STEP 4: 인덱싱
```

### 3. API 서버 실행

```bash
python -m uvicorn src.api.main:app --reload
```

- 웹 테스트 페이지: `http://127.0.0.1:8000/`
- Swagger 문서: `http://127.0.0.1:8000/docs`

### 4. 테스트

```bash
pytest                              # 정제/청킹 유닛 테스트 (API 호출 없음)
python tests/check_rag_quality.py   # RAG 답변 품질 셀프 체크 (실제 API 호출, 소액 비용)
```

## API

**`POST /chat`**

서버는 대화를 저장하지 않는 무상태(stateless) 방식입니다. 후속 질문("부작용은?" 등)이 이어질 수 있도록, 클라이언트가 이전 대화를 `history`에 담아 매 요청마다 함께 보내주세요.

```json
// 요청
{
  "question": "부작용은?",
  "history": [
    { "role": "user", "content": "활명수 효능이 뭐야?" },
    { "role": "assistant", "content": "활명수는 식욕감퇴... [참고: 활명수]" }
  ]
}

// 응답
{
  "answer": "활명수는 식욕감퇴(식욕부진), 위부팽만감, 소화불량... [참고: 활명수]",
  "sources": ["활명수"],
  "evidence": [
    { "item_name": "활명수", "field": "efficacy", "field_label": "효능", "text": "..." }
  ]
}
```

**`GET /health`** → `{"status": "ok"}`

**요청량 제한**: `/chat`은 IP당 분당 15회 · 일일 200회로 제한됩니다. 초과 시 `429`와 함께 안내 메시지가 반환됩니다.

## 폴더 구조

```
moyak-backend/
├── data/                 # raw(원본)/processed(정제) — git 미포함
├── src/
│   ├── config.py         # 환경변수 로드
│   ├── ingestion/        # STEP 0~1: 수집, 정제
│   ├── indexing/         # STEP 2~4: 청킹, 임베딩, 인덱싱
│   ├── rag/              # STEP 5: 프롬프트, RAG 체인
│   └── api/               # STEP 6: FastAPI 서버, 테스트 웹 UI
├── tests/                # STEP 7: 유닛 테스트, 품질 셀프 체크
└── requirements.txt
```

## 로드맵

1. ✅ 단일 약품 정보 조회
2. ✅ 병용/상호작용 안전성 확인
3. ✅ 증상 기반 약 추천 (추천 이유 + 복용법 + 주의사항)
4. ✅ 특수 대상자(임산부/소아/노인) 복용 가능 여부 필터링
5. 자판기 재고/위치 연동
6. 사진 기반(멀티모달) 약 식별

---

개발 배경과 상세 설계 원칙은 [`CLAUDE.md`](./CLAUDE.md)를 참고하세요.