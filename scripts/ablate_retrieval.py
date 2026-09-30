# scripts/ablate_retrieval.py
# 检索链消融对比：纯 dense → hybrid(dense+sparse) → hybrid+BGE 精排
# 用高中评测集（13 题，独立评测集：高中数学/物理/化学核心题）测两级命中：
#   ① 文档级：检索结果 source_name 的文件名（stem）== 预期来源文档
#   ② 答案级：top-1 chunk 的 content 含该题参考答案关键词（精排价值的真实体现维度）
# 运行：PYTHONUTF8=1 PYTHONPATH=. .venv/Scripts/python.exe scripts/ablate_retrieval.py
# 前置：远程 Milvus 已灌高中语料（85 chunk）。

import sys
import re
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(".env.local")

from backend.core.knowledge_base import BGEMEmbedder, KnowledgeBaseClient
from backend.core.reranker import retrieve

TENANT = "tenant_default"
RECALL_K = 10
RERANK_K = 3

# (题目, 预期来源文档 stem, [答案关键词])  —— 关键词从参考答案提炼的核心实体/公式
HIGH_QUERIES = [
    ("函数的三要素是什么？", "high_math_lesson_function",
     ["定义域", "值域", "对应关系"]),
    ("函数的单调性如何定义？", "high_math_lesson_function",
     ["x1<x2", "f(x1)<f(x2)", "单调递增"]),
    ("指数函数 y=a^x 中 a 的取值范围是什么？", "high_math_lesson_function",
     ["a≠1", "a>0且a≠1"]),
    ("对数函数与指数函数是什么关系？", "high_math_lesson_function",
     ["反函数"]),
    ("椭圆的离心率公式是什么？取值范围如何？", "high_math_lesson_conic",
     ["c/a", "e=c/a"]),
    ("椭圆标准方程中 a、b、c 满足什么关系？", "high_math_lesson_conic",
     ["b²+c²", "b2+c2", "a²=b²+c²"]),
    ("椭圆的定义是什么？", "high_math_lesson_conic",
     ["距离之和", "两个定点"]),
    ("二项分布 X~B(n,p) 的概率公式是什么？", "high_math_lesson_probability",
     ["p^k", "c(n,k)", "二项分布"]),
    ("正态分布的概率密度曲线关于什么对称？", "high_math_lesson_probability",
     ["x=μ", "关于x=μ", "对称"]),
    ("高中数学学科核心素养包括哪些？", "high_math_curriculum",
     ["数学抽象", "逻辑推理", "数学建模", "直观想象", "数学运算", "数据分析"]),
    ("高中数学的函数主线包括哪些内容？", "high_math_curriculum",
     ["幂函数", "指数函数", "对数函数", "三角函数"]),
    ("牛顿第二定律的公式是什么？", "high_physics_lesson_newton",
     ["f=ma"]),
    ("氧化还原反应的本质是什么？", "high_chemistry_lesson_redox",
     ["得失", "转移"]),
]


def _stem(source_name: str) -> str:
    return (source_name or "").split(" > ")[0].strip()


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def _has_kw(content: str, kws: list[str]) -> bool:
    c = _norm(content)
    return any(_norm(k) in c for k in kws)


def _hit_content(matches: list[dict]) -> tuple[str, str]:
    """返回 top-1 chunk 的 content（用于答案级判定）与 stem。"""
    if not matches:
        return "", ""
    return matches[0].get("content", ""), matches[0]["stem"]


def dense_only(query: str, dense_vec, kb: KnowledgeBaseClient) -> list[dict]:
    res = kb._client.search(
        collection_name="knowledge_domain",
        data=[dense_vec],
        anns_field="embedding",
        search_params={"metric_type": "COSINE", "params": {"ef": kb.ANN_EF}},
        limit=RECALL_K,
        output_fields=["source_name", "content"],
        filter=f'tenant_id == "{TENANT}"',
    )
    return [{"stem": _stem(h["entity"].get("source_name", "")),
             "content": h["entity"].get("content", ""),
             "score": h.get("distance")} for h in res[0]]


def hybrid_only(query: str, dense_vec, sparse_vec, kb: KnowledgeBaseClient) -> list[dict]:
    cands = kb._hybrid_search(
        query_embedding=dense_vec,
        query_sparse=sparse_vec,
        top_k=RECALL_K,
        filters=f'tenant_id == "{TENANT}"',
    )
    return [{"stem": _stem(c["metadata"].get("source_name", "")),
             "content": c["content"],
             "score": c["score"]} for c in cands]


def hybrid_rerank(query: str) -> list[dict]:
    docs, _conf = retrieve(query, TENANT, recall_top_k=RECALL_K, rerank_top_k=RERANK_K)
    return [{"stem": _stem(d.metadata.get("source_name", "")),
             "content": d.content,
             "score": d.score} for d in docs]


def main():
    embedder = BGEMEmbedder.get_instance()
    kb = KnowledgeBaseClient()

    configs = ("dense-only", "hybrid", "hybrid+rerank")
    doc_stats = {c: Counter() for c in configs}
    ans_stats = {c: Counter() for c in configs}
    detail = []

    for q, expected, kws in HIGH_QUERIES:
        dense_vec, sparse_vec = embedder.encode_query(q)
        results = {
            "dense-only":    dense_only(q, dense_vec, kb),
            "hybrid":        hybrid_only(q, dense_vec, sparse_vec, kb),
            "hybrid+rerank": hybrid_rerank(q),
        }
        row = {"q": q, "expected": expected}
        for cfg in configs:
            hits = results[cfg]
            top1 = hits[0] if hits else {}
            # 文档级
            doc_hit = (top1.get("stem") == expected)
            # 答案级：top-1 content 含答案关键词
            ans_hit = _has_kw(top1.get("content", ""), kws)
            doc_stats[cfg]["doc"] += int(doc_hit)
            ans_stats[cfg]["ans"] += int(ans_hit)
            row[f"{cfg}_doc"] = "✓" if doc_hit else "✗"
            row[f"{cfg}_ans"] = "✓" if ans_hit else "✗"
        detail.append(row)

    n = len(HIGH_QUERIES)
    print(f"\n消融对比（{n} 题高中评测集）\n")
    print(f"{'题目':<26} | {'dense-only':>10} | {'hybrid':>10} | {'hybrid+rerank':>10}")
    print(f"{'':<26} | {'doc  ans':>10} | {'doc  ans':>10} | {'doc  ans':>10}")
    print("-" * 72)
    for r in detail:
        print(f"{r['q'][:26]:<26} | {r['dense-only_doc']:>3}  {r['dense-only_ans']:>3} | "
              f"{r['hybrid_doc']:>3}  {r['hybrid_ans']:>3} | "
              f"{r['hybrid+rerank_doc']:>3}  {r['hybrid+rerank_ans']:>3}")

    print("\n" + "=" * 72)
    print(f"{'命中率':<18} | {'dense-only':>12} | {'hybrid':>12} | {'hybrid+rerank':>12}")
    print("-" * 72)
    for c in configs:
        print(f"  文档级 top-1 命中  {c:<14} {doc_stats[c]['doc']}/{n} ({doc_stats[c]['doc']/n:.0%})")
    print("-" * 72)
    for c in configs:
        print(f"  答案级 top-1 命中  {c:<14} {ans_stats[c]['ans']}/{n} ({ans_stats[c]['ans']/n:.0%})")
    print("=" * 72)

    # 写报告
    lines = [
        "# 检索链消融对比报告",
        "",
        f"- 评测集：高中 {n} 题（独立评测集：高中数学/物理/化学核心题）",
        "- 命中口径两级：① 文档级 = source_name 文件名 == 预期来源；② 答案级 = top-1 chunk 内容含参考答案关键词",
        f"- 三配置统一先召回 {RECALL_K} 候选，再取 top-1 判定",
        "",
        "## 命中率对比",
        "",
        "| 口径 | dense-only | hybrid | hybrid+rerank |",
        "|---|---|---|---|",
    ]
    for key, label in (("doc", "文档级 top-1 命中"), ("ans", "答案级 top-1 命中")):
        d = [doc_stats[c][key] for c in configs]
        a = [ans_stats[c][key] for c in configs]
        if key == "doc":
            vals = d
        else:
            vals = a
        lines.append(f"| {label} | {vals[0]}/{n} ({vals[0]/n:.0%}) | {vals[1]}/{n} ({vals[1]/n:.0%}) | {vals[2]}/{n} ({vals[2]/n:.0%}) |")
    lines += ["", "## 逐题明细", "",
              "| 题目 | 预期来源 | dense(doc/ans) | hybrid(doc/ans) | rerank(doc/ans) |",
              "|---|---|---|---|---|"]
    for r in detail:
        lines.append(f"| {r['q'][:22]} | {r['expected']} | "
                     f"{r['dense-only_doc']}/{r['dense-only_ans']} | "
                     f"{r['hybrid_doc']}/{r['hybrid_ans']} | "
                     f"{r['hybrid+rerank_doc']}/{r['hybrid+rerank_ans']} |")
    lines.append("")
    Path(__file__).resolve().parents[1].joinpath("docs", "ablation_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print("\n报告已写入：docs/ablation_report.md")


if __name__ == "__main__":
    main()
