"""STEP 5: 검색(Pinecone) + 생성(GPT-4o) RAG 체인

세 가지 질문 유형을 분기해서 처리한다.
- specific: 특정 약에 대한 질문 -> 전체 필드 혼합 top_k 검색
- symptom: 증상 기반 추천 질문 -> 효능 필드로 후보 약을 먼저 찾고, 후보별 전체 정보를 모아 추천
- interaction: 병용 안전성 질문 -> 언급된 약 이름을 추출해 각각 전체 정보를 모아 상호작용/주의사항/경고를 비교
"""

import sys
from pathlib import Path

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src import config
from src.rag.prompts import (
    DRUG_EXTRACTION_PROMPT,
    FIELD_LABELS,
    INTENT_SYSTEM_PROMPT,
    INTERACTION_SYSTEM_PROMPT,
    RECOMMEND_SYSTEM_PROMPT,
    REWRITE_SYSTEM_PROMPT,
    SYMPTOM_QUERY_PROMPT,
    SYSTEM_PROMPT,
    build_context,
    build_grouped_context,
    build_interaction_user_prompt,
    build_recommend_user_prompt,
    build_rewrite_prompt,
    build_user_prompt,
)

INDEX_NAME = "moyak-eyakeunyo"
EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o"
REWRITE_MODEL = "gpt-4o-mini"
TOP_K = 5
TEMPERATURE = 0.2

EFFICACY_CANDIDATES_K = 8
MAX_RECOMMEND_CANDIDATES = 3
PER_DRUG_FIELD_K = 10
MAX_INTERACTION_DRUGS = 3


def get_vector_store() -> PineconeVectorStore:
    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL, openai_api_key=config.OPENAI_API_KEY)
    return PineconeVectorStore(
        index_name=INDEX_NAME,
        embedding=embeddings,
        pinecone_api_key=config.PINECONE_API_KEY,
    )


def get_llm():
    return ChatOpenAI(model_name=CHAT_MODEL, openai_api_key=config.OPENAI_API_KEY, temperature=TEMPERATURE)


def get_rewrite_llm():
    return ChatOpenAI(model_name=REWRITE_MODEL, openai_api_key=config.OPENAI_API_KEY, temperature=0)


def rewrite_standalone_question(question: str, history: list[dict], rewrite_llm) -> str:
    """대화 기록을 참고해 후속 질문(대명사/생략 주어 포함)을 검색용 독립 질문으로 바꾼다."""
    if not history:
        return question
    messages = [
        {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
        {"role": "user", "content": build_rewrite_prompt(question, history)},
    ]
    response = rewrite_llm.invoke(messages)
    rewritten = response.content.strip()
    return rewritten or question


def classify_intent(question: str, rewrite_llm) -> str:
    """질문을 'symptom'(증상 추천) / 'interaction'(병용 안전성) / 'specific'(특정 약 질문)으로 분류한다."""
    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    response = rewrite_llm.invoke(messages)
    label = response.content.strip().lower()
    if "symptom" in label:
        return "symptom"
    if "interaction" in label:
        return "interaction"
    return "specific"


def _extract_cited(docs, answer: str) -> tuple[list[str], list[dict]]:
    """LLM이 답변에서 실제로 언급/인용한 청크만 출처/근거로 남긴다.

    그렇지 않으면 "확인되지 않습니다" 류 답변에도 무관한 약품명/원문이 붙는 문제가 생긴다.
    LLM이 긴 제품명을 인용할 때 띄어쓰기를 살짝 바꿔 쓰는 경우가 있어(예: "타이레놀정500밀리그람" ->
    "타이레놀정 500밀리그람"), 공백을 제거하고 비교한다.
    """
    answer_no_space = answer.replace(" ", "")
    cited_docs = [
        doc
        for doc in docs
        if doc.metadata.get("item_name") and doc.metadata["item_name"].replace(" ", "") in answer_no_space
    ]
    sources = sorted({doc.metadata["item_name"] for doc in cited_docs})
    evidence = [
        {
            "item_name": doc.metadata.get("item_name"),
            "field": doc.metadata.get("field"),
            "field_label": FIELD_LABELS.get(doc.metadata.get("field"), doc.metadata.get("field")),
            "text": doc.page_content,
        }
        for doc in cited_docs
    ]
    return sources, evidence


def _ask_specific(question: str, search_query: str, history: list[dict], vector_store, llm) -> dict:
    docs = vector_store.similarity_search(search_query, k=TOP_K)
    context = build_context(docs)
    user_prompt = build_user_prompt(question, context)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend({"role": h["role"], "content": h["content"]} for h in history)
    messages.append({"role": "user", "content": user_prompt})

    answer = llm.invoke(messages).content
    sources, evidence = _extract_cited(docs, answer)
    return {"answer": answer, "sources": sources, "evidence": evidence}


def build_symptom_query(question: str, rewrite_llm) -> str:
    """구어체 증상 질문을 효능 문구 매칭에 유리한 임상 키워드로 바꾼다."""
    messages = [
        {"role": "system", "content": SYMPTOM_QUERY_PROMPT},
        {"role": "user", "content": question},
    ]
    response = rewrite_llm.invoke(messages)
    keywords = response.content.strip()
    return keywords or question


def _get_candidate_item_seqs(search_query: str, vector_store) -> list[str]:
    docs = vector_store.similarity_search(search_query, k=EFFICACY_CANDIDATES_K, filter={"field": "efficacy"})
    seqs = []
    for doc in docs:
        seq = doc.metadata.get("item_seq")
        if seq and seq not in seqs:
            seqs.append(seq)
        if len(seqs) >= MAX_RECOMMEND_CANDIDATES:
            break
    return seqs


def _ask_symptom(question: str, search_query: str, history: list[dict], vector_store, llm, rewrite_llm) -> dict:
    efficacy_query = build_symptom_query(search_query, rewrite_llm)
    candidate_seqs = _get_candidate_item_seqs(efficacy_query, vector_store)
    if not candidate_seqs:
        return {
            "answer": "제공된 자료에서 확인되지 않습니다. 약사와 상담하시는 것을 권장드립니다.",
            "sources": [],
            "evidence": [],
        }

    docs = []
    for seq in candidate_seqs:
        docs.extend(vector_store.similarity_search(efficacy_query, k=PER_DRUG_FIELD_K, filter={"item_seq": seq}))

    context = build_grouped_context(docs, block_label="후보")
    user_prompt = build_recommend_user_prompt(question, context)

    messages = [{"role": "system", "content": RECOMMEND_SYSTEM_PROMPT}]
    messages.extend({"role": h["role"], "content": h["content"]} for h in history)
    messages.append({"role": "user", "content": user_prompt})

    answer = llm.invoke(messages).content
    sources, evidence = _extract_cited(docs, answer)
    return {"answer": answer, "sources": sources, "evidence": evidence}


def extract_drug_names(question: str, rewrite_llm) -> list[str]:
    """질문에 언급된 약 이름(들)을 뽑아낸다. 브랜드명 등 정식 제품명이 아니어도 된다."""
    messages = [
        {"role": "system", "content": DRUG_EXTRACTION_PROMPT},
        {"role": "user", "content": question},
    ]
    response = rewrite_llm.invoke(messages)
    raw = response.content.strip()
    if not raw or raw == "없음":
        return []
    names = [n.strip() for n in raw.split(",") if n.strip()]
    return names[:MAX_INTERACTION_DRUGS]


def _resolve_drug_docs(drug_name: str, vector_store):
    """언급된 약 이름을 실제 등록 품목과 매칭하고, 그 품목의 전체 필드를 가져온다."""
    matches = vector_store.similarity_search(drug_name, k=1)
    if not matches:
        return []
    item_seq = matches[0].metadata.get("item_seq")
    if not item_seq:
        return []
    return vector_store.similarity_search(drug_name, k=PER_DRUG_FIELD_K, filter={"item_seq": item_seq})


def _ask_interaction(question: str, search_query: str, history: list[dict], vector_store, llm, rewrite_llm) -> dict:
    drug_names = extract_drug_names(search_query, rewrite_llm)
    if not drug_names:
        return {
            "answer": "어떤 약들을 함께 복용하시려는지 약품명을 알려주시면 확인해드릴게요.",
            "sources": [],
            "evidence": [],
        }

    docs = []
    for name in drug_names:
        docs.extend(_resolve_drug_docs(name, vector_store))

    if not docs:
        return {
            "answer": "제공된 자료에서 확인되지 않습니다. 약사와 상담하시는 것을 권장드립니다.",
            "sources": [],
            "evidence": [],
        }

    context = build_grouped_context(docs, block_label="약품")
    user_prompt = build_interaction_user_prompt(question, context)

    messages = [{"role": "system", "content": INTERACTION_SYSTEM_PROMPT}]
    messages.extend({"role": h["role"], "content": h["content"]} for h in history)
    messages.append({"role": "user", "content": user_prompt})

    answer = llm.invoke(messages).content
    sources, evidence = _extract_cited(docs, answer)
    return {"answer": answer, "sources": sources, "evidence": evidence}


def ask(question: str, history: list[dict] | None = None, vector_store=None, llm=None, rewrite_llm=None) -> dict:
    vector_store = vector_store or get_vector_store()
    llm = llm or get_llm()
    rewrite_llm = rewrite_llm or get_rewrite_llm()
    history = history or []

    # 후속 질문(예: "부작용은?")도 정확히 검색되도록, 검색에는 재작성된 독립형 질문을 쓴다.
    search_query = rewrite_standalone_question(question, history, rewrite_llm)
    intent = classify_intent(search_query, rewrite_llm)

    if intent == "symptom":
        return _ask_symptom(question, search_query, history, vector_store, llm, rewrite_llm)
    if intent == "interaction":
        return _ask_interaction(question, search_query, history, vector_store, llm, rewrite_llm)
    return _ask_specific(question, search_query, history, vector_store, llm)


def main():
    print("모약이 RAG 체인 테스트 (종료: exit)")
    vector_store = get_vector_store()
    llm = get_llm()
    rewrite_llm = get_rewrite_llm()
    history: list[dict] = []
    while True:
        question = input("\n질문: ").strip()
        if question.lower() in ("exit", "quit"):
            break
        if not question:
            continue
        result = ask(question, history=history, vector_store=vector_store, llm=llm, rewrite_llm=rewrite_llm)
        print(f"\n답변: {result['answer']}")
        print(f"참고 약품: {', '.join(result['sources'])}")
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": result["answer"]})


if __name__ == "__main__":
    main()