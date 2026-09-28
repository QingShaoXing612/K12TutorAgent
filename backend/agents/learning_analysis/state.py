# backend/agents/learning_analysis/state.py

from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class LearningAnalysisState(TypedDict):
    """
    学情分析 Agent 的完整状态定义（10 节点拓扑）。

    链路：
      需求解析 → 学情数据拉取(含归一化) → (掌握度计算 ∥ 典型错题分析)
        → 学生分层画像 → 干预策略检索 → 教学建议生成
        → 报告整合 → 报告质检 → 持久化

    对比备课：无 interrupt/回炉（直线生成→落库），掌握度为确定性计算（LLM 不碰数字）。
    """

    # ── ① 消息历史（LangGraph 核心，add_messages reducer）────────
    messages: Annotated[list[BaseMessage], add_messages]

    # ── ② 请求上下文（API 层注入，节点只读）───────────────────────
    teacher_id: str            # 教师 ID（学情报告是教师行为）
    tenant_id:  str            # 租户 ID（DB 多租户隔离）
    session_id: str            # 会话 ID（用于构造 thread_id）

    # ── ③ 报告槽位（需求解析输入，含教师原始需求透传）────────────
    subject:             str    # 科目，如「数学」
    grade:               str    # 年级，如「六年级」
    class_id:            str    # 班级 ID（MVP 当不透明分组键，对齐 users.class_id）
    report_type:         str    # 报告类型，默认 class（班级学情）；预留 student（个人）
    template:            str    # 报告模板：primary(小学)/junior(初中)/senior(高中)，默认按 grade 推断
    data_source:         str    # 数据源：practice(日常练习) / exam(考试批改)
    time_range:          dict   # 数据时间范围 {start, end}，空 = 全部历史
    exam_ids:            list[str]  # 考试批次（data_source=exam 时用）
    knowledge_scope:     str    # 分析知识点范围（自由文本，如「分数乘除」/「第一单元」；空=不限，节点2收敛）
    teacher_requirement: str    # 教师原始自由文本需求（透传，不吞风格信息）

    # ── ④ 需求解析产物 ─────────────────────────────────────────
    analysis_scope: dict   # 解析后的范围（时间范围归一化 / 聚焦知识点 / 报告侧重点）

    # ── ⑤ 学情数据拉取产物（含归一化）──────────────────────────
    fetched_records: list[dict]  # 归一化答题记录（student×kp×is_correct/score，来源 practice/exam）
    roster:          list[dict]  # 班级花名册（student_id / username）
    unmapped_tags:   list[str]   # 归一化未匹配的自由文本 knowledge_tag（降级「未分类」）
    data_summary:    dict        # 拉取统计（记录数/学生数/覆盖知识点数/来源分布），供质检比对

    # ── ⑥ 知识点掌握度计算产物（确定性规则，LLM 不碰数字）────────
    kp_mastery: list[dict]  # {kp_id, kp_name, mastery_score, correct_count, total_count,
                            #  difficulty, requirement_level, level}

    # ── ⑦ 典型错题分析产物 ─────────────────────────────────────
    typical_errors: list[dict]  # {exercise_id, kp_id, kp_name, error_rate, error_count,
                                #  error_type, student_ids}

    # ── ⑧ 学生分层画像产物 ─────────────────────────────────────
    stratification: list[dict]  # {level, level_name, student_ids, count, profile}

    # ── ⑧b 学生个体画像（学生×kp，样本量门槛；支撑节点7 个别指导）──
    student_profiles: list[dict]  # {student_id, username, avg_rate, level, total_count,
                                  #  weak_kps[], strong_kps[], insufficient_kps[]}

    # ── ⑨ 干预策略检索产物 ─────────────────────────────────────
    interventions: list[dict]   # {strategy_id, strategy_content, strat_level, error_type, matched_reason,
                                #  source_lesson_id(可空), lesson_topic(可空)}（seam③ 教案参考软信号）

    # ── ⑩ 教学建议生成产物 ─────────────────────────────────────
    teaching_suggestions: dict        # {分层建议, 错题补救, 后续教学计划}
    weak_kps:            list[dict]   # 结构化薄弱知识点（seam①：支撑一键备课，
                                      # 含 kp_id/kp_name/mastery_score/建议）

    # ── ⑪ 报告整合产物 ─────────────────────────────────────────
    report_content: dict   # 结构化报告（meta/班级总览/掌握度分布/分层画像/典型错题/教学建议）

    # ── ⑫ 报告质检产物 ─────────────────────────────────────────
    validation: dict   # {passed, issues[], fixed_count}（数字一致性自检）

    # ── ⑬ 持久化产物 ──────────────────────────────────────────
    metrics:         dict   # 中间结果汇总（掌握度/分层/错题），落 learning_reports.metrics JSONB
    saved_report_id: str    # 落库成功的报告 report_id
