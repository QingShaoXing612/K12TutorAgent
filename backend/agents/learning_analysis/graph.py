# backend/agents/learning_analysis/graph.py

from langgraph.graph import StateGraph, START, END

from backend.agents.learning_analysis.state import LearningAnalysisState
from backend.agents.learning_analysis.nodes import (
    requirement_parse_node,
    data_fetch_node,
    mastery_calc_node,
    typical_error_node,
    stratify_node,
    intervention_retrieval_node,
    suggestion_gen_node,
    report_integration_node,
    report_validation_node,
    persistence_node,
)
from backend.core.memory import get_checkpointer


def build_learning_analysis_graph():
    """
    构建并编译学情分析 Agent 的 LangGraph 状态图（10 节点）。

    拓扑：
        requirement_parse → data_fetch → (mastery_calc ∥ typical_error)
        → stratify → intervention_retrieval → suggestion_gen
        → report_integration → report_validation → persistence → END

    直线生成，无 interrupt/回炉（对比备课的反思优化）；唯一写入点是 persistence，
    质检不过则不落库（persistence 内部闸门）。
    """
    builder = StateGraph(LearningAnalysisState)

    # ── 注册节点 ──────────────────────────────────────────────
    builder.add_node("requirement_parse",      requirement_parse_node)
    builder.add_node("data_fetch",             data_fetch_node)
    builder.add_node("mastery_calc",           mastery_calc_node)
    builder.add_node("typical_error",          typical_error_node)
    builder.add_node("stratify",               stratify_node)
    builder.add_node("intervention_retrieval", intervention_retrieval_node)
    builder.add_node("suggestion_gen",         suggestion_gen_node)
    builder.add_node("report_integration",     report_integration_node)
    builder.add_node("report_validation",      report_validation_node)
    builder.add_node("persistence",            persistence_node)

    # ── 线性边 ────────────────────────────────────────────────
    builder.add_edge(START, "requirement_parse")
    builder.add_edge("requirement_parse", "data_fetch")

    # ── 并行：data_fetch → (mastery_calc ∥ typical_error) → stratify ──
    builder.add_edge("data_fetch", "mastery_calc")
    builder.add_edge("data_fetch", "typical_error")
    builder.add_edge("mastery_calc", "stratify")
    builder.add_edge("typical_error", "stratify")

    # ── 线性继续 ──────────────────────────────────────────────
    builder.add_edge("stratify", "intervention_retrieval")
    builder.add_edge("intervention_retrieval", "suggestion_gen")
    builder.add_edge("suggestion_gen", "report_integration")
    builder.add_edge("report_integration", "report_validation")
    builder.add_edge("report_validation", "persistence")

    builder.add_edge("persistence", END)

    # ── 编译（独立 MemorySaver，与 exam/qa/lesson_prep 隔离）──
    checkpointer = get_checkpointer("learning_analysis")
    return builder.compile(checkpointer=checkpointer)
