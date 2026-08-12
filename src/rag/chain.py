"""STEP 5: 검색(Pinecone) + 생성(GPT-4o) RAG 체인"""

import sys
from pathlib import Path

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src import config
from src.rag.prompts import FIELD_LABELS, SYSTEM_PROMPT, build_context, build_user_prompt

INDEX_NAME = "moyak-eyakeunyo"
EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o"
TOP_K = 5
TEMPERATURE = 0.2


def get_retriever():
    embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL, openai_api_key=config.OPENAI_API_KEY)
    vector_store = PineconeVectorStore(
        index_name=INDEX_NAME,
        embedding=embeddings,
        pinecone_api_key=config.PINECONE_API_KEY,
    )
    return vector_store.as_retriever(search_kwargs={"k": TOP_K})


def get_llm():
    return ChatOpenAI(model_name=CHAT_MODEL, openai_api_key=config.OPENAI_API_KEY, temperature=TEMPERATURE)


def ask(question: str, retriever=None, llm=None) -> dict:
    retriever = retriever or get_retriever()
    llm = llm or get_llm()

    docs = retriever.invoke(question)
    context = build_context(docs)
    user_prompt = build_user_prompt(question, context)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    response = llm.invoke(messages)
    answer = response.content

    # 검색은 됐지만 LLM이 답변에서 실제로 언급/인용한 청크만 근거로 남긴다
    # (그렇지 않으면 "확인되지 않습니다" 답변에도 무관한 약품명/원문이 붙는 문제가 생김)
    cited_docs = [doc for doc in docs if doc.metadata.get("item_name") and doc.metadata["item_name"] in answer]
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
    return {"answer": answer, "sources": sources, "evidence": evidence}


def main():
    print("모약이 RAG 체인 테스트 (종료: exit)")
    retriever = get_retriever()
    llm = get_llm()
    while True:
        question = input("\n질문: ").strip()
        if question.lower() in ("exit", "quit"):
            break
        if not question:
            continue
        result = ask(question, retriever=retriever, llm=llm)
        print(f"\n답변: {result['answer']}")
        print(f"참고 약품: {', '.join(result['sources'])}")


if __name__ == "__main__":
    main()