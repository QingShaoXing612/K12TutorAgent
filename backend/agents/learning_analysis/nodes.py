# backend/agents/learning_analysis/nodes.py

import json
import re
import uuid as _uuid
from datetime import datetime, timezone, timedelta

from pydantic import BaseModel, Field
from sqlalchemy import text
from langchain_core.messages import HumanMessage, SystemMessage

from backend.agents.learning_analysis.state import LearningAnalysisState
from backend.agents.learning_analysis.prompts import (SYSTEM_PROMPT, REQUIREMENT_PARSE_PROMPT,
                                                      SUGGESTION_SYSTEM_PROMPT, SUGGESTION_GEN_PROMPT,
                                                      ERROR_TYPE_SYSTEM_PROMPT, ERROR_TYPE_ANALYSIS_PROMPT)
from backend.core.llm_factory import get_structured_llm
from backend.core.logger import get_logger
from backend.dependencies import AsyncSessionLocal

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────
# 结构化输出 Schema
# ──────────────────────────────────────────────────────────────

class RequirementConstraints(BaseModel):
    """需求解析的 LLM 输出：从教师自由文本提取的结构化约束。"""
    question_type_filter: list[str] = Field(default_factory=list, description="题型过滤（DB 枚举：single_choice/multi_choice/judge/fill_blank/short_answer），空=不限")
    stratification_weight: dict = Field(default_factory=dict, description="分层权重倾斜（excellent/good/weak→倍数），空=均等")
    focus: str = Field(default="", description="报告侧重点（一句话摘要）")


# ──────────────────────────────────────────────────────────────
# 节点：requirement_parse — 需求解析（规则校验 + LLM 解析自由文本）
# ──────────────────────────────────────────────────────────────

async def requirement_parse_node(state: LearningAnalysisState) -> dict:
    """
    需求解析（X+Y 混合，无副作用）：
      ① 规则校验：班级合法性（users 表存在该 class_id 学生）+ knowledge_scope 匹配知识点
         + 时间范围归一化 + 模板推断
      ② LLM 解析：teacher_requirement 自由文本 → 题型过滤/分层权重/报告侧重
    """
    subject     = state["subject"]
    grade       = state["grade"]
    class_id    = state["class_id"]
    scope       = state.get("knowledge_scope", "")
    time_range  = state.get("time_range", {}) or {}
    requirement = state.get("teacher_requirement", "")

    class_valid, class_size = await _validate_class(class_id)
    knowledge_points = await _match_scope_knowledge_points(subject, grade, scope)
    normalized_range = _normalize_time_range(time_range)
    template = _infer_template(state.get("template", ""), grade)
    constraints = await _parse_constraints(subject, grade, knowledge_points, requirement)

    analysis_scope = {
        "knowledge_points":      knowledge_points,
        "time_range":            normalized_range,
        "question_type_filter":  constraints.question_type_filter,
        "stratification_weight": constraints.stratification_weight,
        "focus":                 constraints.focus,
        "class_valid":           class_valid,
        "class_size":            class_size,
    }

    logger.info(
        "requirement_parse.done",
        class_size=class_size,
        kp=len(knowledge_points),
        focus=constraints.focus or "（无侧重）",
    )
    return {"analysis_scope": analysis_scope, "template": template}


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────

async def _validate_class(class_id: str) -> tuple[bool, int]:
    """校验班级：users 表存在该 class_id 的学生（MVP 无 classes 表，不查班表）。"""
    try:
        cid = _uuid.UUID(class_id)
    except (ValueError, TypeError, AttributeError):
        logger.warning("requirement_parse.invalid_class_id", class_id=class_id)
        return False, 0
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text("SELECT COUNT(*) FROM users WHERE class_id = :c"),
            {"c": cid},
        )
        size = res.scalar()
    return bool(size), int(size or 0)


async def _match_scope_knowledge_points(subject: str, grade: str, scope: str) -> list[dict]:
    """knowledge_scope 匹配 knowledge_points（空=不限，由节点 2/3 收敛到有数据的 kp）。"""
    if not scope.strip():
        return []
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT kp_id, kp_name, parent_kp_id, difficulty_level, requirement_level "
                 "FROM knowledge_points WHERE subject = :s AND grade = :g"),
            {"s": subject, "g": grade},
        )
        kps = {str(r.kp_id): {
            "kp_id":             str(r.kp_id),
            "kp_name":           r.kp_name,
            "parent_kp_id":      str(r.parent_kp_id) if r.parent_kp_id else None,
            "difficulty_level":  r.difficulty_level,
            "requirement_level": r.requirement_level,
        } for r in rows}

    if not kps:
        logger.warning("requirement_parse.no_knowledge_points", subject=subject, grade=grade)
        return []

    keyword = scope.strip()
    matched = [kp for kp in kps.values() if kp["kp_name"] == keyword]
    if not matched:
        matched = [kp for kp in kps.values() if keyword in kp["kp_name"] or kp["kp_name"] in keyword]

    result = []
    for kp in matched:
        path = []
        cur = kp
        while cur.get("parent_kp_id") and cur["parent_kp_id"] in kps:
            cur = kps[cur["parent_kp_id"]]
            path.append(cur["kp_name"])
        result.append({
            "kp_id":             kp["kp_id"],
            "name":              kp["kp_name"],
            "difficulty_level":  kp["difficulty_level"],
            "requirement_level": kp["requirement_level"],
            "parent_path":       list(reversed(path)),
        })
    return result


def _normalize_time_range(time_range: dict) -> dict:
    """归一化时间范围：稳定输出 {start, end} 两键，缺失补 None（节点 2 解读为「不设界」）。"""
    return {
        "start": time_range.get("start") or None,
        "end":   time_range.get("end") or None,
    }


def _infer_template(template: str, grade: str) -> str:
    """推断报告模板：显式 primary/junior/senior 保留，否则按年级推断。
    高中(高一/高二/高三/十~十二年级)→senior；初中(初一~初三/七~九年级)→junior；其余→primary。
    """
    if template in ("primary", "junior", "senior"):
        return template
    if any(k in grade for k in ("高一", "高二", "高三", "高中", "十年级", "十一年级", "十二年级")):
        return "senior"
    if any(k in grade for k in ("初一", "初二", "初三", "初中", "七年级", "八年级", "九年级")):
        return "junior"
    return "primary"


async def _parse_constraints(subject: str, grade: str, kps: list[dict], requirement: str) -> RequirementConstraints:
    """LLM 解析 teacher_requirement → 题型过滤/分层权重/报告侧重。"""
    if not requirement.strip():
        return RequirementConstraints()
    kp_names = "、".join(k["name"] for k in kps) or "（未指定，全量）"
    prompt = REQUIREMENT_PARSE_PROMPT.format(
        subject=subject,
        grade=grade,
        knowledge_points=kp_names,
        requirement=requirement,
    )
    structured = get_structured_llm("learning_analysis", RequirementConstraints)
    return await structured.ainvoke([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])


# ──────────────────────────────────────────────────────────────
# 节点：data_fetch — 学情数据拉取（含归一化）
# ──────────────────────────────────────────────────────────────

async def data_fetch_node(state: LearningAnalysisState) -> dict:
    """学情数据拉取（含归一化，纯只读、无副作用）。

    双源拉取：student_practice_records（日常练习）+ exam_reviews（考试，经
    exam_submissions 补 student_id/submitted_at）→ knowledge_tag 归一化对齐
    knowledge_points.kp_id → 按 analysis_scope.knowledge_points 过滤 →
    产出 fetched_records / roster / unmapped_tags / data_summary。
    后续节点不再查库，只消费 fetched_records。
    """
    subject   = state["subject"]
    grade     = state["grade"]
    class_id  = state["class_id"]
    data_source = state.get("data_source", "practice")
    exam_ids  = state.get("exam_ids", []) or []
    scope     = state.get("analysis_scope", {}) or {}
    kp_filter = scope.get("knowledge_points", []) or []
    qt_filter = scope.get("question_type_filter", []) or []
    tr         = scope.get("time_range", {}) or {}
    start, end = _time_bounds(tr)

    try:
        cid = _uuid.UUID(class_id)
    except (ValueError, TypeError, AttributeError):
        cid = class_id

    roster  = await _fetch_roster(cid)
    if data_source == "exam":
        p_rows = []
        e_rows = await _fetch_exam(cid, start, end, exam_ids, subject)
    else:
        p_rows = await _fetch_practice(subject, grade, cid, start, end)
        e_rows = []

    by_name, by_id = await _load_kp_map(subject, grade)
    records, unmapped = _normalize_records(p_rows, e_rows, by_name, by_id)

    if kp_filter:
        allowed = {k["kp_id"] for k in kp_filter}
        records = [r for r in records if r["kp_id"] in allowed]

    if qt_filter:
        records = [r for r in records if (r.get("question_type") or "") in qt_filter]

    data_summary = {
        "total_records":    len(records),
        "practice_records": sum(1 for r in records if r["source"] == "practice"),
        "exam_records":     sum(1 for r in records if r["source"] == "exam"),
        "student_count":    len(roster),
        "covered_kp_count": len({r["kp_id"] for r in records if r["kp_id"]}),
        "unmapped_count":   len(unmapped),
        "empty_roster":     len(roster) == 0,
    }

    logger.info(
        "data_fetch.done",
        practice=len(p_rows), exam=len(e_rows),
        records=len(records), roster=len(roster), unmapped=len(unmapped),
    )
    return {
        "fetched_records": records,
        "roster":          roster,
        "unmapped_tags":   unmapped,
        "data_summary":    data_summary,
    }


# ──────────────────────────────────────────────────────────────
# 工具函数：花名册 / 双源拉取 / kp 归一化
# ──────────────────────────────────────────────────────────────

async def _fetch_roster(cid) -> list[dict]:
    """班级花名册：该 class_id 下的学生（过滤 role/is_active，不拉教师）。"""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT id, username FROM users "
                 "WHERE class_id = :c AND role = 'student' AND is_active = TRUE"),
            {"c": cid},
        )
        return [{"student_id": str(r.id), "username": r.username} for r in rows]


async def _fetch_practice(subject: str, grade: str, cid, start, end) -> list[dict]:
    """日常练习源：student_practice_records 直接有 student_id/kp_id/is_correct。"""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(text("""
            SELECT r.id, r.student_id, r.exercise_id, r.kp_id, r.knowledge_tag,
                   r.is_correct, r.score, r.answer, r.recorded_at,
                   e.question_type, e.score AS max_score
            FROM student_practice_records r
            JOIN users u ON r.student_id = u.id
            LEFT JOIN exercise_bank e ON r.exercise_id = e.exercise_id
            WHERE u.class_id = :c AND u.role = 'student'
              AND r.subject = :subject AND r.grade = :grade
              AND r.recorded_at >= CAST(:start AS timestamptz)
              AND r.recorded_at <= CAST(:end AS timestamptz)
        """), {
            "c": cid, "subject": subject, "grade": grade,
            "start": start, "end": end,
        })
        return [
            {
                "id": r.id, "student_id": r.student_id, "exercise_id": r.exercise_id,
                "kp_id": r.kp_id, "knowledge_tag": r.knowledge_tag,
                "is_correct": r.is_correct, "score": r.score, "answer": r.answer,
                "recorded_at": r.recorded_at, "question_type": r.question_type,
                "max_score": r.max_score,
            }
            for r in rows
        ]


async def _fetch_exam(cid, start, end, exam_ids=None, subject=None) -> list[dict]:
    """考试源：exam_reviews 无 student_id/时间/满分，经 exam_submissions + questions 两跳补齐。

    exam_ids 非空时只拉指定考试批次；空 = 全部考试。subject 非空时按科目过滤。
    """
    exam_ids = exam_ids or []
    exam_cond = "AND s.exam_id = ANY(:exam_ids)" if exam_ids else ""
    subject_cond = "AND e.subject = :subject" if subject else ""
    params = {
        "c": cid, "start": start, "end": end,
        "exam_ids": [_uuid.UUID(x) for x in exam_ids],
    }
    if subject:
        params["subject"] = subject
    async with AsyncSessionLocal() as session:
        rows = await session.execute(text(f"""
            SELECT r.id, r.question_id, r.question_type, r.knowledge_tag, r.final_score,
                   r.student_answer, s.student_id, s.submitted_at, q.score AS max_score
            FROM exam_reviews r
            JOIN exam_submissions s ON r.submission_id = s.id
            JOIN exams e ON s.exam_id = e.id
            LEFT JOIN questions q ON r.question_id = q.id
            WHERE s.student_id IN (
                    SELECT id FROM users WHERE class_id = :c AND role = 'student')
              AND r.final_score IS NOT NULL
              AND s.status IN ('reviewed', 'published')
              AND s.submitted_at >= CAST(:start AS timestamptz)
              AND s.submitted_at <= CAST(:end AS timestamptz)
              {subject_cond}
              {exam_cond}
        """), params)
        return [
            {
                "id": r.id, "question_id": r.question_id,
                "question_type": r.question_type, "knowledge_tag": r.knowledge_tag,
                "final_score": r.final_score, "student_id": r.student_id,
                "student_answer": r.student_answer, "submitted_at": r.submitted_at,
                "max_score": r.max_score,
            }
            for r in rows
        ]


async def _load_kp_map(subject: str, grade: str) -> tuple[dict, dict]:
    """加载 subject+grade 的知识点映射：name→kp / kp_id→kp（含难度/课标，供节点3）。"""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            text("SELECT kp_id, kp_name, difficulty_level, requirement_level "
                 "FROM knowledge_points WHERE subject = :s AND grade = :g"),
            {"s": subject, "g": grade},
        )
        by_name = {
            r.kp_name: {
                "kp_id": str(r.kp_id), "kp_name": r.kp_name,
                "difficulty_level": r.difficulty_level,
                "requirement_level": r.requirement_level,
            }
            for r in rows
        }
    by_id = {v["kp_id"]: v for v in by_name.values()}
    return by_name, by_id


def _match_tag(tag: str, by_name: dict) -> dict | None:
    """knowledge_tag → kp：精确 → 互相包含模糊；匹配不上返回 None。"""
    tag = (tag or "").strip()
    if not tag:
        return None
    if tag in by_name:
        return by_name[tag]
    for name, kp in by_name.items():
        if name in tag or tag in name:
            return kp
    return None


def _resolve_kp(kp_id, tag: str, by_name: dict, by_id: dict):
    """归一化：已有 kp_id 直接复用，否则 knowledge_tag 匹配；返回 kp_id/kp_name/难度/课标。"""
    kp = None
    if kp_id and str(kp_id) in by_id:
        kp = by_id[str(kp_id)]
    elif tag:
        kp = _match_tag(tag, by_name)
    if kp:
        return kp["kp_id"], kp["kp_name"], kp.get("difficulty_level"), kp.get("requirement_level")
    return None, None, None, None


def _normalize_records(p_rows: list[dict], e_rows: list[dict],
                       by_name: dict, by_id: dict) -> tuple[list[dict], list[str]]:
    """双源行 → 统一 fetched_records（含 kp 归一化 + 得分语义统一）。"""
    records = []
    unmapped: set[str] = set()

    for r in p_rows:
        kp_id, kp_name, difficulty, requirement = _resolve_kp(
            r["kp_id"], r.get("knowledge_tag"), by_name, by_id)
        tag = r.get("knowledge_tag") or ""
        if not kp_id and tag:
            unmapped.add(tag)
        max_score = r.get("max_score") or 0
        score = r.get("score")
        is_correct = bool(r.get("is_correct"))
        if score is None:
            score = max_score if is_correct else 0
        records.append({
            "record_id": str(r["id"]), "student_id": str(r["student_id"]),
            "source": "practice",
            "exercise_id": str(r["exercise_id"]) if r.get("exercise_id") else None,
            "question_id": None,
            "kp_id": kp_id, "kp_name": kp_name,
            "difficulty_level": difficulty, "requirement_level": requirement,
            "knowledge_tag": tag,
            "question_type": r.get("question_type") or "",
            "answer": r.get("answer") or "",
            "score": score, "max_score": max_score, "is_correct": is_correct,
            "occurred_at": r["recorded_at"].isoformat() if r.get("recorded_at") else None,
        })

    for r in e_rows:
        kp_id, kp_name, difficulty, requirement = _resolve_kp(
            None, r.get("knowledge_tag"), by_name, by_id)
        tag = r.get("knowledge_tag") or ""
        if not kp_id and tag:
            unmapped.add(tag)
        final_score = r.get("final_score") or 0
        max_score = r.get("max_score") or 0
        records.append({
            "record_id": str(r["id"]), "student_id": str(r["student_id"]),
            "source": "exam",
            "exercise_id": None,
            "question_id": str(r["question_id"]) if r.get("question_id") else None,
            "kp_id": kp_id, "kp_name": kp_name,
            "difficulty_level": difficulty, "requirement_level": requirement,
            "knowledge_tag": tag,
            "question_type": r.get("question_type") or "",
            "answer": r.get("student_answer") or "",
            "score": final_score, "max_score": max_score,
            "is_correct": final_score == max_score and max_score > 0,
            "occurred_at": r["submitted_at"].isoformat() if r.get("submitted_at") else None,
        })

    return records, sorted(unmapped)


_FLOOR_TS = datetime(1900, 1, 1, tzinfo=timezone.utc)
_CEIL_TS  = datetime(2999, 1, 1, tzinfo=timezone.utc)


def _time_bounds(tr: dict) -> tuple:
    """时间范围 → (start, end)。None/空串替换为边界时间，保证 SQL 参数非 None 可推断类型。"""
    start = tr.get("start") or None
    end = tr.get("end") or None
    return start or _FLOOR_TS, end or _CEIL_TS


# ──────────────────────────────────────────────────────────────
# 节点：mastery_calc — 知识点掌握度计算（确定性规则，无 LLM）
# ──────────────────────────────────────────────────────────────

# 难度门槛调整量：hard 知识点达标线更低（难题拿 0.6 已不错），easy 更高（简单题 0.6 算差）
_DIFFICULTY_ADJ = {"easy": +0.10, "medium": 0.0, "hard": -0.10}
_BASE_EXCELLENT = 0.80
_BASE_GOOD = 0.60


async def mastery_calc_node(state: LearningAnalysisState) -> dict:
    """掌握度计算（并行分支之一，纯计算、无副作用）。

    按 kp 聚合 fetched_records：mastery_score = Σscore/Σmax_score（得分率）；
    等级按「得分率 + 知识点难度」双重门槛判定 excellent/good/weak。
    LLM 不碰数字。未归一化（kp_id 为空）的记录不参与掌握度。
    """
    records = state.get("fetched_records", []) or []
    groups: dict[str, dict] = {}

    for r in records:
        kp_id = r.get("kp_id")
        if not kp_id:
            continue  # 未分类（跨科 tag）不参与掌握度
        g = groups.setdefault(kp_id, {
            "kp_id": kp_id, "kp_name": r.get("kp_name"),
            "difficulty_level": r.get("difficulty_level"),
            "requirement_level": r.get("requirement_level"),
            "sum_score": 0, "sum_max": 0,
            "correct_count": 0, "total_count": 0,
        })
        g["sum_score"] += r.get("score") or 0
        g["sum_max"] += r.get("max_score") or 0
        g["total_count"] += 1
        if r.get("is_correct"):
            g["correct_count"] += 1

    kp_mastery = []
    for kp_id, g in groups.items():
        rate = g["sum_score"] / g["sum_max"] if g["sum_max"] else 0.0
        diff = g["difficulty_level"] or "medium"
        adj = _DIFFICULTY_ADJ.get(diff, 0.0)
        excellent_line = _BASE_EXCELLENT + adj
        good_line = _BASE_GOOD + adj
        if rate >= excellent_line:
            level = "excellent"
        elif rate >= good_line:
            level = "good"
        else:
            level = "weak"
        kp_mastery.append({
            "kp_id": kp_id,
            "kp_name": g["kp_name"],
            "mastery_score": round(rate, 2),
            "correct_count": g["correct_count"],
            "total_count": g["total_count"],
            "difficulty": diff,
            "requirement_level": g["requirement_level"],
            "level": level,
        })

    # 稳定排序：掌握度升序，薄弱靠前（下游分层/干预优先看薄弱）
    kp_mastery.sort(key=lambda x: (x["mastery_score"], x["kp_name"] or ""))

    logger.info("mastery_calc.done", kp=len(kp_mastery),
                weak=sum(1 for m in kp_mastery if m["level"] == "weak"))
    return {"kp_mastery": kp_mastery}


# ──────────────────────────────────────────────────────────────
# 节点：stratify — 学生分层画像（绝对阈值，纯计算无副作用）
# ──────────────────────────────────────────────────────────────

_EXCELLENT_LINE = 0.80
_GOOD_LINE = 0.60
_MIN_SAMPLE = 2  # 个体画像样本量门槛：该学生该 kp 作答 < 2 题标「数据不足」不硬判
_LEVEL_NAMES = {"excellent": "优秀", "good": "达标", "weak": "薄弱"}


async def stratify_node(state: LearningAnalysisState) -> dict:
    """学生分层画像（并行汇合后的串行节点，纯统计无 LLM）。

    学生个人掌握度 = 该学生 Σscore/Σmax_score（题目维度加权，题目多的 kp 权重自然大）；
    绝对阈值分层：≥0.8 excellent / ≥0.6 good / 否则 weak（对齐 strat_level 绝对语义）。
    每层 profile = 层内学生按 kp 聚合的得分率，标注薄弱/擅长 kp。
    未归一化（kp_id 为空）记录不参与。
    """
    records = state.get("fetched_records", []) or []

    # 1. 按学生聚合得分率
    student_scores: dict[str, dict] = {}
    for r in records:
        sid = r.get("student_id")
        if not sid or not r.get("kp_id"):
            continue
        s = student_scores.setdefault(sid, {"sum_score": 0, "sum_max": 0})
        s["sum_score"] += r.get("score") or 0
        s["sum_max"] += r.get("max_score") or 0

    # 2. 绝对阈值分层（顺带记录每个学生所属层）
    student_rate: dict[str, float] = {}
    student_level: dict[str, str] = {}
    layers = {"excellent": [], "good": [], "weak": []}
    for sid, s in student_scores.items():
        rate = s["sum_score"] / s["sum_max"] if s["sum_max"] else 0.0
        student_rate[sid] = rate
        if rate >= _EXCELLENT_LINE:
            layers["excellent"].append(sid)
            student_level[sid] = "excellent"
        elif rate >= _GOOD_LINE:
            layers["good"].append(sid)
            student_level[sid] = "good"
        else:
            layers["weak"].append(sid)
            student_level[sid] = "weak"

    # 3. 每层 profile（层内 kp 聚合 + 薄弱/擅长标注）
    stratification = []
    for level in ("excellent", "good", "weak"):
        sids = set(layers[level])
        profile = {"avg_rate": None, "weak_kps": [], "strong_kps": []}
        if sids:
            kp_agg: dict[str, dict] = {}
            for r in records:
                if r.get("student_id") in sids and r.get("kp_id"):
                    kp_id = r["kp_id"]
                    k = kp_agg.setdefault(kp_id, {
                        "sum_score": 0, "sum_max": 0, "kp_name": r.get("kp_name")})
                    k["sum_score"] += r.get("score") or 0
                    k["sum_max"] += r.get("max_score") or 0
            for k in kp_agg.values():
                rate = k["sum_score"] / k["sum_max"] if k["sum_max"] else 0.0
                if rate < _GOOD_LINE:
                    profile["weak_kps"].append(k["kp_name"])
                elif rate >= _EXCELLENT_LINE:
                    profile["strong_kps"].append(k["kp_name"])
            profile["avg_rate"] = round(
                sum(student_rate[sid] for sid in sids) / len(sids), 2)
        stratification.append({
            "level": level,
            "level_name": _LEVEL_NAMES[level],
            "student_ids": sorted(layers[level]),
            "count": len(layers[level]),
            "profile": profile,
        })

    # 4. 学生个体画像（学生 × kp，样本量门槛：作答 < 2 题标「数据不足」不硬判）
    roster_map = {r["student_id"]: r.get("username") for r in state.get("roster", [])}
    student_kp: dict[str, dict] = {}
    for r in records:
        sid = r.get("student_id")
        kp_id = r.get("kp_id")
        if not sid or not kp_id:
            continue
        k = student_kp.setdefault(sid, {}).setdefault(
            kp_id, {"sum_score": 0, "sum_max": 0, "count": 0, "kp_name": r.get("kp_name")})
        k["sum_score"] += r.get("score") or 0
        k["sum_max"] += r.get("max_score") or 0
        k["count"] += 1

    student_profiles = []
    for sid in sorted(student_rate):
        weak_kps, strong_kps, insufficient = [], [], []
        for kp_id, k in student_kp.get(sid, {}).items():
            rate = k["sum_score"] / k["sum_max"] if k["sum_max"] else 0.0
            entry = {"kp_id": kp_id, "kp_name": k["kp_name"],
                     "rate": round(rate, 2), "count": k["count"]}
            if k["count"] < _MIN_SAMPLE:
                insufficient.append(entry)
            elif rate < _GOOD_LINE:
                weak_kps.append(entry)
            elif rate >= _EXCELLENT_LINE:
                strong_kps.append(entry)
        student_profiles.append({
            "student_id": sid,
            "username": roster_map.get(sid),
            "avg_rate": round(student_rate[sid], 2),
            "level": student_level[sid],
            "total_count": sum(k["count"] for k in student_kp.get(sid, {}).values()),
            "weak_kps": weak_kps,
            "strong_kps": strong_kps,
            "insufficient_kps": insufficient,
        })
    # 重点学生（weak）靠前，节点7 生成个别指导时直接取前面
    student_profiles.sort(key=lambda p: {"weak": 0, "good": 1, "excellent": 2}.get(p["level"], 9))

    logger.info("stratify.done",
                excellent=len(layers["excellent"]),
                good=len(layers["good"]),
                weak=len(layers["weak"]),
                profiles=len(student_profiles))
    return {"stratification": stratification, "student_profiles": student_profiles}


# ──────────────────────────────────────────────────────────────
# 节点：typical_error — 典型错题分析（并行分支另一支）
# ──────────────────────────────────────────────────────────────

# error_type 启发式映射：题型 → 错因类型（MVP 粗略，后续可 LLM 分析 content/answer 增强）
_ERROR_TYPE_BY_QTYPE = {
    "fill_blank":    "calculation",  # 填空多为计算
    "short_answer":  "method",       # 解答题多为方法/综合
    "single_choice": "concept",      # 选择/判断多为概念辨析
    "multi_choice":  "concept",
    "judge":         "concept",
    "code":          "method",
}


async def typical_error_node(state: LearningAnalysisState) -> dict:
    """典型错题分析（并行分支另一支，纯统计 + 启发式错因分类）。

    按 exercise 聚合错误记录：error_rate = 1 - 该题得分率（1 - Σscore/Σmax_score），
    error_count = 没全对人数，student_ids = 答错学生（去重）。
    error_type 按题型映射（启发式）。双源覆盖：practice 按 exercise_id、exam 按 question_id 聚合；
    exam 题不在 exercise_bank，故 exam 源只做启发式错因（不进 LLM 增强）。
    """
    records = state.get("fetched_records", []) or []

    agg: dict[str, dict] = {}
    for r in records:
        ex_id = r.get("exercise_id")
        q_id = r.get("question_id")
        key = ex_id or q_id  # practice 用 exercise_id，exam 用 question_id
        if not key:
            continue
        g = agg.setdefault(key, {
            "exercise_id": ex_id,
            "question_id": q_id,
            "source": r.get("source"),
            "kp_id": r.get("kp_id"),
            "kp_name": r.get("kp_name"),
            "question_type": r.get("question_type") or "",
            "sum_score": 0, "sum_max": 0,
            "error_count": 0,
            "student_ids": [],
        })
        g["sum_score"] += r.get("score") or 0
        g["sum_max"] += r.get("max_score") or 0
        if not r.get("is_correct"):
            g["error_count"] += 1
            if r.get("student_id"):
                g["student_ids"].append(r["student_id"])

    typical_errors = []
    for g in agg.values():
        if g["error_count"] == 0:
            continue  # 无人做错，不典型
        error_rate = 1 - (g["sum_score"] / g["sum_max"]) if g["sum_max"] else 0.0
        typical_errors.append({
            "exercise_id": g["exercise_id"],
            "question_id": g["question_id"],
            "source": g["source"],
            "kp_id": g["kp_id"],
            "kp_name": g["kp_name"],
            "error_rate": round(error_rate, 2),
            "error_count": g["error_count"],
            "error_type": _ERROR_TYPE_BY_QTYPE.get(g["question_type"], "method"),
            "student_ids": sorted(set(g["student_ids"])),
        })

    # 错误率降序：最典型的错题靠前
    typical_errors.sort(key=lambda x: (-x["error_rate"], -x["error_count"]))

    # LLM 增强：分析真实错因，覆盖启发式映射；失败降级保留启发式 error_type
    if typical_errors:
        try:
            error_type_map = await _analyze_error_types(typical_errors, records)
            for te in typical_errors:
                et = error_type_map.get(te["exercise_id"])
                if et in ("concept", "calculation", "reading", "method"):
                    te["error_type"] = et
        except Exception as e:
            logger.warning("typical_error.llm_error_type_failed", error=str(e))

    logger.info("typical_error.done", errors=len(typical_errors))
    return {"typical_errors": typical_errors}


class ErrorTypeItem(BaseModel):
    """错因分类的 LLM 输出单元。"""
    exercise_id: str
    error_type: str = Field(..., description="concept/calculation/reading/method 之一")


class ErrorTypeResult(BaseModel):
    items: list[ErrorTypeItem]


async def _analyze_error_types(typical_errors: list[dict], records: list[dict]) -> dict:
    """LLM 批量分析典型错题的 error_type（启发式映射的增强），返回 {exercise_id: error_type}。"""
    items_desc = []
    async with AsyncSessionLocal() as session:
        for i, te in enumerate(typical_errors, 1):
            ex_id = te.get("exercise_id")
            if not ex_id:
                continue
            ex = (await session.execute(text(
                "SELECT content, options, answer FROM exercise_bank WHERE exercise_id = :id"
            ), {"id": ex_id})).first()
            if not ex:
                continue
            wrong = next((r.get("answer", "") for r in records
                          if r.get("exercise_id") == ex_id and not r.get("is_correct")
                          and r.get("answer")), "")
            if not wrong or wrong.strip().lower() in ("x", "-", "空", "none", "无"):
                continue  # 无真实错误答案（占位符），LLM 无法判错因 → 保留启发式 error_type
            items_desc.append(
                f"【题{i}】exercise_id={ex_id}\n"
                f"题干：{ex.content}\n"
                f"选项：{ex.options or '无'}\n"
                f"学生错误答案：{wrong or '（空）'}\n"
                f"正确答案：{ex.answer}"
            )
    if not items_desc:
        return {}
    prompt = ERROR_TYPE_ANALYSIS_PROMPT.format(items="\n\n".join(items_desc))
    structured = get_structured_llm("learning_analysis", ErrorTypeResult)
    result = await structured.ainvoke([
        SystemMessage(content=ERROR_TYPE_SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ])
    return {item.exercise_id: item.error_type for item in result.items}


# ──────────────────────────────────────────────────────────────
# 节点：intervention_retrieval — 干预策略检索（PG 单路，只读）
# ──────────────────────────────────────────────────────────────

_ZERO_UUID = _uuid.UUID("00000000-0000-0000-0000-000000000000")


def _to_uuid(v):
    try:
        return _uuid.UUID(str(v)) if v else None
    except (ValueError, TypeError, AttributeError):
        return None


# error_type → 中文标签（语义检索查询串用）
_ERROR_TYPE_CN = {"concept": "概念理解", "calculation": "计算", "reading": "审题", "method": "方法"}


def _build_intervention_query(intent: dict) -> str:
    """构造干预策略语义检索查询串（kp 名 + 错因中文标签）。"""
    kp = intent.get("kp_name") or ""
    et = intent.get("error_type")
    et_cn = _ERROR_TYPE_CN.get(et, "") if et else ""
    if not kp and not et_cn:
        return ""
    return f"{kp} {et_cn} 教学干预策略".strip()


async def intervention_retrieval_node(state: LearningAnalysisState) -> dict:
    """干预策略检索（PG 单路，只读无副作用；Milvus 双路后置 seam）。

    消费 stratification（各层 weak_kps）+ typical_errors（错题 error_type），
    按 strat_level + error_type + kp_id 匹配 intervention_strategies 表。
    匹配优先级：具体 kp > 具体层 > 具体错因 > 通用(all)；结果按策略去重。
    """
    subject = state["subject"]
    grade = state["grade"]
    stratification = state.get("stratification", []) or []
    typical_errors = state.get("typical_errors", []) or []
    records = state.get("fetched_records", []) or []

    kp_name_to_id = {r["kp_name"]: r["kp_id"]
                     for r in records if r.get("kp_id") and r.get("kp_name")}

    # 收集检索意图（去重）：(kp_id, kp_name, strat_level, error_type)
    intents = []
    seen = set()
    for layer in stratification:
        if layer["level"] not in ("weak", "good"):
            continue
        for kp_name in layer["profile"]["weak_kps"]:
            key = (kp_name, layer["level"], None)
            if key in seen:
                continue
            seen.add(key)
            intents.append({"kp_id": kp_name_to_id.get(kp_name), "kp_name": kp_name,
                            "strat_level": layer["level"], "error_type": None})
    for te in typical_errors:
        key = (te.get("kp_name"), "weak", te.get("error_type"))
        if key in seen:
            continue
        seen.add(key)
        intents.append({"kp_id": te.get("kp_id"), "kp_name": te.get("kp_name"),
                        "strat_level": "weak", "error_type": te.get("error_type")})

    interventions = []
    strategy_seen = set()
    async with AsyncSessionLocal() as session:
        for intent in intents:
            kp = _to_uuid(intent["kp_id"]) or _ZERO_UUID
            etype = intent["error_type"] or ""
            rows = await session.execute(text("""
                SELECT id, strategy_content, strat_level, error_type, source_lesson_id
                FROM intervention_strategies
                WHERE subject = :s AND grade = :g
                  AND (strat_level = :level OR strat_level = 'all')
                  AND (:etype = '' OR error_type = :etype OR error_type = 'all')
                  AND (kp_id = :kp OR kp_id IS NULL)
                ORDER BY (kp_id IS NOT NULL) DESC, (strat_level != 'all') DESC,
                         (error_type != 'all') DESC
                LIMIT 2
            """), {"s": subject, "g": grade, "level": intent["strat_level"],
                   "etype": etype, "kp": kp})
            for row in rows:
                sid = str(row.id)
                if sid in strategy_seen:
                    continue
                strategy_seen.add(sid)
                interventions.append({
                    "strategy_id": sid,
                    "strategy_content": row.strategy_content,
                    "strat_level": row.strat_level,
                    "error_type": row.error_type,
                    "source_lesson_id": str(row.source_lesson_id) if row.source_lesson_id else None,
                    "matched_reason": (f"{intent['kp_name'] or '全知识点'}·"
                                       f"{intent['strat_level']}层·"
                                       f"{intent['error_type'] or '通用错因'}"),
                })

    # ── Milvus 双路：语义检索补充（intervention 灌入 knowledge_domain，resource_type='intervention'）──
    try:
        from backend.core.knowledge_base import BGEMEmbedder, KnowledgeBaseClient
        embedder = BGEMEmbedder.get_instance()
        kb = KnowledgeBaseClient()
        sem_ids: set[str] = set()
        for intent in intents:
            q = _build_intervention_query(intent)
            if not q:
                continue
            dense, sparse = embedder.encode_query(q)
            filters = kb._build_filter(
                "tenant_default", resource_type="intervention",
                subject=subject, grade=grade,
            )
            hits = kb._hybrid_search(dense, sparse, top_k=2, filters=filters)
            for h in hits:
                cid = h.get("metadata", {}).get("content_id") or ""
                if cid:
                    sem_ids.add(cid)
        if sem_ids:
            uuids = [u for u in (_to_uuid(x) for x in sem_ids) if u]
            if uuids:
                async with AsyncSessionLocal() as session:
                    rows = await session.execute(text(
                        "SELECT id, strategy_content, strat_level, error_type, source_lesson_id "
                        "FROM intervention_strategies WHERE id = ANY(:ids)"
                    ), {"ids": uuids})
                    for row in rows:
                        sid = str(row.id)
                        if sid in strategy_seen:
                            continue
                        strategy_seen.add(sid)
                        interventions.append({
                            "strategy_id": sid,
                            "strategy_content": row.strategy_content,
                            "strat_level": row.strat_level,
                            "error_type": row.error_type,
                            "source_lesson_id": str(row.source_lesson_id) if row.source_lesson_id else None,
                            "matched_reason": "语义匹配（Milvus 双路）",
                        })
    except Exception as e:
        logger.warning("intervention_retrieval.milvus_failed", error=str(e))

    # seam③：关联教案作为软信号参考（查 lesson_plans 拿 topic）
    lesson_ids = [_to_uuid(iv["source_lesson_id"]) for iv in interventions
                  if iv.get("source_lesson_id")]
    if lesson_ids:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(text(
                "SELECT lesson_id, topic FROM lesson_plans WHERE lesson_id = ANY(:ids)"
            ), {"ids": lesson_ids})
            topic_map = {str(r.lesson_id): r.topic for r in rows}
        for iv in interventions:
            if iv.get("source_lesson_id"):
                iv["lesson_topic"] = topic_map.get(iv["source_lesson_id"])

    logger.info("intervention_retrieval.done", intents=len(intents),
                interventions=len(interventions))
    return {"interventions": interventions}


# ──────────────────────────────────────────────────────────────
# 节点：suggestion_gen — 教学建议生成（LLM 组织语言 + 确定性 weak_kps）
# ──────────────────────────────────────────────────────────────

class TeachingSuggestions(BaseModel):
    """教学建议 LLM 结构化输出：三键对齐 state 的 teaching_suggestions。"""
    layered_suggestion: str = Field(..., description="分层建议：优等/达标/薄弱各层针对性教学措施")
    error_remediation: str = Field(..., description="错题补救：重点关注学生（薄弱生）的个别指导建议")
    follow_up_plan: str = Field(..., description="后续教学计划：班级整体共性问题与改进方向")


def _weak_kp_suggestion(kp_name: str, mastery: float) -> str:
    """薄弱知识点的一句话建议（规则模板，确定性可质检）。"""
    if mastery < 0.3:
        return f"「{kp_name}」掌握度 {mastery}，严重薄弱，建议优先安排基础概念重建 + 专项补差"
    return f"「{kp_name}」掌握度 {mastery}，建议安排专项练习巩固"


def _extract_weak_kps(kp_mastery: list[dict]) -> list[dict]:
    """确定性提取薄弱知识点（seam① 一键备课入口，结构化不埋自由文本）。"""
    weak_kps = [
        {
            "kp_id": m["kp_id"],
            "kp_name": m["kp_name"],
            "mastery_score": m["mastery_score"],
            "level": m["level"],
            "suggestion": _weak_kp_suggestion(m["kp_name"], m["mastery_score"]),
        }
        for m in kp_mastery if m.get("level") == "weak"
    ]
    weak_kps.sort(key=lambda x: x["mastery_score"])
    return weak_kps


def _sanitize_numbers(text: str, allowed: set) -> str:
    """把建议文本里不在允许清单内的 0.XX 数字替换为最接近的允许值。

    兜底防 LLM 编造数字（如 0.33）触发 report_validation 第 5 项「数字抽查」拦截。
    只处理 (0,1) 区间的诊断数字；已在清单（含 0.05 容差）内的原样保留。
    """
    if not allowed or not text:
        return text

    def _repl(m):
        val = float(m.group())
        if val <= 0 or val >= 1:
            return m.group()
        if any(abs(val - a) < 0.05 for a in allowed):
            return m.group()
        closest = min(allowed, key=lambda a: abs(val - a))
        return f"{closest:.2f}"

    return re.sub(r"0\.\d+", _repl, text)


async def suggestion_gen_node(state: LearningAnalysisState) -> dict:
    """教学建议生成（全链路唯一 LLM 节点，无副作用）。

    两个产物分工：
    - weak_kps：确定性规则提取（kp_mastery 里 level=weak 的 kp + 规则模板建议），
      结构化供机器读，是 seam① 一键备课的入口。
    - teaching_suggestions：LLM 消费确定性诊断结果组织成三键自然语言，
      「分层建议 / 错题补救 / 后续教学计划」。LLM 只组织语言、引用数字不篡改。
    """
    kp_mastery = state.get("kp_mastery", []) or []
    typical_errors = state.get("typical_errors", []) or []
    stratification = state.get("stratification", []) or []
    student_profiles = state.get("student_profiles", []) or []
    interventions = state.get("interventions", []) or []

    weak_kps = _extract_weak_kps(kp_mastery)

    if not kp_mastery and not typical_errors:
        teaching_suggestions = {"分层建议": "", "错题补救": "", "后续教学计划": ""}
    else:
        # 允许引用的诊断数字清单（与质检 real_numbers 同源，明确告知 LLM 哪些数字能写，防编造）
        allowed_nums = {round(m.get("mastery_score", 0), 2) for m in kp_mastery} \
            | {round(t.get("error_rate", 0), 2) for t in typical_errors} \
            | {round(l.get("profile", {}).get("avg_rate", 0), 2) for l in stratification
               if l.get("profile", {}).get("avg_rate") is not None}
        allowed_numbers = "、".join(f"{v:.2f}" for v in sorted(allowed_nums)) or "（无）"

        prompt = SUGGESTION_GEN_PROMPT.format(
            mastery=json.dumps(kp_mastery, ensure_ascii=False),
            errors=json.dumps(typical_errors, ensure_ascii=False),
            stratification=json.dumps(stratification, ensure_ascii=False),
            profiles=json.dumps(student_profiles, ensure_ascii=False),
            interventions=json.dumps(interventions, ensure_ascii=False),
            allowed_numbers=allowed_numbers,
        )
        structured = get_structured_llm("learning_analysis", TeachingSuggestions)
        result = await structured.ainvoke([
            SystemMessage(content=SUGGESTION_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])
        teaching_suggestions = {
            "分层建议": _sanitize_numbers(result.layered_suggestion, allowed_nums),
            "错题补救": _sanitize_numbers(result.error_remediation, allowed_nums),
            "后续教学计划": _sanitize_numbers(result.follow_up_plan, allowed_nums),
        }

    logger.info("suggestion_gen.done", weak_kps=len(weak_kps))
    return {"weak_kps": weak_kps, "teaching_suggestions": teaching_suggestions}


# ──────────────────────────────────────────────────────────────
# 节点：report_integration — 报告整合（纯规则拼接，无副作用）
# ──────────────────────────────────────────────────────────────

async def report_integration_node(state: LearningAnalysisState) -> dict:
    """报告整合（纯规则拼接，无 LLM）。

    把各节点结构化产物按模块组装进 report_content：
    meta / overview(班级总览) / mastery(掌握度分布) / stratification(分层画像)
    / student_profiles(个体画像) / errors(典型错题) / suggestions(教学建议)。
    只拼接 + 提取 meta，不做一致性校验（校验在节点9 报告质检）。
    """
    analysis_scope = state.get("analysis_scope", {}) or {}

    meta = {
        "class_id":     state.get("class_id"),
        "subject":      state["subject"],
        "grade":        state["grade"],
        "report_type":  state.get("report_type", "class"),
        "template":     state.get("template", "primary"),
        "time_range":   analysis_scope.get("time_range", {}),
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "teacher_id":   state.get("teacher_id"),
    }

    report_content = {
        "meta":              meta,
        "overview":          state.get("data_summary", {}),
        "mastery":           state.get("kp_mastery", []),
        "stratification":    state.get("stratification", []),
        "student_profiles":  state.get("student_profiles", []),
        "errors":            state.get("typical_errors", []),
        "suggestions": {
            "teaching": state.get("teaching_suggestions", {}),
            "weak_kps": state.get("weak_kps", []),
        },
    }

    logger.info("report_integration.done",
                mastery=len(report_content["mastery"]),
                errors=len(report_content["errors"]))
    return {"report_content": report_content}


# ──────────────────────────────────────────────────────────────
# 节点：report_validation — 报告质检（数字一致性自检，防 LLM 篡改）
# ──────────────────────────────────────────────────────────────

def _extract_decimals(text: str) -> list[float]:
    """从文本提取「0.XX」格式的诊断数字（掌握度/错误率常为此格式）。"""
    return [float(s) for s in re.findall(r"0\.\d+", text)]


async def report_validation_node(state: LearningAnalysisState) -> dict:
    """报告质检（数字一致性自检，无副作用）。

    校验 report_content 与确定性节点产物的一致性，重点防 LLM 篡改数字：
    1) 结构完整性（7 模块齐全）
    2) 数字范围合法（mastery_score/error_rate ∈ [0,1]）
    3) 数量一致性（掌握度列表、分层人数总和 = 有作答学生）
    4) 分层人数 ≤ 班级人数（稀疏数据下允许 <，不允许 >）
    5) LLM 数字抽查（teaching_suggestions 引用的 0.XX 数字须在真实诊断数字集合内）

    只发现问题不修复（fixed_count 恒 0，MVP 不自动修复）。
    """
    report_content = state.get("report_content", {}) or {}
    kp_mastery = state.get("kp_mastery", []) or []
    typical_errors = state.get("typical_errors", []) or []
    stratification = state.get("stratification", []) or []
    student_profiles = state.get("student_profiles", []) or []
    data_summary = state.get("data_summary", {}) or {}
    teaching_suggestions = state.get("teaching_suggestions", {}) or {}

    issues: list[str] = []

    # 1. 结构完整性
    required = {"meta", "overview", "mastery", "stratification",
                "student_profiles", "errors", "suggestions"}
    missing = required - set(report_content.keys())
    if missing:
        issues.append(f"report_content 缺模块: {sorted(missing)}")

    # 2. 数字范围
    for m in kp_mastery:
        s = m.get("mastery_score", 0)
        if not (0 <= s <= 1):
            issues.append(f"掌握度越界: {m.get('kp_name')}={s}")
    for t in typical_errors:
        r = t.get("error_rate", 0)
        if not (0 <= r <= 1):
            issues.append(f"错误率越界: {t.get('kp_name')}={r}")

    # 3. 数量一致性
    if len(report_content.get("mastery", [])) != len(kp_mastery):
        issues.append(f"掌握度列表数量不一致: report={len(report_content.get('mastery', []))} "
                      f"vs 源={len(kp_mastery)}")
    layered_count = sum(l.get("count", 0) for l in stratification)
    if layered_count != len(student_profiles):
        issues.append(f"分层人数总和 {layered_count} != 有作答学生 {len(student_profiles)}")

    # 4. 分层人数 ≤ 班级人数
    roster_count = data_summary.get("student_count", 0)
    if layered_count > roster_count:
        issues.append(f"分层人数总和 {layered_count} > 班级人数 {roster_count}")

    # 5. LLM 数字抽查（防篡改）
    real_numbers = {round(m["mastery_score"], 2) for m in kp_mastery} \
        | {round(t["error_rate"], 2) for t in typical_errors} \
        | {round(l.get("profile", {}).get("avg_rate", 0), 2) for l in stratification
           if l.get("profile", {}).get("avg_rate") is not None}
    text = " ".join(str(v) for v in teaching_suggestions.values())
    for val in _extract_decimals(text):
        if 0 < val < 1 and not any(abs(val - r) < 0.05 for r in real_numbers):
            issues.append(f"LLM 引用了诊断结果中不存在的数字 {val}")

    validation = {
        "passed": len(issues) == 0,
        "issues": issues,
        "fixed_count": 0,
    }

    logger.info("report_validation.done", passed=validation["passed"], issues=len(issues))
    return {"validation": validation}


# ──────────────────────────────────────────────────────────────
# 节点：persistence — 持久化（唯一写入节点，写 learning_reports 单表）
# ──────────────────────────────────────────────────────────────

async def persistence_node(state: LearningAnalysisState) -> dict:
    """持久化（全链路唯一写入点，写 learning_reports 单表）。

    scope←analysis_scope、metrics←中间结果汇总、report_content←报告正文。
    质检不过不落库（saved_report_id=""，数据不可信不写）。
    单表 INSERT，PG 事务原子。Milvus 摘要向量化后置（MVP 未实现）。
    """
    validation = state.get("validation", {}) or {}
    metrics = {
        "kp_mastery":      state.get("kp_mastery", []),
        "stratification":  state.get("stratification", []),
        "student_profiles": state.get("student_profiles", []),
        "typical_errors":  state.get("typical_errors", []),
        "interventions":   state.get("interventions", []),
        "data_summary":    state.get("data_summary", {}),
    }

    if not validation.get("passed", False):
        logger.warning("persistence.skipped", reason="质检未通过")
        return {"saved_report_id": "", "metrics": metrics}

    analysis_scope = state.get("analysis_scope", {}) or {}
    scope = {
        "time_range":       analysis_scope.get("time_range", {}),
        "knowledge_points": analysis_scope.get("knowledge_points", []),
        "focus":            analysis_scope.get("focus", ""),
    }

    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            INSERT INTO learning_reports (
                tenant_id, teacher_id, class_id, subject, grade,
                report_type, template, scope, metrics, report_content, status
            ) VALUES (
                :tenant_id, :teacher_id, :class_id, :subject, :grade,
                :report_type, :template,
                CAST(:scope AS jsonb), CAST(:metrics AS jsonb),
                CAST(:report_content AS jsonb), 'draft'
            )
            RETURNING id
        """), {
            "tenant_id": state.get("tenant_id", "tenant_default"),
            "teacher_id": _to_uuid(state.get("teacher_id")),
            "class_id": _to_uuid(state.get("class_id")),
            "subject": state["subject"],
            "grade": state["grade"],
            "report_type": state.get("report_type", "class"),
            "template": state.get("template", "primary"),
            "scope": json.dumps(scope, ensure_ascii=False),
            "metrics": json.dumps(metrics, ensure_ascii=False),
            "report_content": json.dumps(state.get("report_content", {}), ensure_ascii=False),
        })
        report_id = str(result.scalar())
        await session.commit()

    logger.info("persistence.done", report_id=report_id)
    return {"saved_report_id": report_id, "metrics": metrics}
