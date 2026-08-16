#!/usr/bin/env python3
"""教材知识库 V1 —— 检索链路调试脚本。

用法：
    # 用内置样例教材（默认，离线 local embedding，无需网络）
    uv run python scripts/test_knowledge_retrieval.py

    # 用真实教材 Markdown 文件
    uv run python scripts/test_knowledge_retrieval.py --md path/to/textbook.md

    # 用真实教材 PDF（走 MinerU，需要 .venv-mineru 已安装）
    uv run python scripts/test_knowledge_retrieval.py --pdf path/to/book.pdf

    # 切换为 SiliconFlow 语义 embedding（需 .env 中已配置 SILICONFLOW_API_KEY）
    uv run python scripts/test_knowledge_retrieval.py --provider siliconflow

输出每个 case 的 Query 与 Top1..Top5 调试信息（score/chapter/section/heading/text），
便于人工判断是 Parser / Chunk / Embedding / Retrieval 哪一层的问题。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from calculus_agent.config import get_settings
from calculus_agent.knowledge.rag.embedding import (
    LocalHashingEmbedding,
    SiliconFlowEmbedding,
    get_embedding_provider,
)
from calculus_agent.knowledge.rag.parser import MarkdownTextbookParser, MinerUTextbookParser
from calculus_agent.knowledge.rag.retriever import KnowledgeRetriever
from calculus_agent.knowledge.rag.vector_store import SqliteVectorStore

HERE = Path(__file__).resolve().parent
DEFAULT_FIXTURE = HERE.parent / "tests" / "knowledge" / "fixtures" / "calculus_textbook_sample.md"

TOP_K = 5


def _print_results(query: str, results, top_k: int) -> None:
    print("\n" + "=" * 72)
    print(f"Query: {query}")
    print("=" * 72)
    if not results:
        print("（无检索结果）")
        return
    for rank, r in enumerate(results[:top_k], start=1):
        preview = " ".join(r.text.split())[:180]
        print(f"\nTop {rank}")
        print(f"  score  : {r.score:.4f}")
        print(f"  chapter: {r.chapter or '(无)'}")
        print(f"  section: {r.section or '(无)'}")
        print(f"  heading: {r.heading or '(无)'}")
        print(f"  source : {r.source_file}")
        print(f"  text   : {preview}")


def _topic_hit(results, keywords) -> bool:
    return any(
        any(kw in (r.text + r.heading + r.chapter + r.section) for kw in keywords)
        for r in results
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="教材知识检索 V1 调试脚本")
    parser.add_argument("--pdf", type=str, default=None, help="教材 PDF 路径（走 MinerU）")
    parser.add_argument("--md", type=str, default=None, help="教材 Markdown 路径")
    parser.add_argument(
        "--provider", type=str, default=None,
        choices=["local", "siliconflow"],
        help="embedding provider（默认读 settings：local）",
    )
    parser.add_argument("--db", type=str, default=None, help="向量库 SQLite 路径（默认内存）")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--case", type=int, default=0, help="只跑指定 case(1/2/3)，0=全部")
    args = parser.parse_args()

    # 选择 embedding provider
    settings = get_settings()
    if args.provider == "siliconflow":
        if not settings.siliconflow_api_key:
            print("错误：未配置 SILICONFLOW_API_KEY，无法使用 siliconflow provider", file=sys.stderr)
            return 2
        embedding = SiliconFlowEmbedding(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            model=settings.textbook_embedding_model,
        )
        print(f"[embedding] SiliconFlow / model={settings.textbook_embedding_model}")
    elif args.provider == "local":
        embedding = LocalHashingEmbedding(dim=settings.textbook_embedding_dim)
        print(f"[embedding] LocalHashing / dim={settings.textbook_embedding_dim}")
    else:
        embedding = get_embedding_provider()
        print(f"[embedding] settings 默认 / {type(embedding).__name__}")

    db_path = args.db or ":memory:"
    store = SqliteVectorStore(db_path)
    retriever = KnowledgeRetriever(embedding_provider=embedding, vector_store=store)

    # 载入教材
    if args.pdf:
        print(f"[解析] MinerU 解析 PDF：{args.pdf}")
        doc = MinerUTextbookParser().parse(Path(args.pdf))
    elif args.md:
        print(f"[解析] 读取 Markdown：{args.md}")
        doc = MarkdownTextbookParser().parse(Path(args.md))
    else:
        print(f"[解析] 内置样例教材：{DEFAULT_FIXTURE}")
        doc = MarkdownTextbookParser().parse(DEFAULT_FIXTURE)

    n = retriever.index_document(doc)
    print(f"[索引] 已切分并向量化 {n} 个知识块")

    cases = {
        1: (
            "求函数的凹凸区间并判断拐点",
            ["凹凸区间", "拐点", "二阶导数判定", "函数的凹凸性"],
        ),
        2: (
            "求函数在某点的极限",
            ["极限运算", "函数极限", "极限"],
        ),
        3: (
            "题目：判断函数 f(x)=x^3-3x 的凹凸性。解答：先求一阶导数 f'(x)=3x^2-3，"
            "再求二阶导数 f''(x)=6x；令 f''(x)=0 解得 x=0。通过一阶导数与二阶导数分析单调区间与极值，"
            "最后根据二阶导数判定法判断凹凸性并求拐点。",
            ["凹凸", "拐点", "二阶导数判定"],
        ),
    }

    run = [args.case] if args.case in cases else sorted(cases)
    all_ok = True
    for cid in run:
        query, keywords = cases[cid]
        results = retriever.retrieve_knowledge(query, top_k=args.top_k)
        _print_results(query, results, args.top_k)
        ok = _topic_hit(results, keywords)
        all_ok = all_ok and ok
        label = "PASS" if ok else "FAIL"
        print(f"\n[Case {cid}] 期望命中 {keywords} -> {label}")

    print("\n" + "=" * 72)
    print(f"汇总: {'全部 PASS' if all_ok else '存在 FAIL（请人工查看 Top-K 内容）'}")
    print("=" * 72)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
