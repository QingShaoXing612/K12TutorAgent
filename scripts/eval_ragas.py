# scripts/eval_ragas.py
# RAGAS 风格 RAG 质量评测（LLM-as-judge，4 个核心指标）
#
# 评估对象：retrieve()（Hybrid 召回 + BGE 精排）+ RAG 生成（DeepSeek）
# 评测集：基于 samples/ 下语料（小学圆的周长/数学课标/分数论文 + 高中数学/物理/化学/语文/英语）的问答对
#
# 指标（对齐 RAGAS 定义，DeepSeek 当裁判）：
#   faithfulness     答案忠实度   —— 答案里的每条陈述能否被检索上下文支持   [0,1]
#   answer_relevancy 答案相关性   —— 答案是否针对问题（生成问题 + 向量相似度） [0,1]
#   context_precision 上下文精确率 —— 相关文档块是否排在前面（rank 加权）    [0,1]
#   context_recall   上下文召回率 —— 参考答案的关键信息是否都被检索到        [0,1]
#
# 运行：PYTHONUTF8=1 PYTHONPATH=. .venv/Scripts/python.exe scripts/eval_ragas.py
# 前置：Milvus 已灌入 3 份中小学语料（scripts/build_knowledge_base.py）

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(".env.local")

import numpy as np
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage

from backend.core.reranker import retrieve
from backend.core.llm_factory import get_llm, get_structured_llm
from backend.core.knowledge_base import BGEMEmbedder

TENANT_ID    = "tenant_default"
RERANK_TOP_K = 3          # 精排后喂给 LLM 的文档块数
REPORT_PATH  = Path(__file__).parent.parent / "docs" / "ragas_report.md"

# ══════════════════════════════════════════════════════════════
# 基础集（question + reference 参考答案，均据 3 份中小学数学语料编写）
# ══════════════════════════════════════════════════════════════

EVAL_SET = [
    # ── 圆的周长教案（lesson_plan_sample.md）──
    {"question": "圆的周长计算公式是什么？",
     "reference": "圆的周长计算公式是 C = πd = 2πr，π 取近似值 3.14。"},
    {"question": "圆周率 π 的近似值取多少？",
     "reference": "π 取近似值 3.14。"},
    {"question": "教案中用哪两种方法测量圆形纸片的周长与直径？",
     "reference": "用滚动法和绕绳法测量圆形纸片的周长与直径。"},
    {"question": "谁对圆周率研究做出了贡献？",
     "reference": "祖冲之对圆周率研究做出了贡献。"},
    {"question": "圆的周长教学，重点和难点分别是什么？",
     "reference": "重点是圆的周长计算公式的推导与应用；难点是理解圆周率 π 的意义。"},

    # ── 义务教育数学课程标准（curriculum_sample.md）──
    {"question": "课程标准要求第三学段学生探索并掌握哪些立体图形的计算？",
     "reference": "认识圆柱、圆锥，探索并掌握圆柱的表面积、体积以及圆锥体积的计算公式。"},
    {"question": "课程标准要求探索并掌握圆的哪些计算公式？",
     "reference": "圆的周长和面积计算公式，能解决简单实际问题。"},
    {"question": "课程标准「学业要求」提出在解决与圆有关的问题中形成哪些素养？",
     "reference": "形成量感、空间观念和几何直观。"},
    {"question": "圆的周长教学应引导学生经历怎样的探究过程？",
     "reference": "经历「测量—猜想—验证」的完整探究过程，感悟极限思想与化曲为直的数学方法。"},
    {"question": "课程标准「教学提示」提到圆的周长教学应体会谁的贡献？",
     "reference": "体会祖冲之对圆周率研究的贡献。"},

    # ── 分数概念教学论文（paper_sample.md）──
    {"question": "论文提出的分数概念教学核心策略是什么？",
     "reference": "以「数形结合」为核心策略，借助面积模型、数轴模型帮助学生建立分数的直观。"},
    {"question": "论文用哪两种模型帮助学生建立分数的直观？",
     "reference": "面积模型和数轴模型。"},
    {"question": "论文用「为什么 3/4 与 6/8 相等」说明什么教学问题？",
     "reference": "学生停留在程序性记忆层面，未形成概念性理解。"},
    {"question": "论文中的操作活动通过什么方式让学生理解「平均分」？",
     "reference": "通过折纸、涂色等动手活动，让学生在做中理解「平均分」是分数产生的前提。"},
    {"question": "论文建议在哪些后续教学中持续渗透数形结合思想？",
     "reference": "分数乘法、分数除法等后续教学。"},
]


# ── 困难集：多跳 / 跨 chunk / 对抗性（需综合多处信息或辨析易混概念）──

HARD_EVAL_SET = [
    {"question": "圆的周长教学中，教案「探究新知」和课标「教学提示」共同强调的教学方法是什么？各自如何体现？",
     "reference": "共同强调「测量—猜想—验证」的探究过程。教案通过滚动法/绕绳法测量、计算周长与直径的比值发现规律；课标强调感悟极限思想与化曲为直的数学方法。"},
    {"question": "为什么「理解圆周率 π 的意义」是教学难点？教案和课标分别给出什么依据？",
     "reference": "教案把「理解圆周率 π 的意义」列为难点，通过动手测量亲历猜想—验证理解 π 的本质而非死记公式；课标要求感悟极限思想、化曲为直，体会祖冲之对圆周率研究的贡献。"},
    {"question": "圆的周长公式 C = πd = 2πr 中，d 与 r 分别代表什么？有何关系？",
     "reference": "d 是直径，r 是半径，直径等于半径的 2 倍（d = 2r）。课标要求知道圆的特征，理解直径、半径的关系。"},
    {"question": "课程标准要求第三学段学生探索并掌握圆柱和圆锥的哪些计算？",
     "reference": "圆柱的表面积、体积，以及圆锥体积的计算公式。"},
    {"question": "教案「巩固练习」环节包含哪两类练习？",
     "reference": "已知直径或半径求周长，以及已知周长求直径的逆向练习。"},
    {"question": "论文用「数形结合」解决分数概念的什么问题？具体借助哪些模型和活动？",
     "reference": "解决分数概念抽象性造成的认知困难。借助面积模型、数轴模型，以及折纸、涂色等操作活动，弥合抽象与直观之间的鸿沟。"},
    {"question": "论文强调「平均分」在分数概念中的地位，具体如何论证？",
     "reference": "通过折纸、涂色等操作活动让学生在做中理解「平均分」是分数产生的前提。"},
    {"question": "圆的周长教学「教学反思」和论文「结论」各自强调什么教学理念？",
     "reference": "教案反思强调动手测量亲历猜想—验证、理解圆周率的本质而非死记公式；论文结论强调数形结合能弥合分数概念抽象与直观的鸿沟，建议持续渗透。"},
    {"question": "课标「内容要求」里，关于圆的认识和测量，规定了哪几个层次？",
     "reference": "认识圆和扇形、会用圆规画圆、知道圆的特征理解直径半径关系；探索并掌握圆的周长和面积计算公式。"},
    {"question": "圆周率 π 是谁「发现」的？",
     "reference": "语料表述为「祖冲之对圆周率研究做出了贡献」，并未表述为「发现」π；应表述为祖冲之对圆周率研究作出贡献。"},
]


# ── 高中集：基于 7 份高中语料（高一/高二/高三数学 + 物理/化学）──

HIGH_EVAL_SET = [
    # ── 高一函数教案（high_math_lesson_function.md）──
    {"question": "函数的三要素是什么？",
     "reference": "函数三要素是定义域、值域、对应关系。"},
    {"question": "函数的单调性如何定义？",
     "reference": "设函数 f(x) 定义域为 I，区间 D⊆I，若对任意 x1<x2∈D 都有 f(x1)<f(x2)，则称 f(x) 在 D 上单调递增。"},
    {"question": "指数函数 y=a^x 中 a 的取值范围是什么？",
     "reference": "a>0 且 a≠1。"},
    {"question": "对数函数与指数函数是什么关系？",
     "reference": "互为反函数。"},
    # ── 高二椭圆教案（high_math_lesson_conic.md）──
    {"question": "椭圆的离心率公式是什么？取值范围如何？",
     "reference": "离心率 e=c/a，取值范围 0<e<1。"},
    {"question": "椭圆标准方程中 a、b、c 满足什么关系？",
     "reference": "a²=b²+c²。"},
    {"question": "椭圆的定义是什么？",
     "reference": "平面内到两个定点 F1、F2 的距离之和等于常数 2a（2a>|F1F2|）的点的轨迹。"},
    # ── 高三随机变量教案（high_math_lesson_probability.md）──
    {"question": "二项分布 X~B(n,p) 的概率公式是什么？",
     "reference": "P(X=k)=C(n,k)·p^k·(1-p)^(n-k)。"},
    {"question": "正态分布的概率密度曲线关于什么对称？",
     "reference": "关于 x=μ 对称，σ 越大曲线越矮胖。"},
    # ── 高中数学课标（high_math_curriculum.md）──
    {"question": "高中数学学科核心素养包括哪些？",
     "reference": "数学抽象、逻辑推理、数学建模、直观想象、数学运算、数据分析。"},
    {"question": "高中数学的函数主线包括哪些内容？",
     "reference": "必修：函数概念与性质、幂函数、指数函数、对数函数、三角函数、函数应用；选择性必修：数列、一元函数导数及其应用。"},
    # ── 高一物理（high_physics_lesson_newton.md）──
    {"question": "牛顿第二定律的公式是什么？",
     "reference": "F=ma。"},
    # ── 高一化学（high_chemistry_lesson_redox.md）──
    {"question": "氧化还原反应的本质是什么？",
     "reference": "电子的转移（得失或偏移）。"},
    {"question": "氧化还原反应中，化合价升高的元素被什么、作什么剂？",
     "reference": "化合价升高被氧化，作还原剂。"},
    {"question": "双线桥法表示电子转移时分别标出什么？",
     "reference": "分别标出反应物中得电子与失电子的元素。"},

    # ── 高一物理补充（high_physics_lesson_newton.md）──
    {"question": "牛顿第二定律 F=ma 中的 F 指什么？",
     "reference": "合外力，即物体所受各力的合力。"},
    {"question": "牛顿第二定律具有哪些特性？",
     "reference": "瞬时性、矢量性、独立性、同体性四大特性。"},

    # ── 高一语文（high_chinese_lesson_quanxue.md）──
    {"question": "《劝学》开篇提出的中心论点是什么？",
     "reference": "学不可以已，即学习不可以停止。"},
    {"question": "「青，取之于蓝而青于蓝」用了什么论证方法，说明什么道理？",
     "reference": "比喻论证，用青出于蓝、冰寒于水比喻学习可以提高自己、后学可以超过前人。"},
    {"question": "「锲而不舍，金石可镂」与「锲而舍之，朽木不折」构成什么论证？",
     "reference": "对比论证，通过正反对比说明学习贵在坚持。"},
    {"question": "《劝学》的作者是谁？属于哪个学派的哪一时期思想家？",
     "reference": "荀子，名况，战国末期赵国人，儒家学派思想家。"},

    # ── 高一英语（high_english_lesson_attributive_clause.md）──
    {"question": "定语从句中引导词分为哪两类？",
     "reference": "关系代词（who、whom、which、that、whose）和关系副词（when、where、why）。"},
    {"question": "关系代词 who 和 which 分别指代什么？",
     "reference": "who 指人，which 指物。"},
    {"question": "限制性定语从句与非限制性定语从句在形式上的主要区别是什么？",
     "reference": "非限制性定语从句用逗号隔开且不可用 that 引导，限制性定语从句不用逗号。"},

    # ── 高二物理（high_physics_lesson_momentum.md）──
    {"question": "动量守恒定律的适用条件是什么？",
     "reference": "系统不受外力或所受合外力为零时，系统总动量保持不变。"},
    {"question": "动量定理的表达式是什么？",
     "reference": "Ft=Δp=mv₂-mv₁，即合外力的冲量等于物体动量的变化。"},
    {"question": "动量的定义式和单位是什么？",
     "reference": "p=mv，单位是 kg·m/s。"},

    # ── 高二化学（high_chemistry_lesson_equilibrium.md）──
    {"question": "化学平衡状态的特征可用哪五个字概括？",
     "reference": "逆、等、动、定、变。"},
    {"question": "勒夏特列原理的内容是什么？",
     "reference": "改变影响平衡的条件，平衡向减弱这种改变的方向移动。"},
    {"question": "催化剂对化学平衡有什么影响？",
     "reference": "催化剂只改变反应速率（缩短到达平衡的时间），不改变平衡状态，平衡不移动。"},
]


# ══════════════════════════════════════════════════════════════
# LLM-as-judge 结构化输出 Schema
# ══════════════════════════════════════════════════════════════

class Statements(BaseModel):
    statements: list[str] = Field(description="答案中拆出的原子事实陈述列表")


class StatementVerdict(BaseModel):
    statement: str
    verdict: int = Field(description="1=被上下文支持，0=不支持")
    reason: str = ""


class StatementVerdicts(BaseModel):
    verdicts: list[StatementVerdict]


class GeneratedQuestions(BaseModel):
    questions: list[str] = Field(description="答案所回答的问题列表")


class ChunkVerdict(BaseModel):
    chunk_index: int = Field(description="文档块编号（0 起）")
    verdict: int = Field(description="1=相关，0=不相关")
    reason: str = ""


class ChunkVerdicts(BaseModel):
    verdicts: list[ChunkVerdict]


class ClaimVerdict(BaseModel):
    claim: str = Field(description="参考答案中的关键信息点")
    verdict: int = Field(description="1=上下文中能找到依据，0=找不到")
    reason: str = ""


class ClaimVerdicts(BaseModel):
    verdicts: list[ClaimVerdict]


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def _cosine(a, b) -> float:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 1e-9 else 0.0


def _fmt_contexts(docs) -> str:
    """把精排文档块拼成编号上下文，供生成与裁判使用。"""
    parts = []
    for i, d in enumerate(docs):
        parts.append(f"[{i}] {d.content.strip()}")
    return "\n\n".join(parts)


def rag_generate(question: str, docs) -> str:
    """基于检索上下文的 RAG 生成（温度 0，仅依据上下文）。"""
    llm = get_llm("qa", temperature=0)
    prompt = (
        "你是 K12 教学助教。请仅根据以下检索到的上下文回答问题，"
        "不要编造上下文之外的信息。若上下文不足以回答，请说明「知识库中未找到相关信息」。\n\n"
        f"上下文：\n{_fmt_contexts(docs)}\n\n"
        f"问题：{question}"
    )
    return _extract_text(llm.invoke([HumanMessage(content=prompt)]))


def _extract_text(resp) -> str:
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        ).strip()
    return str(content).strip()


# ══════════════════════════════════════════════════════════════
# 四个指标（LLM-as-judge）
# ══════════════════════════════════════════════════════════════

async def faithfulness(question: str, answer: str, docs) -> float:
    """答案忠实度：答案中每条陈述被上下文支持的比例。"""
    judge = get_structured_llm("qa", Statements)
    # Step 1：拆陈述
    s = await judge.ainvoke([
        HumanMessage(content=(
            f"将下面的答案拆解成一条条独立的、可验证的事实性陈述（原子陈述），"
            f"每条是一个简单事实主张，不含连接词。\n\n答案：{answer}"
        ))
    ])
    statements = s.statements
    if not statements:
        return 1.0   # 无事实主张（如「未找到信息」）视为无幻觉

    # Step 2：逐条判支持
    vj = get_structured_llm("qa", StatementVerdicts)
    v = await vj.ainvoke([
        HumanMessage(content=(
            f"上下文：\n{_fmt_contexts(docs)}\n\n"
            f"陈述列表：\n"
            + "\n".join(f"{i}. {st}" for i, st in enumerate(statements))
            + "\n\n逐条判断每条陈述是否被上下文支持（1=支持，0=不支持），并给出理由。"
        ))
    ])
    verdicts = v.verdicts
    if not verdicts:
        return 0.0
    return sum(x.verdict for x in verdicts) / len(verdicts)


async def answer_relevancy(question: str, answer: str) -> float:
    """答案相关性：答案所回答的问题与原问题的向量相似度均值。"""
    judge = get_structured_llm("qa", GeneratedQuestions)
    g = await judge.ainvoke([
        HumanMessage(content=(
            f"给定一个答案，生成 3 个该答案所针对/回答的问题"
            f"（即这个答案是在回答哪些问题）。\n\n答案：{answer}"
        ))
    ])
    questions = g.questions or []

    embedder = BGEMEmbedder.get_instance()
    q_dense, _ = embedder.encode_query(question)
    sims = []
    for gen_q in questions:
        g_dense, _ = embedder.encode_query(gen_q)
        sims.append(_cosine(q_dense, g_dense))
    return float(np.mean(sims)) if sims else 0.0


async def context_precision(question: str, reference: str, docs) -> float:
    """上下文精确率：相关文档块是否排在前面（rank 加权 precision@k 均值）。"""
    judge = get_structured_llm("qa", ChunkVerdicts)
    v = await judge.ainvoke([
        HumanMessage(content=(
            f"问题：{question}\n参考答案：{reference}\n\n"
            f"检索到的文档块（按相关性排序）：\n{_fmt_contexts(docs)}\n\n"
            f"判断每个文档块对回答该问题是否相关（1=相关，0=不相关），并给出理由。"
        ))
    ])
    verdicts = {x.chunk_index: x.verdict for x in v.verdicts}
    K = len(docs)
    if K == 0:
        return 0.0
    # precision@k = 前 k 个里相关的比例；再对 k=1..K 取平均
    prec_sum = 0.0
    for k in range(1, K + 1):
        rel = sum(verdicts.get(i, 0) for i in range(k))
        prec_sum += rel / k
    return prec_sum / K


async def context_recall(reference: str, docs) -> float:
    """上下文召回率：参考答案关键信息在检索上下文中可找到的比例。"""
    judge = get_structured_llm("qa", ClaimVerdicts)
    v = await judge.ainvoke([
        HumanMessage(content=(
            f"参考答案：{reference}\n\n"
            f"检索到的上下文：\n{_fmt_contexts(docs)}\n\n"
            f"把参考答案拆成若干关键信息点，逐条判断每条信息能否在上下文中找到依据"
            f"（1=能找到，0=找不到），并给出理由。"
        ))
    ])
    verdicts = v.verdicts
    if not verdicts:
        return 0.0
    return sum(x.verdict for x in verdicts) / len(verdicts)


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

async def evaluate_one(idx: int, item: dict) -> dict:
    q = item["question"]
    ref = item["reference"]

    # ① 检索（Hybrid 召回 + BGE 精排）
    docs, confidence = retrieve(q, TENANT_ID, rerank_top_k=RERANK_TOP_K)

    # ② 生成
    answer = rag_generate(q, docs)

    # ③ 四项指标
    fa = await faithfulness(q, answer, docs)
    ar = await answer_relevancy(q, answer)
    cp = await context_precision(q, ref, docs)
    cr = await context_recall(ref, docs)

    return {
        "idx": idx,
        "question": q,
        "answer": answer,
        "confidence": confidence,
        "n_docs": len(docs),
        "faithfulness": fa,
        "answer_relevancy": ar,
        "context_precision": cp,
        "context_recall": cr,
    }


async def run_set(name: str, items: list[dict]) -> tuple[list[dict], dict]:
    print(f"\n{'='*60}\n {name}（{len(items)} 题）\n{'='*60}")
    results = []
    for idx, item in enumerate(items):
        r = await evaluate_one(idx, item)
        results.append(r)
        print(
            f"[{idx+1:>2}/{len(items)}] 忠实度={r['faithfulness']:.2f} "
            f"相关={r['answer_relevancy']:.2f} 精率={r['context_precision']:.2f} "
            f"召回={r['context_recall']:.2f} 置信={r['confidence']:.2f} ｜ {item['question'][:26]}"
        )
    agg = {k: float(np.mean([r[k] for r in results])) for k in
           ("faithfulness", "answer_relevancy", "context_precision", "context_recall", "confidence")}
    return results, agg


def _detail_lines(results: list[dict]) -> list[str]:
    lines = [
        "| # | 问题 | 忠实度 | 相关性 | 精确率 | 召回率 | 置信度 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['idx']+1} | {r['question'][:30]} | {r['faithfulness']:.2f} | "
            f"{r['answer_relevancy']:.2f} | {r['context_precision']:.2f} | "
            f"{r['context_recall']:.2f} | {r['confidence']:.2f} |"
        )
    return lines


async def main():
    high_only = "--high-only" in sys.argv
    sets = (
        [("高中集（高中数学/物理/化学）", HIGH_EVAL_SET)]
        if high_only
        else [
            ("基础集（单跳/直接抽取）", EVAL_SET),
            ("困难集（多跳/跨chunk/对抗）", HARD_EVAL_SET),
            ("高中集（高中数学/物理/化学）", HIGH_EVAL_SET),
        ]
    )
    print(f"RAGAS 评测：{len(sets)} 组（recall_top_k=10 → rerank_top_k={RERANK_TOP_K}）")

    results = {}
    for name, items in sets:
        r, agg = await run_set(name, items)
        results[name] = (r, agg)

    # 打印聚合对比
    print("\n" + "=" * 60)
    print(" 聚合结果对比（0~1）")
    print("=" * 60)
    for name, (_, agg) in results.items():
        print(f" 【{name}】忠实度={agg['faithfulness']:.3f} 相关={agg['answer_relevancy']:.3f} "
              f"精率={agg['context_precision']:.3f} 召回={agg['context_recall']:.3f} "
              f"置信={agg['confidence']:.3f}")

    # 写报告
    lines = [
        "# RAGAS 评测报告",
        "",
        f"- 评测集：`samples/` 下小学（圆的周长教案/数学课标/分数论文）+ 高中（高一/高二/高三数学 + 物理/化学/语文/英语）语料，共 {len(sets)} 组",
        "- 管线：Hybrid 召回（BGE-M3 dense+sparse）→ BGE-Reranker 精排 → DeepSeek 生成",
        "- 裁判：DeepSeek（LLM-as-judge），指标对齐 RAGAS 定义",
        "",
        "## 聚合结果对比",
        "",
        "| 指标 | " + " | ".join(name for name in results) + " |",
        "|---|" + "---|" * len(results),
    ]
    for key, label in [
        ("faithfulness", "答案忠实度 faithfulness"),
        ("answer_relevancy", "答案相关性 answer_relevancy"),
        ("context_precision", "上下文精确率 context_precision"),
        ("context_recall", "上下文召回率 context_recall"),
        ("confidence", "检索置信度 confidence"),
    ]:
        lines.append("| " + label + " | " + " | ".join(f"{results[n][1][key]:.3f}" for n in results) + " |")
    lines.append("")
    for name, (r, _) in results.items():
        lines += [f"## {name}逐题明细", "", *_detail_lines(r), ""]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告已写入：{REPORT_PATH}")


# ══════════════════════════════════════════════════════════════
# 忠实度裁判校准（负样本判别力）：验证 LLM-as-judge 能否区分「有据」与「幻觉」
# ══════════════════════════════════════════════════════════════

CALIBRATION_SET = [
    {
        "name": "① 有据（应高分）",
        "contexts": ["圆的周长计算公式是 C = πd = 2πr，π 取近似值 3.14。"],
        "answer": "圆的周长等于 π 乘直径，即 C = πd，π 约等于 3.14。",
    },
    {
        "name": "② 幻觉（应低分）",
        "contexts": ["圆的周长计算公式是 C = πd = 2πr，π 取近似值 3.14。"],
        "answer": "圆的面积等于 π 乘以半径的平方，这个公式是祖冲之发现的。",
    },
    {
        "name": "③ 无关上下文（应低分）",
        "contexts": ["西湖醋鱼是杭州传统名菜，选用草鱼烹制，酸甜可口，广受欢迎。"],
        "answer": "圆的周长公式是 C = πd = 2πr，π 约等于 3.14。",
    },
    {
        "name": "④ 半有据半幻觉（应~0.5）",
        "contexts": ["圆的周长计算公式是 C = πd = 2πr，π 取近似值 3.14。"],
        "answer": "圆的周长公式是 C = πd，π 约等于 3.14；此外圆的面积公式 S = πr² 也是祖冲之推导的。",
    },
]


async def calibrate_faithfulness_judge():
    """跑 4 个已知答案的样例，验证忠实度裁判的判别力。"""
    print("忠实度裁判校准（负样本判别力）\n")
    scores = {}
    for case in CALIBRATION_SET:
        docs = [SimpleNamespace(content=c) for c in case["contexts"]]
        score = await faithfulness(case["name"], case["answer"], docs)
        scores[case["name"]] = score
        print(f"  {case['name']}: faithfulness = {score:.2f}")

    grounded = scores["① 有据（应高分）"]
    hallucinated = scores["② 幻觉（应低分）"]
    off_topic = scores["③ 无关上下文（应低分）"]
    partial = scores["④ 半有据半幻觉（应~0.5）"]

    gap1 = grounded - hallucinated
    gap2 = grounded - off_topic

    print("\n" + "=" * 60)
    print(" 判别力判定")
    print("=" * 60)
    print(f" 有据 vs 幻觉   分差 = {gap1:+.2f}  → {'✅ 能区分' if gap1 >= 0.3 else '❌ 区分力不足'}")
    print(f" 有据 vs 无关   分差 = {gap2:+.2f}  → {'✅ 能区分' if gap2 >= 0.3 else '❌ 区分力不足'}")
    print(f" 半有据半幻觉   （期望≈0.5，实测 {partial:.2f}）")
    print("\n结论：分差 ≥ 0.3 视为裁判可区分有据/幻觉，忠实度指标才可信；"
          "否则该指标需要收紧 prompt 或换裁判，全绿的 1.000 不可采信。")


if __name__ == "__main__":
    if "--calibrate" in sys.argv:
        asyncio.run(calibrate_faithfulness_judge())
    else:
        asyncio.run(main())
