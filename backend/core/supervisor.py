# backend/core/supervisor.py
# Supervisor 主状态机：LangGraph supervisor 图
#
# 结构（经典 supervisor 模式）：
#   START → supervisor ──next──> qa / exam / lesson_prep / learning_analysis / FINISH
#             ▲  └────────────────────────────────────────────┘
#             └──────────── worker 执行完回到 supervisor 再决策（循环，直到 FINISH）
#
# - supervisor 节点：LLM 根据对话历史决定下一步调用哪个 Agent
# - qa：真挂 subgraph（文本问答可直连）
# - exam / lesson_prep / learning_analysis：引导节点（返回 guidance，因需上传文件/填表单）
# - FINISH：结束
#
# 轻量 orchestrator（orchestrator.py）仍保留：负责「Agent 图懒加载缓存」，
# 本 supervisor 是「顶层调度」，两者职责不同（调度 vs 注册表）。

from __future__ import annotations

import json
from typing import Annotated, Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

from backend.core.llm_factory import get_llm
from backend.core.memory import get_checkpointer
from backend.core.logger import get_logger

logger = get_logger(__name__)


# ── Supervisor 状态（QAState 超集 + supervisor 决策字段）───────
# qa subgraph 读写 QAState 的全部字段，故这些字段必须出现在父图 State 里，
# 才能把 qa 图作为子图直接挂载（字段兼容）。
class SupervisorState(TypedDict):
    # ── 共享 ─────────────────────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]
    next: str                                    # supervisor 决策：qa/exam/lesson_prep/learning_analysis/pipeline/clarify/finish
    reason: str                                  # supervisor 决策依据（一句话，前端路由卡片展示）

    # ── QA 上下文字段（对齐 QAState，qa subgraph 需要）────────
    student_id:  str
    tenant_id:   str
    session_id:  str
    course_id:   Optional[str]
    original_query:    str
    query_type:        str
    rewritten_queries: list[str]
    hyde_document:     Optional[str]
    ranked_chunks:     list[dict]
    confidence:        float
    is_high_confidence: bool
    web_search_results: list[dict]
    answer:            str
    sources:           list[str]
    answer_mode:       str
    existing_summary:  Optional[str]
    should_summarize:  bool
    enable_web_search: bool
    fallback_used:     bool
    structured_output: Optional[dict]

    # ── supervisor 输出 ──────────────────────────────────────
    guidance: dict                               # 引导信息（exam/lesson_prep/learning_analysis/clarify 节点返回）
    pipeline_plan: dict                          # multi_agent 协同计划（pipeline 节点返回）


# ── supervisor 决策 prompt ──────────────────────────────────────
_SUPERVISOR_PROMPT = """你是 TeachMate 多 Agent 系统的 supervisor，根据对话历史决定下一步调用哪个 Agent。

可选 next（严格只输出 JSON，不要其他内容）：
- qa：用户直接提问学科知识，需要问答 Agent 回答
- exam：用户要批改试卷/作业（需上传 Word 答卷）
- lesson_prep：用户要备课
- learning_analysis：用户要出学情报告
- pipeline：综合需求（同时涉及学情报告 + 备课，如「根据学情报告备课」）
- clarify：意图不明确，无法判断，需要追问
- finish：已经完成回答或引导，无需再调用任何 Agent

返回格式：{{"next": "<上表之一>", "reason": "<一句话判断依据>"}}

对话历史：
{messages}

返回："""

_VALID_NEXT = frozenset({"qa", "exam", "lesson_prep", "learning_analysis", "pipeline", "clarify", "finish"})


def _extract_text(content) -> str:
    """兼容地把 message.content 转纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    return str(content)


def _fmt_messages(messages: list[BaseMessage]) -> str:
    """把消息历史拼成给 LLM 看的文本（截断每条，控制 prompt 长度）。"""
    lines = []
    for m in messages:
        role = "用户" if isinstance(m, HumanMessage) else "AI"
        lines.append(f"{role}：{_extract_text(m.content)[:200]}")
    return "\n".join(lines)


async def supervisor_node(state: SupervisorState) -> dict:
    """supervisor 决策节点：LLM 根据对话历史决定 next。异常时保守返回 finish。

    收敛保护：若最后一条消息是 AI 消息（qa 已回答 / 引导已返回），确定性返回 finish，
    不再调 LLM——避免「supervisor → worker → supervisor」循环不收敛导致重复回答。
    """
    messages = state.get("messages", [])
    if messages and not isinstance(messages[-1], HumanMessage):
        logger.info("supervisor.finish", reason="last_message_is_ai")
        return {"next": "finish", "reason": "已由 Agent 完成处理"}

    next_label = "finish"
    reason = "路由判断异常，默认结束"
    try:
        llm = get_llm("intent", temperature=0)
        resp = await llm.ainvoke([
            HumanMessage(content=_SUPERVISOR_PROMPT.format(
                messages=_fmt_messages(messages)
            ))
        ])
        raw = _extract_text(resp.content).strip()
        parsed = json.loads(raw)
        next_label = parsed.get("next", "finish").strip().lower()
        reason = parsed.get("reason", "LLM 路由判断")
        if next_label not in _VALID_NEXT:
            logger.warning("supervisor.unknown_next", next=next_label, fallback="finish")
            next_label = "finish"
    except Exception as e:
        logger.warning("supervisor.decide_failed", error=str(e), fallback="finish")

    logger.info("supervisor.decide", next=next_label, reason=reason)
    return {"next": next_label, "reason": reason}


# ── 引导节点 ────────────────────────────────────────────────────
# exam / lesson_prep / learning_analysis 需要专门页面（上传文件/填表单），
# 不在对话里直接执行，而是返回引导卡片让前端跳转。

_GUIDANCE = {
    "exam": {
        "message": "检测到您需要批改试卷/作业。请前往「试卷批改」页面上传 Word 格式答卷，AI 将自动完成批改并分析知识薄弱点。",
        "action_label": "前往试卷批改",
        "action_url": "/exam",
    },
    "lesson_prep": {
        "message": "检测到您需要备课。请前往「智能备课」页面填写科目、年级、章节主题，AI 将生成教案与习题，并支持反思回炉优化。",
        "action_label": "前往智能备课",
        "action_url": "/lesson-prep",
    },
    "learning_analysis": {
        "message": "检测到您需要生成学情报告。请前往「学情报告」页面选择班级、科目与时间范围，AI 将分析班级掌握度、分层画像并给出教学建议。",
        "action_label": "前往学情报告",
        "action_url": "/learning-analysis",
    },
}


def _make_guidance_node(agent_type: str):
    """生成引导节点：返回 guidance + 追加 AI 消息（让 supervisor 收敛保护生效）。"""
    guidance = _GUIDANCE[agent_type]

    async def node(state: SupervisorState) -> dict:
        return {
            "guidance": guidance,
            "messages": [AIMessage(content=guidance["message"])],
        }

    return node


# ── pipeline / clarify 节点 ─────────────────────────────────────
_PIPELINE_PLAN = {
    "title": "学情报告 → 一键备课",
    "intro": "已为您规划「学情报告 → 一键备课」链路，建议先出学情报告，再基于报告的薄弱点一键生成教案。",
    "steps": [
        {
            "step": 1,
            "agent_type": "learning_analysis",
            "label": "学情报告",
            "desc": "选择班级、科目与时间范围，AI 分析班级掌握度、分层画像与薄弱点",
            "action_label": "生成学情报告",
            "action_url": "/learning-analysis",
            "tip": "报告会输出结构化薄弱点，供下一步备课引用",
        },
        {
            "step": 2,
            "agent_type": "lesson_prep",
            "label": "智能备课",
            "desc": "基于学情报告的薄弱点一键生成教案（自动预填重难点与补充需求）",
            "action_label": "一键备课",
            "action_url": "/lesson-prep",
            "tip": "在学情报告页点「一键备课」即可自动带入薄弱点",
        },
    ],
}

_CLARIFY_GUIDANCE = {
    "message": "您的问题我还不太确定应该用哪个功能来帮您，能否描述得更具体一些？"
               "例如：您是想提问学科知识、批改试卷、备课，还是出学情报告？",
    "action_label": "",
    "action_url": "",
}


async def pipeline_node(state: SupervisorState) -> dict:
    """multi_agent 综合需求 → 返回协同计划 + AI 消息触发收敛保护。"""
    return {
        "pipeline_plan": _PIPELINE_PLAN,
        "messages": [AIMessage(content=_PIPELINE_PLAN["title"])],
    }


async def clarify_node(state: SupervisorState) -> dict:
    """意图不明 → 返回追问引导 + AI 消息触发收敛保护。"""
    return {
        "guidance": _CLARIFY_GUIDANCE,
        "messages": [AIMessage(content=_CLARIFY_GUIDANCE["message"])],
    }


# ── 图构建 ──────────────────────────────────────────────────────
def _route_by_next(state: SupervisorState) -> str:
    """supervisor 条件边：按 next 字段路由。"""
    return state.get("next", "finish")


def build_supervisor_graph():
    """构建并编译 supervisor 主状态机。"""
    builder = StateGraph(SupervisorState)

    # 注册节点
    builder.add_node("supervisor", supervisor_node)

    # qa：真挂 subgraph（复用 build_qa_graph，字段兼容）
    from backend.agents.qa.graph import build_qa_graph
    builder.add_node("qa", build_qa_graph())

    # 其余三个 agent：引导节点
    builder.add_node("exam", _make_guidance_node("exam"))
    builder.add_node("lesson_prep", _make_guidance_node("lesson_prep"))
    builder.add_node("learning_analysis", _make_guidance_node("learning_analysis"))

    # multi_agent 协同计划 / 意图不明追问
    builder.add_node("pipeline", pipeline_node)
    builder.add_node("clarify", clarify_node)

    # 入口
    builder.add_edge(START, "supervisor")

    # supervisor 条件边
    builder.add_conditional_edges(
        "supervisor",
        _route_by_next,
        {
            "qa": "qa",
            "exam": "exam",
            "lesson_prep": "lesson_prep",
            "learning_analysis": "learning_analysis",
            "pipeline": "pipeline",
            "clarify": "clarify",
            "finish": END,
        },
    )

    # worker 执行完回到 supervisor（循环决策，直到 FINISH）
    builder.add_edge("qa", "supervisor")
    builder.add_edge("exam", "supervisor")
    builder.add_edge("lesson_prep", "supervisor")
    builder.add_edge("learning_analysis", "supervisor")
    builder.add_edge("pipeline", "supervisor")
    builder.add_edge("clarify", "supervisor")

    checkpointer = get_checkpointer("supervisor")
    return builder.compile(checkpointer=checkpointer)


# ── 模块级单例：避免每次请求重复编译 supervisor 图（含 qa 子图编译，开销大）──
_supervisor_graph = None


def get_supervisor_graph():
    """懒加载并缓存 supervisor 主状态机（应用生命周期内复用）。"""
    global _supervisor_graph
    if _supervisor_graph is None:
        _supervisor_graph = build_supervisor_graph()
        logger.info("supervisor.graph_loaded")
    return _supervisor_graph
