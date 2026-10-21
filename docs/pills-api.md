# 알약 사진 인식 API

알약 사진으로 어떤 약인지 후보를 찾는다. (작성: 선우 — DB·알약 인식 담당, 2026-10-21 개정)

로그인은 필요 없다. 약을 찾아보는 것 자체는 개인정보와 무관하고, 자판기 앞에서 바로 쓸 수 있어야 한다.

## 한눈에

```
사진 1장 (또는 앞면+뒷면 2장)
  ├─ ① Claude(claude-opus-5-5)가 원래 사진 + 가운데 확대 사진을 보고
  │     모양 · 색 · 각인 · 로고 여부 · 서로 다른 약 종류 수 를 판정
  │     (안 되면 예전 방식: OpenCV 모양·색 + Google Vision 각인)
  ├─ ② Supabase drug_pills(25,350개, 읽기 전용)에서 점수순 후보
  └─ ③ 1위가 확실하지 않으면 상위 10개 후보의 식약처 사진을 한 장으로 모아
        Claude 에게 보여주고 다시 고르게 함 (재순위)
```

코드: `src/pills/identify.py`(전 과정) · `vision_llm.py`/`vision_claude.py`(①) · `search.py`(②) · `rerank.py`(③).
예전 방식은 `vision.py`(OpenCV) · `ocr.py`(Google Vision) 그대로다.

## 정확도

| 시험 사진 | 결과 |
|---|---|
| 식약처 사진 119장 (일반의약품만) | **1위 93.3% · 3위 안 93.3%** (예전 방식 1위 64.7%) |
| 실사진 14장 (인터넷·직접 촬영, 전체 의약품) | **3위 안 71.4%** |
| 실사진, 앞뒤 두 장을 올린 경우 (약 기준) | **10개 중 9개 3위 안** |
| 폰 사진 흉내 59장 (식약처 알약을 손바닥·책상 배경에 합성) | 3위 안 69.5% |

**1위를 정답으로 단정하지 말 것.** 사진 한 장만으로는 정보가 부족한 약이 있다(앞면이 로고뿐, 각인이
'50' 처럼 흔함, 용량만 다른 같은 약). 그래서 후보를 보여주고 사용자가 고르게 한다.

## `POST /pills/identify`

`multipart/form-data`. **5~15초** 걸린다(Claude 판정 + 필요하면 재순위) — 화면에 로딩 표시를 할 것.

| 필드 | | |
|---|---|---|
| `file` | 필수 | 알약 사진 (JPEG/PNG, 8MB 이하) |
| `back` | 선택 | **같은 알약의 뒷면** 사진. 앞면이 로고뿐인 약은 뒷면이 있어야 찾는다 |

쿼리
- `otc_only` (기본 `true`) — 일반의약품만. 자판기 화면은 `true`, **챗봇은 `false` 권장**(집에 있는 처방약을 묻는 경우가 많다)
- `limit` (기본 10, 최대 50) — 챗봇 카드는 3개면 충분하다

응답
```json
{
  "detected": {
    "shape": "장방형", "color": "노랑", "imprint": "WAL 250",
    "aspect": 1.9, "confidence": 0.8,
    "engine": "claude-opus-5-5",
    "reranked": true
  },
  "candidates": [
    {
      "item_seq": "200402091", "item_name": "클로틴정(클래리트로마이신)",
      "entp_name": "오스틴제약(주)", "drug_shape": "장방형", "color_class1": "노랑",
      "print_front": "WAL250", "print_back": null,
      "item_image": "https://nedrug.mfds.go.kr/...", "etc_otc_name": "전문의약품",
      "score": 0.62, "matched": ["모양", "색", "각인 전부 일치", "사진 비교"]
    }
  ],
  "confident": false,
  "message": "사진과 비슷한 약을 찾았어요. 사진만으로는 확실하지 않으니, 맞는 약을 눌러 주세요.",
  "note": null
}
```

- `message` — 챗봇 말풍선에 그대로 띄울 한 줄.
- `confident` — 1위가 각인까지 통째로 맞고 2위와 점수가 벌어졌을 때만 `true`. 이때 `message` 가
  "사진 속 약은 OOO로 보여요" 가 된다. `false` 면 반드시 사용자가 고르게 한다.
- `note` — 주의할 점(로고만 있는 면 → "뒷면도 같이 올리면…", 서로 다른 약이 섞임, 포장지를 찍음 등). 있으면 그대로 보여준다.
- `detected.engine` — `claude-opus-5-5` 면 Claude 가 판정, `opencv+google` 이면 예비 방식(키 없음·크레딧 소진·하루 상한 초과 등).
- `matched` — 무엇이 맞아서 올라온 후보인지(모양 / 색 / 각인 일치 / 각인 전부 일치 / 로고 / 사진 비교 …). 뱃지로 쓰기 좋다.
- `score` 는 확률이 아니라 "근거가 얼마나 맞았나" 를 0~1 로 나타낸 값이다.

오류
| 코드 | 언제 |
|---|---|
| 400 | 빈 파일 / 이미지가 아님 |
| 413 | 8MB 초과 |
| 429 | 요청량 초과 — IP당 10회/분 · 100회/일 |
| 503 | DB 주소 미설정, OpenCV 미설치, DB 조회 실패 |

## 챗봇에 붙이는 법 (앱 챗봇 화면에 이미 붙여 둠)

`src/api/static/app/chatbot/index.html` 의 입력창 왼쪽 **"+" 버튼**이 이 흐름이다. Flutter 로 옮길 때도 같은 순서로 하면 된다.

```
사용자: [+] → 사진 1~2장 선택
  → POST /pills/identify?otc_only=false&limit=3   (file, back)
  → 말풍선: message + 후보 카드 3개(item_image · item_name · 각인)
사용자: 카드 하나 누름 ("이 약 맞아요")
  → POST /chat {"question": "클로틴정(클래리트로마이신)은(는) 어떤 약이야? 효능이랑 주의사항 알려줘", "history": [...]}
  → 이후 "부작용은?" 같은 후속 질문은 평소 챗봇 대화 그대로
```

```js
const fd = new FormData();
fd.append("file", front);            // 필수
if (back) fd.append("back", back);   // 선택
const res = await fetch("/pills/identify?otc_only=false&limit=3", { method: "POST", body: fd });
const data = await res.json();        // data.message, data.candidates, data.confident, data.note
```

**알약 인식은 "약을 특정하는 단계"까지만 한다.** 설명·병용금기·추천은 기존 `/chat`(식약처 자료 기반 RAG)이 그대로 한다.
그래서 사진만 보고 약 설명을 지어내는 일이 없다.

## 키와 설정 — 앱에는 키가 없다

알약 인식은 챗봇과 **같은 서버** 안의 API 라서, 앱(프론트)은 주소만 부르면 된다. **앱에 넣을 키는 없다.**
키는 서버 환경변수(Render 대시보드 → Environment)에만 넣는다. git·채팅·앱 코드에 넣지 않는다.

```
ANTHROPIC_API_KEY=       # Claude. 없으면 예전 방식(OpenCV + Google)으로 자동 동작
GOOGLE_VISION_API_KEY=   # 예비 방식의 각인 읽기. 비우면 예비 방식은 모양·색만
PILL_DATABASE_URL=       # 비우면 DRUG_DATABASE_URL → MAP_DATABASE_URL 순으로 사용
# 선택
PILL_LLM_MODEL=claude-opus-5-5   # 비워 두면 LLM 을 끈다
PILL_RERANK=1                    # 0 이면 재순위를 끈다
PILL_LLM_DAILY_LIMIT=300         # 서버 전체 하루 최대 사진 수(비용 상한)
```

## 비용과 남용 방지

- 사진 1장당 평균 **약 2센트**(판정 1.4센트 + 재순위가 필요할 때 1.2센트). 월 500장이면 약 10달러.
- IP당 10회/분 · 100회/일 + **서버 전체 하루 상한**(`PILL_LLM_DAILY_LIMIT`, 기본 300장 ≈ 6달러).
  상한을 넘으면 실패시키지 않고 그날 남은 요청을 예비 방식으로 처리한다. 상한은 메모리에만 세므로 서버가 재시작되면 0부터 센다.
- Anthropic 크레딧이 떨어지면 서버 로그에 `Claude 응답 400 invalid_request_error: Your credit balance is too low…` 가
  찍히고 예비 방식으로 동작한다.

## 테스트 페이지

`GET /pill-test` — 사진을 두 장까지 끌어다 놓고 판정·후보·재순위 여부를 바로 본다. 정답이 전문의약품이면 "일반의약품만" 을 끌 것.

## 배포 전에 확인할 것

- Render 에 `ANTHROPIC_API_KEY` 를 넣었는지 (`detected.engine` 이 `claude-opus-5-5` 로 나오는지).
- 재순위는 서버가 식약처 사진(`nedrug.mfds.go.kr`)을 받아 와야 한다. 해외 서버에서 그 사이트가 막히면
  재순위만 조용히 건너뛴다(판정·검색은 그대로). 로그에 "후보 사진 못 받음" 이 찍히면 이 경우다.
- `sql/015_fuzzystrmatch.sql` 이 Supabase 에 적용돼 있는지 — 각인 한 글자 오독 보정(`levenshtein()`)에 필요하다.

## 측정 도구

고치기 전후를 숫자로 비교할 때 쓴다. 추측으로 고치지 말 것.

```
python scripts/check_pill_search.py --ocr llm --model claude-opus-5-5   # 식약처 119장, 판정+검색
python scripts/check_pill_search.py --ocr google                        # 예비 방식
```

실사진·합성 세트 채점과 엔진 비교 스크립트는 개인 저장소(`moyak`)의 `scripts/check_pill_real.py`,
`make_synth_photos.py`, `compare_pill_engines.py` 에 있다(사진 파일은 출처 문제로 저장소에 없다).
자세한 경과는 `docs/pill_recognition_status.md`.

## 고칠 때 주의

- 각인 비교 규칙을 건드리면 `tests/test_pill_search.py`, `tests/test_pills.py` 부터 돌릴 것. 거기 있는 건 전부 실제로 틀렸던 것들이다.
- 점수 배점(`search.py` 맨 위 `W_*`)은 손으로 고치지 않는다. 바꿀 때마다 식약처 119장·500장으로 전후를 쟀다(값 옆 주석).
- 질문(프롬프트)을 고치면 캐시가 무효가 되어 다시 재야 한다. 글자 회전 지침을 넣어 봤다가 합성 세트가 64.4 → 61.0% 로 떨어져 뺐다.
- 예비 방식의 주의사항(색은 측정값 거리로만, OCR 전처리는 CLAHE 가 최선, easyocr 은 영어로만)은 `vision.py`·`ocr.py` 주석에 있다.
