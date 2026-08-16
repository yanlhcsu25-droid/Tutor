"""教材知识检索 V1 —— 三个验证 case（含调试信息输出）。

运行：
    uv run python -m tests.knowledge.test_textbook_retrieval
或：
    uv run pytest tests/knowledge/test_textbook_retrieval.py -s

本测试不仅断言 PASS/FAIL，还会打印 Top-K 调试信息，便于人工判断：
    Parser / Chunk / Embedding / Retrieval 哪一层出了问题。
"""

from __future__ import annotations

from pathlib import Path

from calculus_agent.knowledge.rag.embedding import LocalHashingEmbedding
from calculus_agent.knowledge.rag.parser import MarkdownTextbookParser
from calculus_agent.knowledge.rag.retriever import KnowledgeRetriever
from calculus_agent.knowledge.rag.vector_store import SqliteVectorStore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "calculus_textbook_sample.md"

TOP_K = 5


def _build_retriever() -> KnowledgeRetriever:
    embedding = LocalHashingEmbedding(dim=2048)
    store = SqliteVectorStore(":memory:")
    retriever = KnowledgeRetriever(embedding_provider=embedding, vector_store=store)
    doc = MarkdownTextbookParser().parse(FIXTURE)
    n = retriever.index_document(doc)
    print(f"\n[索引] 已切分并向量化 {n} 个知识块（来源：{doc.source_file}）")
    return retriever


def _print_results(query: str, results) -> None:
    print("\n" + "=" * 70)
    print(f"Query: {query}")
    print("=" * 70)
    if not results:
        print("（无检索结果）")
        return
    for rank, r in enumerate(results[:TOP_K], start=1):
        preview = r.text.replace("\n", " ")[:160]
        print(f"\nTop {rank}")
        print(f"  score : {r.score:.4f}")
        print(f"  chapter: {r.chapter or '(无)'}")
        print(f"  section: {r.section or '(无)'}")
        print(f"  heading: {r.heading or '(无)'}")
        print(f"  source : {r.source_file}")
        print(f"  text   : {preview}")


def _contains_topic(results, keywords) -> bool:
    return any(
        any(kw in (r.text + r.heading + r.chapter + r.section) for kw in keywords)
        for r in results
    )


def case_concavity() -> bool:
    retriever = _build_retriever()
    query = "求函数的凹凸区间并判断拐点"
    results = retriever.retrieve_knowledge(query, top_k=TOP_K)
    _print_results(query, results)
    ok = _contains_topic(results, ["凹凸区间", "拐点", "二阶导数判定", "函数的凹凸性"])
    print(f"\n[Case 1 函数凹凸性] 期望命中『凹凸区间/拐点/二阶导数判定/函数的凹凸性』 -> {'PASS' if ok else 'FAIL'}")
    return ok


def case_limit() -> bool:
    retriever = _build_retriever()
    query = "求函数在某点的极限"
    results = retriever.retrieve_knowledge(query, top_k=TOP_K)
    _print_results(query, results)
    top = results[0] if results else None
    limit_hit = _contains_topic(results, ["极限运算", "函数极限", "极限"])
    # 不应被凹凸性主导：top1 应属于极限主题
    top_is_limit = bool(top) and ("极限" in (top.heading + top.text + top.chapter + top.section))
    ok = limit_hit and top_is_limit
    print(f"\n[Case 2 函数极限] 期望主要召回『函数极限/极限运算法则』而非凹凸性 -> {'PASS' if ok else 'FAIL'}")
    return ok


def case_target_vs_method() -> bool:
    """最重要的 bad case：题目目标=判断凹凸性，但解答大量使用一阶/二阶导数。"""
    retriever = _build_retriever()
    query = (
        "题目：判断函数 f(x) = x^3 - 3x 的凹凸性。\n"
        "解答过程：先求一阶导数 f'(x) = 3x^2 - 3，再求二阶导数 f''(x) = 6x；"
        "令 f''(x) = 0 解得 x = 0。通过一阶导数与二阶导数分析单调区间与极值，"
        "最后根据二阶导数判定法判断凹凸性并求拐点。"
    )
    results = retriever.retrieve_knowledge(query, top_k=TOP_K)
    _print_results(query, results)
    concavity_hit = _contains_topic(results, ["凹凸", "拐点", "二阶导数判定"])
    ok = concavity_hit
    print(
        f"\n[Case 3 考查对象 vs 解题方法] 期望仍召回『函数凹凸性』相关教材而非被导数计算占据 -> "
        f"{'PASS' if ok else 'FAIL'}"
    )
    return ok


def main() -> None:
    r1 = case_concavity()
    r2 = case_limit()
    r3 = case_target_vs_method()
    print("\n" + "=" * 70)
    print(f"汇总: Case1={r1}  Case2={r2}  Case3={r3}")
    print("=" * 70)


if __name__ == "__main__":
    main()
