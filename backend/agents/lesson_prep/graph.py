# backend/agents/lesson_prep/graph.py

from langgraph.graph import StateGraph, START, END

from backend.agents.lesson_prep.state import LessonPrepState
from backend.agents.lesson_prep.nodes import (
    requirement_parse_node,
    semantic_retrieve_node,
    objective_generate_node,
    key_point_analyze_node,
    process_generate_node,
    exercise_match_node,
    quality_check_node,
    lesson_plan_integrate_node,
    reflection_optimize_node,
    save_lesson_plan_node,
)
from backend.core.memory import get_checkpointer


# 反思优化可回炉的模块（revision_target 合法值）
_REVISION_TARGETS = {"objectives", "key_points", "process", "exercises"}


def _route_after_reflection(state: LessonPrepState) -> str:
    """反思优化后的路由：满意（空）→ save；提意见 → 回炉对应模块。"""
    target = state.get("revision_target", "")
    return target if target in _REVISION_TARGETS else "save"


def build_lesson_prep_graph():
    """
    构建并编译备课生产 Agent 的 LangGraph 状态图（10 节点）。

    拓扑：
        requirement_parse → semantic_retrieve → (objective_generate ∥ key_point_analyze)
        → process_generate → exercise_match → quality_check
        → lesson_plan_integrate → reflection_optimize [interrupt]
        → save → END

    反思优化回炉（条件边）：revision_target ∈ {objectives/key_points/process/exercises}
    时路由回对应节点（级联重跑下游），空则进 save（唯一出口）。
    """
    builder = StateGraph(LessonPrepState)

    # ── 注册节点 ──────────────────────────────────────────────
    builder.add_node("requirement_parse",      requirement_parse_node)
    builder.add_node("semantic_retrieve",      semantic_retrieve_node)
    builder.add_node("objective_generate",     objective_generate_node)
    builder.add_node("key_point_analyze",      key_point_analyze_node)
    builder.add_node("process_generate",       process_generate_node)
    builder.add_node("exercise_match",         exercise_match_node)
    builder.add_node("quality_check",          quality_check_node)
    builder.add_node("lesson_plan_integrate",  lesson_plan_integrate_node)
    builder.add_node("reflection_optimize",    reflection_optimize_node)
    builder.add_node("save_lesson_plan",       save_lesson_plan_node)

    # ── 线性边 ────────────────────────────────────────────────
    builder.add_edge(START, "requirement_parse")
    builder.add_edge("requirement_parse", "semantic_retrieve")

    # ── 并行：semantic_retrieve → (objective_generate ∥ key_point_analyze) → process_generate ──
    builder.add_edge("semantic_retrieve", "objective_generate")
    builder.add_edge("semantic_retrieve", "key_point_analyze")
    builder.add_edge("objective_generate", "process_generate")
    builder.add_edge("key_point_analyze", "process_generate")

    # ── 线性继续 ──────────────────────────────────────────────
    builder.add_edge("process_generate", "exercise_match")
    builder.add_edge("exercise_match", "quality_check")
    builder.add_edge("quality_check", "lesson_plan_integrate")
    builder.add_edge("lesson_plan_integrate", "reflection_optimize")

    # ── 反思优化条件路由（回炉 / save）─────────────────────────
    builder.add_conditional_edges(
        "reflection_optimize",
        _route_after_reflection,
        {
            "save":       "save_lesson_plan",
            "objectives": "objective_generate",
            "key_points": "key_point_analyze",
            "process":    "process_generate",
            "exercises":  "exercise_match",
        },
    )

    builder.add_edge("save_lesson_plan", END)

    # ── 编译（绑定独立 MemorySaver，与 exam/qa 隔离）─────────
    checkpointer = get_checkpointer("lesson_prep")
    return builder.compile(checkpointer=checkpointer)
