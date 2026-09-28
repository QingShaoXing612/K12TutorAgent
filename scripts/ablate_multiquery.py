# scripts/ablate_multiquery.py
# 多路召回消融：直接检索（PRECISE 单 query）vs Multi-Query 多路召回（BROAD 子查询合并）
# 用困难集 10 题（跨文档/多跳，与 eval_ragas.py HARD_EVAL_SET 一致）比「参考答案信息点覆盖率」。
# 判定：检索结果合并文本覆盖了 reference 的多少个关键信息点（关键词从 reference 原文提炼）。
# 运行：PYTHONUTF8=1 PYTHONPATH=. .venv/Scripts/python.exe scripts/ablate_multiquery.py
# 前置：远程 Milvus 已灌语料；DeepSeek API key 已配。

import sys
import re
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(".env.local")

from langchain_core.messages import HumanMessage

from backend.core.reranker import retrieve
from backend.core.llm_factory import get_llm

TENANT = "tenant_default"
MAX_BROAD_QUERIES = 3
RECALL_TOP_K_BROAD_PER = 4

MULTI_QUERY_REWRITE_PROMPT = """你是一位 K12 教学助手，负责将学生模糊的问题改写为多个具体的学科问题。

【上一轮 AI 回答内容（供参考，推断"没懂"指的是什么）】
{last_answer}

【学生当前输入】
{query}

请将学生的问题改写为 3-5 个具体、独立的学科问题，每行一个。
要求：
- 每个问题独立完整，可以单独搜索
- 覆盖不同角度（是什么 / 为什么 / 怎么做 / 和什么区别）
- 不要编号，直接输出问题文本

输出示例：
圆的周长公式是什么？
直径和半径是什么关系？
如何用滚动法测量圆的周长？"""

# 困难集 10 题：每题 (question, [信息点关键词列表, ...])
# 信息点关键词从 reference 原文提炼（benchmark 参考答案锚定原文，正确 chunk 必含这些词）
HARD_EVAL_SET = [
    ("圆的周长教学中，教案「探究新知」和课标「教学提示」共同强调的教学方法是什么？各自如何体现？",
     [["猜想", "验证"], ["滚动法", "绕绳法"], ["极限", "化曲为直"]]),
    ("为什么「理解圆周率 π 的意义」是教学难点？教案和课标分别给出什么依据？",
     [["本质", "死记"], ["猜想", "验证"], ["祖冲之"]]),
    ("圆的周长公式 C = πd = 2πr 中，d 与 r 分别代表什么？有何关系？",
     [["直径", "半径"], ["2倍", "两倍", "2r"]]),
    ("课程标准要求第三学段学生探索并掌握圆柱和圆锥的哪些计算？",
     [["圆柱"], ["圆锥"]]),
    ("教案「巩固练习」环节包含哪两类练习？",
     [["求周长"], ["求直径", "逆向"]]),
    ("论文用「数形结合」解决分数概念的什么问题？具体借助哪些模型和活动？",
     [["抽象", "认知"], ["面积模型", "数轴模型", "数轴"], ["折纸", "涂色"]]),
    ("论文强调「平均分」在分数概念中的地位，具体如何论证？",
     [["平均分", "前提"], ["折纸", "涂色"]]),
    ("圆的周长教学「教学反思」和论文「结论」各自强调什么教学理念？",
     [["动手", "测量"], ["数形结合", "鸿沟"]]),
    ("课标「内容要求」里，关于圆的认识和测量，规定了哪几个层次？",
     [["圆规", "扇形"], ["直径", "半径"], ["周长", "面积"]]),
    ("圆周率 π 是谁「发现」的？",
     [["祖冲之", "贡献"]]),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").lower())


def _has_kw(content: str, kws: list[str]) -> bool:
    c = _norm(content)
    return any(_norm(k) in c for k in kws)


def _coverage(docs, info_points) -> tuple[int, int]:
    """返回 (覆盖信息点数, 总信息点数)。一个信息点命中其任一关键词即算覆盖。"""
    text = " ".join(getattr(d, "content", "") for d in docs)
    covered = sum(1 for kws in info_points if _has_kw(text, kws))
    return covered, len(info_points)


def _direct_retrieve(query: str):
    docs, _ = retrieve(query, TENANT, recall_top_k=10, rerank_top_k=3)
    return docs


def _multi_retrieve(sub_queries: list[str]):
    seen = {}
    for q in sub_queries:
        docs, _ = retrieve(q, TENANT, recall_top_k=RECALL_TOP_K_BROAD_PER, rerank_top_k=RECALL_TOP_K_BROAD_PER)
        for d in docs:
            key = d.content[:100]
            if key not in seen or d.score > seen[key].score:
                seen[key] = d
    return sorted(seen.values(), key=lambda x: x.score, reverse=True)


def _extract_text(resp) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content).strip()
    return str(content).strip()


async def _rewrite(query: str) -> list[str]:
    prompt = MULTI_QUERY_REWRITE_PROMPT.format(last_answer="（无上一轮回答）", query=query)
    llm = get_llm("qa", temperature=0.3)
    resp = await llm.ainvoke([HumanMessage(content=prompt)])
    raw = _extract_text(resp)
    rewritten = [
        line.lstrip("0123456789.-）)、 ").strip()
        for line in raw.split("\n")
        if line.strip() and len(line.strip()) > 3
    ][:MAX_BROAD_QUERIES]
    return rewritten or [query]


async def main():
    print(f"\n多路召回消融（困难集 {len(HARD_EVAL_SET)} 题，比参考答案信息点覆盖率）\n")
    direct_hits = 0
    multi3_hits = 0
    multi6_hits = 0
    total_points = 0
    detail = []

    for question, info_points in HARD_EVAL_SET:
        direct_docs = _direct_retrieve(question)          # top-3
        sub_queries = await _rewrite(question)
        multi_merged = _multi_retrieve(sub_queries)        # 完整 merged（不截断）

        dc, np_ = _coverage(direct_docs, info_points)
        mc3, _ = _coverage(multi_merged[:3], info_points)
        mc6, _ = _coverage(multi_merged[:6], info_points)

        direct_hits += dc
        multi3_hits += mc3
        multi6_hits += mc6
        total_points += np_

        detail.append((question[:24], dc, mc3, mc6, np_, sub_queries))
        print(f"  [{question[:24]}] 直接 {dc}/{np_} | 多路top3 {mc3}/{np_} | 多路top6 {mc6}/{np_}")

    n = len(HARD_EVAL_SET)
    print("\n" + "=" * 72)
    print(f"信息点覆盖：直接检索    {direct_hits}/{total_points} ({direct_hits/total_points:.0%})")
    print(f"            多路 top-3  {multi3_hits}/{total_points} ({multi3_hits/total_points:.0%})")
    print(f"            多路 top-6  {multi6_hits}/{total_points} ({multi6_hits/total_points:.0%})")
    print("=" * 72)

    # 写报告
    lines = [
        "# 多路召回消融对比报告",
        "",
        f"- 评测集：困难集 {n} 题（跨文档/多跳，与 `eval_ragas.py` HARD_EVAL_SET 一致）",
        "- 对比：直接检索（PRECISE 单 query top-3）vs Multi-Query 多路召回（BROAD 子查询合并 top-3 / top-6）",
        "- 判定：检索结果合并文本覆盖参考答案关键信息点的比例（关键词从 reference 原文提炼）",
        "",
        "## 汇总",
        "",
        f"- 信息点覆盖：直接 **{direct_hits}/{total_points} ({direct_hits/total_points:.0%})** ｜ 多路 top-3 **{multi3_hits}/{total_points} ({multi3_hits/total_points:.0%})** ｜ 多路 top-6 **{multi6_hits}/{total_points} ({multi6_hits/total_points:.0%})**",
        "",
        "## 逐题明细",
        "",
        "| 题目 | 直接 | 多路top3 | 多路top6 | 子查询示例 |",
        "|---|---|---|---|---|",
    ]
    for q, dc, mc3, mc6, np_, subs in detail:
        lines.append(f"| {q} | {dc}/{np_} | {mc3}/{np_} | {mc6}/{np_} | {'；'.join(subs[:2])} |")
    lines.append("")
    Path(__file__).resolve().parents[1].joinpath("docs", "ablation_multiquery.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print("\n报告已写入：docs/ablation_multiquery.md")


if __name__ == "__main__":
    asyncio.run(main())
