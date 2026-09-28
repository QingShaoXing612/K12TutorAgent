# backend/agents/lesson_prep/state.py

from typing import Annotated, Optional
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class LessonPrepState(TypedDict):
    """
    备课生产 Agent 的完整状态定义（10 节点拓扑）。

    链路：
      需求解析 → 语义检索 → (教学目标 ∥ 重点难点) → 教学流程
        → 习题匹配 → 逐题质检 → 教案整合 → 反思优化(回炉) → save
    """

    # ── ① 消息历史（LangGraph 核心，add_messages reducer）────────
    messages: Annotated[list[BaseMessage], add_messages]

    # ── ② 请求上下文（API 层注入，节点只读）───────────────────────
    teacher_id: str            # 教师 ID（备课是教师行为）
    tenant_id:  str            # 租户 ID（Milvus / DB 多租户隔离）
    session_id: str            # 会话 ID（用于构造 thread_id）
    course_id:  Optional[str]  # 课程 ID；备课按 subject+grade 组织，此字段保留但非检索主维度

    # ── ③ 备课槽位（需求解析输入，含教师原始需求透传）────────────
    subject:            str    # 科目，如「数学」
    grade:              str    # 年级，如「六年级」
    topic:              str    # 章节/知识点主题，如「圆的周长与面积」
    lesson_type:        str    # 课型：new(新授课)/exercise(习题课)/review(复习课)，默认 new
    duration:           int    # 课时（分钟），默认 40
    teacher_requirement: str   # 教师原始自由文本需求（透传，不吞风格信息）

    # ── ④ 需求解析产物 ─────────────────────────────────────────
    knowledge_points:  list[dict]  # 结构化知识点（kp_id/name/difficulty_level/requirement_level/父路径）
    retrieval_queries: list[str]   # 优化后的检索 query 列表（供语义检索 Milvus 多路召回）

    # ── ⑤ 语义检索产物 ─────────────────────────────────────────
    retrieved_resources: dict       # 按 6 组分：reference_lesson_plans/curriculums/papers/materials/lesson_templates/history_lesson_plans
    exercise_candidates: list[dict]  # 配套习题候选（PG exercise_bank 按 subject+grade+知识点筛出）

    # ── ⑥ 教学目标产物 ─────────────────────────────────────────
    teaching_objectives: dict   # {knowledge_skills, process_methods, emotion_values} 三维目标

    # ── ⑦ 重点难点产物 ─────────────────────────────────────────
    key_difficult_points: dict  # {key_points, difficult_points}

    # ── ⑧ 教学流程产物 ─────────────────────────────────────────
    teaching_process: dict      # {steps: [{name, duration_min, teacher_activity, student_activity, design_intent}]}

    # ── ⑨ 习题匹配产物 ─────────────────────────────────────────
    selected_exercises: dict    # {class_exercises:[{exercise_id,reason}], homework_exercises:[...]}

    # ── ⑩ 逐题质检产物 ─────────────────────────────────────────
    quality_reports:  list[dict]  # 每题适配性质检结果
    reselect_count:   int         # 坏题重新匹配/兜底生成轮数（上限由 graph 控制）

    # ── ⑪ 教案整合产物 ─────────────────────────────────────────
    final_lesson_plan: dict      # 结构化完整教案（meta/objectives/key_points/difficult_points/process/board/exercises/reflection）

    # ── ⑫ 反思优化产物 ─────────────────────────────────────────
    teacher_feedback: str   # 教师修改意见（反思优化节点注入）
    revision_target:  str   # 回炉目标节点（空 = 满意，进 save）

    # ── ⑬ 落库产物 ────────────────────────────────────────────
    saved_lesson_id:     str       # 落库成功的教案 lesson_id
    saved_relation_ids:  list[str]  # 关联表写入的 id（lesson_knowledge_rel/lesson_exercise_rel）
